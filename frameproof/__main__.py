"""CLI.

Три команды разделены НАМЕРЕННО, и это не вкусовщина:

  index   строит индекс, печатает покрытие — ни одной картинки
  search  ищет по речи и по тексту с экрана — ни одной картинки
  frames  отдаёт изображения — единственная команда, которая это делает

Если бы поиск мог вернуть картинки, вся экономия исчезала бы на первом же запросе:
агент звал бы «поиск» и получал бы гигабайты пикселей вместо строчек текста.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .util import parse_tc, slugify, tc_short


def _work_dir(target: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    base = os.environ.get("FRAMEPROOF_HOME") or os.path.join(
        os.path.expanduser("~"), ".frameproof"
    )
    name = os.path.splitext(os.path.basename(target))[0] if os.path.exists(target) else target
    for junk in ("https://", "http://", "www.", "youtube.com/watch?v=", "youtu.be/"):
        name = name.replace(junk, "")
    return os.path.join(base, slugify(name) or "video")


def cmd_index(args: argparse.Namespace) -> int:
    from .analyze import analyze
    from .extract import extract
    from .index import write
    from .probe import probe
    from .report import render
    from .select import select_frames

    out_dir = _work_dir(args.target, args.out)
    os.makedirs(out_dir, exist_ok=True)

    title, source_url, transcript = args.target, None, None
    video_path = args.target
    audio_path: str | None = None

    if not os.path.exists(args.target):
        from .fetch import fetch

        source_url = args.target
        print(f"качаю: {args.target}", file=sys.stderr)
        got = fetch(args.target, out_dir, max_height=args.max_height)
        video_path, title, audio_path = got.video_path, got.title, got.audio_path
        if got.subtitle_path:
            from .transcribe import from_subtitles

            transcript = from_subtitles(
                got.subtitle_path, auto=got.subtitle_auto, lang=got.subtitle_lang
            )
            kind = "авто" if got.subtitle_auto else "ручные"
            print(
                f"субтитры: {kind}, {got.subtitle_lang}, "
                f"{len(transcript.segments)} реплик",
                file=sys.stderr,
            )

    info = probe(video_path)
    print(
        f"видео: {info.width}x{info.height} {info.fps:.2f} к/с "
        f"{tc_short(info.duration)}{' VFR' if info.vfr else ''}",
        file=sys.stderr,
    )

    if transcript is None and not args.no_transcribe:
        transcript = _local_transcript(audio_path or video_path, out_dir, args.lang)
    if transcript is None:
        print(
            "⚠ транскрипта НЕТ: субтитров у ролика не нашлось, локальная расшифровка "
            "не отработала. Поиск будет только по тексту с экрана (--ocr).",
            file=sys.stderr,
        )

    print("анализирую изменения экрана...", file=sys.stderr)
    sig = analyze(info)
    sel = select_frames(
        sig, info.duration, max_gap=args.max_gap, cap=args.max_frames
    )
    print(f"извлекаю {len(sel.picks)} кадров...", file=sys.stderr)
    frames = extract(info, sel.picks, os.path.join(out_dir, "frames"), width=args.width)

    if args.ocr:
        from . import ocr as ocr_mod

        if ocr_mod.available():
            print("распознаю текст на кадрах...", file=sys.stderr)

    index = write(
        out_dir,
        info=info,
        selection=sel,
        frames=frames,
        transcript=transcript,
        source_url=source_url,
        title=title,
    )

    if args.ocr:
        from . import ocr as ocr_mod

        hits = ocr_mod.annotate_index(out_dir)
        if hits:
            print(f"текст найден на {hits} кадрах — теперь экран грепается", file=sys.stderr)
        elif not ocr_mod.available():
            print("OCR пропущен: нет swiftc (нужны Xcode Command Line Tools)", file=sys.stderr)

    print()
    print(render(sel, title=title[:60], frame_w=frames[0].width if frames else 0,
                 frame_h=frames[0].height if frames else 0))
    print()
    print(f"индекс: {out_dir}")
    print(f"дальше:  frameproof search \"<запрос>\" --out {out_dir}")
    print(f"         frameproof frames --at 4:12 --out {out_dir}")
    return 0 if index["coverage"]["complete"] else 0


def _local_transcript(source_path: str, out_dir: str, lang: str | None):
    """Расшифровка локально. О любом провале сообщаем вслух.

    Тихий возврат None здесь once уже стоил пустого индекса: скачивался video-only
    поток без звука, ffmpeg честно не находил аудиодорожку, и пользователь получал
    ноль реплик без единого предупреждения.
    """
    from .transcribe import transcribe_audio
    from .util import run, which

    if not source_path or not os.path.exists(source_path):
        print("расшифровка пропущена: нет файла со звуком", file=sys.stderr)
        return None

    audio = os.path.join(out_dir, "audio16k.wav")
    if not os.path.exists(audio):
        try:
            run([which("ffmpeg"), "-v", "error", "-y", "-i", source_path,
                 "-vn", "-ac", "1", "-ar", "16000", audio])
        except Exception as exc:
            print(f"не смог выделить звук: {exc}", file=sys.stderr)
            return None
    if not os.path.exists(audio) or os.path.getsize(audio) < 1024:
        print("расшифровка пропущена: в файле нет звуковой дорожки", file=sys.stderr)
        return None
    print("расшифровываю локально (ключи не нужны)...", file=sys.stderr)
    try:
        return transcribe_audio(audio, lang=lang)
    except Exception as exc:
        print(f"расшифровка не удалась: {exc}", file=sys.stderr)
        return None


def cmd_search(args: argparse.Namespace) -> int:
    from .index import load_index, search

    out_dir = args.out or _work_dir(args.query, None)
    if not os.path.exists(os.path.join(out_dir, "index.json")):
        print(f"нет индекса в {out_dir}. Сначала: frameproof index <url|файл>", file=sys.stderr)
        return 2
    hits = search(out_dir, args.query, limit=args.limit)
    if not hits:
        print(f"не найдено: {args.query!r}")
        idx = load_index(out_dir)
        if not idx["transcript"]["segment_count"]:
            print("(транскрипта в индексе нет — искать пока не по чему)")
        return 1
    for h in hits:
        line = h.line()
        print(line if len(line) <= 200 else line[:197] + "...")
    print()
    print(f"{len(hits)} совпадений. Ни одной картинки не загружено.")
    print(f"Посмотреть момент: frameproof frames --at {tc_short(hits[0].t)} --out {out_dir}")
    return 0


def cmd_frames(args: argparse.Namespace) -> int:
    from .index import frames_by_ids, frames_near, load_index

    out_dir = args.out
    if not out_dir or not os.path.exists(os.path.join(out_dir, "index.json")):
        print("нужен --out с готовым индексом", file=sys.stderr)
        return 2
    idx = load_index(out_dir)

    if args.ids:
        rows = frames_by_ids(out_dir, [s.strip() for s in args.ids.split(",")])
    elif args.at:
        rows = frames_near(out_dir, parse_tc(args.at), count=args.count)
    else:
        print("укажите --at 4:12 или --ids f0043", file=sys.stderr)
        return 2

    if not rows:
        print("кадров не нашлось")
        return 1

    cov = idx["coverage"]
    for r in rows:
        inside = [g for g in cov["gaps"] if g["from"] <= r["t"] <= g["to"]]
        mark = "  ⚠ участок без гарантии покрытия" if inside else ""
        print(f"[{r['tc'].split('.')[0]} / {r['id']}] {os.path.join(out_dir, r['path'])}"
              f"  ({r['est_tokens']} токенов){mark}")
        if r.get("caption"):
            print(f"    уже разобран: {r['caption']}")
    total = sum(r["est_tokens"] for r in rows)
    print()
    print(f"{len(rows)} кадров, примерно {total} визуальных токенов.")
    print("Показывай их модели и цитируй меткой [MM:SS / fNNNN].")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .index import load_index

    out_dir = args.out
    idx = load_index(out_dir)
    cov = idx["coverage"]
    v, f = idx["video"], idx["frames"]
    print(f"ВИДЕО: {v['title'][:70]}")
    print(f"  {tc_short(v['duration_sec'])}, {v['width']}x{v['height']}, {v['fps']} к/с")
    print(f"КАДРЫ: {f['count']}  (переходы {f['caught_changes']}, страховка {f['safety_fills']})")
    print(f"  {f['est_tokens_per_frame']} токенов на кадр")
    if cov["complete"]:
        print(f"ПОКРЫТИЕ: 100 % — максимальный разрыв {cov['actual_max_gap_sec']} с "
              f"при цели {cov['max_gap_target_sec']:.0f} с")
    else:
        print(f"ПОКРЫТИЕ: {cov['ratio'] * 100:.0f} % — участки без кадров:")
        for g in cov["gaps"]:
            print(f"    {g['tc']}   НЕ утверждай, что было на экране здесь")
    t = idx["transcript"]
    print(f"ТРАНСКРИПТ: {t['segment_count']} реплик, источник {t['source']}, язык {t['language']}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    import shutil

    print(f"frameproof {__version__}")
    ok = True
    for tool, why in (("ffmpeg", "разбор видео"), ("ffprobe", "метаданные"),
                      ("yt-dlp", "ссылки (для локальных файлов не нужен)")):
        path = shutil.which(tool)
        print(f"  {'✓' if path else '✗'} {tool:<8} {path or 'НЕ НАЙДЕН'}   — {why}")
        if not path and tool != "yt-dlp":
            ok = False
    try:
        import numpy
        print(f"  ✓ numpy    {numpy.__version__}")
    except ImportError:
        print("  ✗ numpy    НЕ НАЙДЕН — pip install numpy")
        ok = False
    for mod, why in (("mlx_whisper", "быстрая расшифровка на Apple Silicon"),
                     ("whisper", "расшифровка везде"),
                     ("yt_dlp", "загрузка по ссылке")):
        try:
            __import__(mod)
            print(f"  ✓ {mod:<12} {why}")
        except ImportError:
            print(f"  · {mod:<12} нет — {why} недоступна")
    from . import ocr as ocr_mod
    print(f"  {'✓' if ocr_mod.available() else '·'} Apple Vision OCR "
          f"{'доступен' if ocr_mod.available() else 'нет swiftc — OCR пропустится'}")
    print()
    print("готов к работе" if ok else "не хватает обязательного — см. выше")
    return 0 if ok else 1


def cmd_install(args: argparse.Namespace) -> int:
    """Ставит скилл для Claude Code симлинком на канонический каталог."""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skill")
    src = os.path.abspath(src)
    if not os.path.exists(os.path.join(src, "SKILL.md")):
        print(f"не нашёл skill/SKILL.md рядом с пакетом ({src})", file=sys.stderr)
        return 2
    dst_dir = os.path.expanduser("~/.claude/skills")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "frameproof")
    if os.path.islink(dst) or os.path.exists(dst):
        if not args.force:
            print(f"{dst} уже существует. Перезаписать: --force", file=sys.stderr)
            return 1
        if os.path.islink(dst):
            os.unlink(dst)
        else:
            import shutil as sh
            sh.rmtree(dst)
    os.symlink(src, dst)
    print(f"✓ скилл поставлен: {dst} → {src}")
    print("  В Claude Code: «посмотри это видео <ссылка>» или /frameproof")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="frameproof",
        description="Агент смотрит видео без слепых зон и доказывает тайм-кодом, что видел.",
    )
    p.add_argument("--version", action="version", version=f"frameproof {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("index", help="построить индекс (картинок не отдаёт)")
    i.add_argument("target", help="ссылка или путь к файлу")
    i.add_argument("--out", help="папка индекса")
    i.add_argument("--max-gap", type=float, default=15.0,
                   help="гарантия: без кадра не дольше N секунд (по умолчанию 15)")
    i.add_argument("--max-frames", type=int, default=220, help="потолок кадров")
    i.add_argument("--width", type=int, default=1280, help="ширина кадра (1280 = 1196 токенов)")
    i.add_argument("--max-height", type=int, default=1080, help="качество скачиваемого потока")
    i.add_argument("--lang", default=None, help="язык для расшифровки, напр. ru")
    i.add_argument("--ocr", action="store_true", help="распознать текст на кадрах (macOS)")
    i.add_argument("--no-transcribe", action="store_true",
                   help="не расшифровывать, если нет субтитров")
    i.set_defaults(func=cmd_index)

    s = sub.add_parser("search", help="искать по речи и тексту с экрана (картинок не отдаёт)")
    s.add_argument("query")
    s.add_argument("--out", required=True, help="папка индекса")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("frames", help="отдать кадры — единственная команда с картинками")
    f.add_argument("--out", required=True, help="папка индекса")
    f.add_argument("--at", help="момент: 4:12 / 1:04:12 / 252")
    f.add_argument("--ids", help="идентификаторы через запятую: f0043,f0044")
    f.add_argument("--count", type=int, default=1, help="сколько кадров вокруг момента")
    f.set_defaults(func=cmd_frames)

    r = sub.add_parser("report", help="показать покрытие готового индекса")
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_report)

    d = sub.add_parser("doctor", help="проверить окружение")
    d.set_defaults(func=cmd_doctor)

    n = sub.add_parser("install", help="поставить скилл в Claude Code")
    n.add_argument("--force", action="store_true")
    n.set_defaults(func=cmd_install)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nпрервано", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
