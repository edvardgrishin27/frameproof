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
import re
import sys

from . import __version__
from .util import display_path, parse_tc, plural, slugify, tc_short


def _чинить_вывод() -> None:
    """Разрешить консоли печатать то, что мы пишем.

    Отзыв с Windows: `doctor` падал первой же строкой, потому что консоль там
    по умолчанию cp1251, а в таблице стоит «✓». Падала не одна команда, а все:
    кириллица в справке ложится туда же. Лечилось это снаружи, переменной
    PYTHONIOENCODING, но знать о ней человек не обязан.

    `errors="replace"` оставлен намеренно: если кодировку сменить не дали
    (перенаправление в файл, чужая обёртка), лучше показать таблицу с потерянным
    символом, чем не показать ничего.
    """
    for поток in (sys.stdout, sys.stderr):
        try:
            поток.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass  # не текстовый поток или перенаправление: печатаем как есть


_чинить_вывод()


#: На сколько секунд анализ может не дойти до конца ролика, прежде чем это обрыв.
DECODE_SLACK = 5.0


_ССЫЛКА = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|www\.)|^[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/", re.I)


def _похоже_на_ссылку(target: str) -> bool:
    """Ссылка со схемой, с www. или вида `домен.зона/путь`. Путь к файлу — нет."""
    return bool(_ССЫЛКА.match(target.strip()))


def _work_dir(target: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    base = os.environ.get("FRAMEPROOF_HOME") or os.path.join(
        os.path.expanduser("~"), ".frameproof"
    )
    if os.path.exists(target):
        # Имя файла не уникально: `recording.mp4` из двух разных созвонов писались
        # в одну папку, первый индекс молча перезаписывался, а звук второго брался
        # от первого. Короткий хэш полного пути разводит их, а тот же файл
        # по-прежнему попадает в ту же папку.
        stem = os.path.splitext(os.path.basename(target))[0]
        tag = _source_stamp(target, with_mtime=False)[:6]
        return os.path.join(base, f"{slugify(stem, limit=53) or 'video'}-{tag}")
    name = target
    for junk in ("https://", "http://", "www.", "youtube.com/watch?v=", "youtu.be/"):
        name = name.replace(junk, "")
    return os.path.join(base, slugify(name) or "video")


def _source_stamp(path: str, *, with_mtime: bool = True) -> str:
    """Отпечаток файла-источника: полный путь, а для кэша ещё размер и время правки."""
    import hashlib

    real = os.path.realpath(path)
    raw = real
    if with_mtime:
        st = os.stat(real)
        raw = f"{real}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _extract_audio(source_path: str, out_dir: str) -> str | None:
    """Звук 16 кГц моно для расшифровки. Чужой кэш не берём.

    Раньше готовый `audio16k.wav` брался, если просто лежал в папке. Лежать там
    мог звук другого ролика — и расшифровка молча приезжала от него. Теперь рядом
    пишется отпечаток источника, и при несовпадении звук извлекается заново.
    """
    from .util import run, which

    audio = os.path.join(out_dir, "audio16k.wav")
    stamp_file = audio + ".source"
    stamp = _source_stamp(source_path)
    try:
        with open(stamp_file, encoding="utf-8") as fh:
            fresh = fh.read().strip() == stamp and os.path.exists(audio)
    except OSError:
        fresh = False
    if not fresh:
        # Старый звук убираем ДО извлечения: у нового источника может не оказаться
        # звуковой дорожки, ffmpeg тогда выходит, не тронув файл, и чужой звук
        # остался бы лежать под новым отпечатком.
        for stale in (audio, stamp_file):
            if os.path.exists(stale):
                os.remove(stale)
        # check=False: у файла может просто не быть звуковой дорожки, и это
        # нормальная ситуация, а не повод показывать человеку дамп команды.
        # Пишем во временный файл и переименовываем только при успехе: обрезок
        # от оборванного ffmpeg не должен получить отпечаток и стать кэшем.
        part = audio + ".part"
        proc = run([which("ffmpeg"), "-v", "error", "-y", "-i", source_path,
                    "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", part], check=False)
        if proc.returncode == 0 and os.path.exists(part):
            os.replace(part, audio)
            with open(stamp_file, "w", encoding="utf-8") as fh:
                fh.write(stamp)
        elif os.path.exists(part):
            os.remove(part)
    if not os.path.exists(audio) or os.path.getsize(audio) < 1024:
        return None
    return audio


def cmd_index(args: argparse.Namespace) -> int:
    from .analyze import analyze
    from .extract import extract
    from .index import write
    from .probe import probe
    from .report import render
    from .select import keep_extracted, select_frames

    out_dir = _work_dir(args.target, args.out)
    os.makedirs(out_dir, exist_ok=True)

    title, source_url, transcript = args.target, None, None
    video_path = args.target
    audio_path: str | None = None

    # Готовые субтитры бьют любую расшифровку: они уже есть, они точнее и они ничего
    # не стоят. Разбор subtitle-форматов написан давно, но дотянуться до него можно
    # было только через ссылку — для локального файла с лежащим рядом .vtt входа не
    # существовало вовсе. На Windows это отрезало расшифровку целиком: mlx-whisper
    # только под Apple Silicon, а openai-whisper тянет за собой torch.
    if getattr(args, "subs", None):
        from .transcribe import from_subtitles

        if not os.path.exists(args.subs):
            print(f"не нашёл файл субтитров: {args.subs}", file=sys.stderr)
            return 2
        transcript = _nonempty(from_subtitles(args.subs, lang=args.lang), args.subs, args)
        if transcript is not None:
            n = len(transcript.segments)
            print(f"субтитры: {args.subs}, {n} {plural(n, 'реплика', 'реплики', 'реплик')}",
                  file=sys.stderr)

    if not os.path.exists(args.target) and not _похоже_на_ссылку(args.target):
        # Опечатка в пути раньше уезжала «качаться» и падала на «нужен yt-dlp».
        print(f"файл не найден: {display_path(args.target)}. Если это ссылка — "
              "дайте её целиком, с https://", file=sys.stderr)
        return 2
    if not os.path.exists(args.target):
        from .fetch import fetch

        source_url = args.target
        print(f"качаю: {args.target}", file=sys.stderr)
        got = fetch(
            args.target, out_dir, max_height=args.max_height,
            want_audio=not (args.no_transcribe or transcript is not None),
            cookies_from_browser=args.cookies_from_browser,
            proxy=args.proxy,
            js_runtime=args.js_runtime,
        )
        video_path, title, audio_path = got.video_path, got.title, got.audio_path
        if got.subtitle_path and transcript is None:
            from .transcribe import from_subtitles

            transcript = _nonempty(from_subtitles(
                got.subtitle_path, auto=got.subtitle_auto, lang=got.subtitle_lang
            ), got.subtitle_path, args)
            if transcript is not None:
                kind = "авто" if got.subtitle_auto else "ручные"
                n = len(transcript.segments)
                print(f"субтитры: {kind}, {got.subtitle_lang}, "
                      f"{n} {plural(n, 'реплика', 'реплики', 'реплик')}", file=sys.stderr)

    info = probe(video_path)
    print(
        f"видео: {info.width}x{info.height} {info.fps:.2f} к/с "
        f"{tc_short(info.duration)}{' VFR' if info.vfr else ''}",
        file=sys.stderr,
    )

    if transcript is None and not args.no_transcribe:
        transcript = _local_transcript(audio_path or video_path, out_dir, args.lang)
    if transcript is None and not args.no_transcribe:
        print(
            "⚠ транскрипта нет — искать по речи будет нечему. "
            "Текст с экрана всё равно доступен: добавьте --ocr.",
            file=sys.stderr,
        )

    if args.fast:
        print("быстрый проход: только ключевые кадры...", file=sys.stderr)
    else:
        print("анализирую изменения экрана...", file=sys.stderr)
    from .analyze import FastModeUnreliable

    try:
        sig = analyze(info, fast=args.fast)
    except FastModeUnreliable as exc:
        print(f"⚠ быстрый проход ненадёжен ({exc}) — делаю полный", file=sys.stderr)
        sig = analyze(info, fast=False)
    # Декод мог оборваться на битом файле: анализ дошёл до середины и замолчал.
    # Кадры дальше поставит только сетка — об этом надо сказать вслух.
    if not args.fast and len(sig.times) and info.duration - sig.times[-1] > DECODE_SLACK:
        print(f"⚠ декод оборвался на {tc_short(sig.times[-1])} из {tc_short(info.duration)}: "
              "дальше смены экрана не отслеживались, кадры там только по сетке, "
              "а снять их может не выйти — смотри отчёт покрытия", file=sys.stderr)

    cues: list[float] = []
    if transcript is not None and not args.no_cues:
        from .transcribe import pointing_cues

        cues = pointing_cues(transcript)
        if cues:
            print(f"якорей по речи («вот здесь», «смотрите»): {len(cues)}", file=sys.stderr)

    sel = select_frames(
        sig, info.duration, max_gap=args.max_gap, cap=args.max_frames, cues=cues
    )
    print(f"извлекаю {len(sel.picks)} "
          f"{plural(len(sel.picks), 'кадр', 'кадра', 'кадров')}...", file=sys.stderr)
    frames = extract(info, sel.picks, os.path.join(out_dir, "frames"), width=args.width)
    # Дальше — только то, что легло на диск: покрытие, отчёт и OCR-копия считаются
    # по снятым кадрам, а не по плану.
    sel = keep_extracted(sel, [f.t for f in frames])
    if sel.missing:
        print(f"⚠ не удалось снять {sel.missing} "
              f"{plural(sel.missing, 'кадр', 'кадра', 'кадров')} — "
              "их места будут в отчёте как «БЕЗ КАДРА», если разрыв больше цели",
              file=sys.stderr)

    if args.ocr:
        from . import ocr as ocr_mod

        if ocr_mod.available(args.ocr_command):
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
        import shutil
        import tempfile

        from . import ocr as ocr_mod

        # Показываем ужатый кадр, распознаём крупный. Ширина показа выбрана по цене
        # токенов, и распознаванию она только мешает: мелкий интерфейс на 1280 не
        # читается. Крупная копия живёт до конца распознавания и удаляется.
        ocr_width = args.ocr_width if args.ocr_width > 0 else (info.width or args.width)
        images, big_dir = None, None
        if ocr_width > args.width and ocr_mod.available(args.ocr_command):
            big_dir = tempfile.mkdtemp(prefix="frameproof-ocr-")
            print(f"переснимаю кадры в {ocr_width} px для распознавания...", file=sys.stderr)
            try:
                big = extract(info, sel.picks, big_dir, width=ocr_width)
            except RuntimeError as exc:
                big = []
                print(f"крупная копия не снялась ({exc}) — распознаю по кадрам показа",
                      file=sys.stderr)
            if len(big) == len(frames):
                images = [f.path for f in big]
            elif big:
                print(f"крупная копия снялась не целиком ({len(big)} из {len(frames)}) — "
                      "распознаю по кадрам показа, мелкий текст может не прочитаться",
                      file=sys.stderr)
        try:
            hits = ocr_mod.annotate_index(out_dir, images=images, command=args.ocr_command)
        finally:
            if big_dir:
                shutil.rmtree(big_dir, ignore_errors=True)
        if hits:
            print(f"текст найден на {hits} "
                  f"{plural(hits, 'кадре', 'кадрах', 'кадрах')} — теперь экран грепается",
                  file=sys.stderr)
        elif not ocr_mod.available(args.ocr_command):
            where = args.ocr_command or "swiftc (нужны Xcode Command Line Tools)"
            print(f"OCR пропущен: не найден {where}", file=sys.stderr)

    print()
    print(render(sel, title=title[:60], frame_w=frames[0].width if frames else 0,
                 frame_h=frames[0].height if frames else 0))
    print()
    print(f"Индекс: {index['index_id']}   ← поставь эту строку в шапку разбора, "
          "verify по ней отличит чужую сборку")
    print(f"индекс: {display_path(out_dir)}")
    print(f"дальше:  frameproof search \"<запрос>\" --out {display_path(out_dir)}")
    print(f"         frameproof frames --at 4:12 --out {display_path(out_dir)}")
    # Код 0 и при неполном покрытии: индекс собран, а разрывы названы в отчёте.
    return 0


def _nonempty(transcript, source: str, args: argparse.Namespace):
    """Субтитры без единой реплики — это «субтитров нет», а не пустой транскрипт.

    Раньше такой файл засчитывался как транскрипт: расшифровка не запускалась,
    искать по речи было нечему, и об этом никто не говорил.
    """
    if transcript.segments:
        return transcript
    tail = ("" if args.no_transcribe
            else " Пробую расшифровать звук.")
    print(f"⚠ в субтитрах {display_path(source)} не нашлось ни одной реплики — "
          f"считаю, что субтитров нет.{tail}", file=sys.stderr)
    return None


def _local_transcript(source_path: str, out_dir: str, lang: str | None):
    """Расшифровка локально. О любом провале сообщаем вслух.

    Тихий возврат None здесь once уже стоил пустого индекса: скачивался video-only
    поток без звука, ffmpeg честно не находил аудиодорожку, и пользователь получал
    ноль реплик без единого предупреждения.
    """
    from .transcribe import transcribe_audio

    if not source_path or not os.path.exists(source_path):
        print("расшифровка пропущена: нет файла со звуком", file=sys.stderr)
        return None

    audio = _extract_audio(source_path, out_dir)
    if audio is None:
        print("в файле нет звуковой дорожки — расшифровывать нечего", file=sys.stderr)
        return None
    print("расшифровываю локально (ключи не нужны)...", file=sys.stderr)
    try:
        return transcribe_audio(audio, lang=lang)
    except Exception as exc:
        print(f"расшифровка не удалась: {exc}", file=sys.stderr)
        return None


def cmd_search(args: argparse.Namespace) -> int:
    from .index import gaps_near_hits, load_index, search_with_total

    out_dir = args.out
    if not os.path.exists(os.path.join(out_dir, "index.json")):
        print(f"нет индекса в {display_path(out_dir)}. Сначала: frameproof index <url|файл>", file=sys.stderr)
        return 2
    hits, total = search_with_total(out_dir, args.query, limit=args.limit)
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
    if total > len(hits):
        # Выдача идёт по времени: обрезанный хвост — это поздние моменты ролика.
        # Без этой строки агент решал, что тема звучит только в начале.
        print(f"показано {len(hits)} из {total} "
              f"{plural(total, 'совпадения', 'совпадений', 'совпадений')} — первые по времени. "
              f"Остальные: --limit {total}. Ни одной картинки не загружено.")
    else:
        print(f"{len(hits)} {plural(len(hits), 'совпадение', 'совпадения', 'совпадений')}. "
              "Ни одной картинки не загружено.")

    # Оговорка к ответу, а не общая статистика: где искать было НЕ ПО ЧЕМУ.
    # Без неё человек отвечает уверенно, не зная, что рядом с найденным
    # лежит участок без кадров, и там тот же разговор мог продолжиться.
    дыры = gaps_near_hits(load_index(out_dir), hits)
    if дыры["near"]:
        сколько = len(дыры["near"])
        слово = plural(сколько, "участок", "участка", "участков")
        print(f"\n⚠ рядом с найденным {сколько} {слово} без кадров: "
              + ", ".join(g["tc"] for g in дыры["near"][:4])
              + (f" и ещё {сколько - 4}" if сколько > 4 else ""))
        print("  Ответ мог быть и там. Проверьте: frameproof report --out " + display_path(out_dir))
    elif дыры["all"]:
        всего = len(дыры["all"])
        print(f"\nВсего в записи {всего} "
              f"{plural(всего, 'участок', 'участка', 'участков')} без кадров, "
              "но ближе трёх минут к находкам их нет.")

    print(f"Посмотреть момент: frameproof frames --at {tc_short(hits[0].t)} --out {display_path(out_dir)}")
    return 0


def cmd_frames(args: argparse.Namespace) -> int:
    from .index import frames_by_ids, frames_near, load_index

    out_dir = args.out
    if not out_dir or not os.path.exists(os.path.join(out_dir, "index.json")):
        print("нужен --out с готовым индексом", file=sys.stderr)
        return 2
    idx = load_index(out_dir)

    if args.sheet:
        return _frames_sheet(args, out_dir, idx)
    if args.exact:
        if not args.at:
            print("--exact работает вместе с --at: frames --at 4:12 --exact", file=sys.stderr)
            return 2
        from .serve import exact_frame

        row, fresh = exact_frame(out_dir, parse_tc(args.at))
        if fresh:
            print(f"снят новый кадр {row['id']} ровно в {tc_short(row['t'])} — "
                  "текста OCR у него нет, в поиск он не попал", file=sys.stderr)
        rows = [row]
    elif args.ids:
        rows = frames_by_ids(out_dir, [s.strip() for s in args.ids.split(",")])
    elif args.at:
        rows = frames_near(out_dir, parse_tc(args.at), count=args.count)
    else:
        print("укажите --at 4:12, --ids f0043 или --sheet", file=sys.stderr)
        return 2

    if not rows:
        print("кадров не нашлось")
        return 1

    _log_served(out_dir, rows)
    cov = idx["coverage"]
    for r in rows:
        inside = [g for g in cov["gaps"] if g["from"] <= r["t"] <= g["to"]]
        mark = "  ⚠ участок без гарантии покрытия" if inside else ""
        print(f"[{tc_short(r['t'])} / {r['id']}] {display_path(os.path.join(out_dir, r['path']))}"
              f"  ({r['est_tokens']} токенов){mark}")
        if r.get("caption"):
            print(f"    заметка агента: {r['caption']}")
    total = sum(r["est_tokens"] for r in rows)
    print()
    print(f"{len(rows)} {plural(len(rows), 'кадр', 'кадра', 'кадров')}, "
          f"примерно {total} {plural(total, 'визуальный токен', 'визуальных токена', 'визуальных токенов')}.")
    print("Показывай их модели и цитируй меткой [MM:SS / fNNNN].")
    return 0


def _frames_sheet(args: argparse.Namespace, out_dir: str, idx: dict) -> int:
    """Обзорный лист отрезка: 16 миниатюр по цене одного кадра."""
    from .serve import sheet

    duration = float(idx["video"]["duration_sec"])
    t_from = parse_tc(args.t_from) if args.t_from else 0.0
    t_to = parse_tc(args.t_to) if args.t_to else duration
    path, rows, tokens = sheet(out_dir, t_from, t_to)
    _log_served(out_dir, rows, via="sheet")
    print(f"обзор {tc_short(t_from)}–{tc_short(t_to)}: {display_path(path)}  ({tokens} токенов)")
    print("ячейки слева направо, сверху вниз:")
    for n, r in enumerate(rows, 1):
        print(f"  {n:>2}. [{tc_short(r['t'])} / {r['id']}]")
    print()
    print("Миниатюра — чтобы выбрать момент, а не чтобы читать текст. "
          "Мелкое смотри в упор: frameproof frames --ids <id> --out " + display_path(out_dir))
    return 0


def cmd_caption(args: argparse.Namespace) -> int:
    """Заметка агента к кадру: что на нём, после того как кадр посмотрели."""
    from .index import build_search, set_caption

    if not os.path.exists(os.path.join(args.out, "index.json")):
        print(f"нет индекса в {display_path(args.out)}", file=sys.stderr)
        return 2
    if not set_caption(args.out, args.frame_id, args.text.strip()):
        print(f"кадра {args.frame_id} в индексе нет", file=sys.stderr)
        return 1
    build_search(args.out)
    print(f"заметка к {args.frame_id} записана, теперь она находится поиском "
          "(с пометкой «заметка агента» — это не доказательство)")
    return 0


def _log_served(out_dir: str, rows: list[dict], *, via: str = "frames") -> None:
    """Журнал выданных кадров.

    Нужен ровно для одной проверки: утверждение о кадре, который агенту ни разу не
    выдавали, — это утверждение, сделанное не глядя. Поймать это можно только зная,
    что именно инструмент отдал.
    """
    import time

    try:
        with open(os.path.join(out_dir, "served.jsonl"), "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(
                    {"id": r["id"], "t": r["t"], "at": round(time.time(), 3), "via": via},
                    ensure_ascii=False,
                ) + "\n")
    except OSError:
        pass          # журнал — вспомогательный; его отсутствие не должно ломать выдачу


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import audit, plan_second_look, render

    out_dir = args.out
    if not os.path.exists(os.path.join(out_dir, "index.json")):
        print(f"нет индекса в {out_dir}", file=sys.stderr)
        return 2
    if not os.path.exists(args.answer):
        print(f"нет файла с разбором: {args.answer}", file=sys.stderr)
        return 2
    with open(args.answer, encoding="utf-8") as fh:
        answer = fh.read()

    claims = audit(answer, out_dir)

    if args.plan:
        tasks = plan_second_look(claims, out_dir, limit=args.limit)
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        eligible = sum(1 for c in claims if c.needs_second_look)
        if eligible > len(tasks):
            print(f"в план вошло {len(tasks)} из {eligible}, пропущено {eligible - len(tasks)}: "
                  f"сначала взяты сомнительные. Поднять потолок: --limit {eligible}",
                  file=sys.stderr)
        return 0

    if args.json:
        print(json.dumps(
            [
                {
                    "n": c.n, "text": c.text,
                    "t": c.t if c.has_time else None, "untagged": c.untagged,
                    "tc_raw": c.tc_raw, "frame_id": c.frame_id,
                    "severity": c.severity, "needs_second_look": c.needs_second_look,
                    "findings": [
                        {"code": f.code, "severity": f.severity, "detail": f.detail}
                        for f in c.findings
                    ],
                }
                for c in claims
            ],
            ensure_ascii=False, indent=2,
        ))
        return 0

    print(render(claims, index_dir=out_dir))
    if any(c.severity == "FAIL" for c in claims):
        return 1
    # Разбор про экран без единой метки непроверяем целиком — это не «чисто».
    return 1 if claims and all(c.untagged for c in claims) else 0


def cmd_report(args: argparse.Namespace) -> int:
    from .index import load_index

    out_dir = args.out
    if not os.path.exists(os.path.join(out_dir, "index.json")):
        print(f"нет индекса в {display_path(out_dir)}. Сначала: frameproof index <url|файл>",
              file=sys.stderr)
        return 2
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
    print(f"ТРАНСКРИПТ: {t['segment_count']} "
          f"{plural(t['segment_count'], 'реплика', 'реплики', 'реплик')}, "
          f"источник {t['source']}, язык {t['language']}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    import shutil

    print(f"frameproof {__version__}")
    ok = True
    # yt-dlp здесь НЕТ намеренно: код дёргает его как библиотеку (`import yt_dlp`),
    # бинарь в PATH не вызывается ни разу. Проверка бинаря стояла рядом с проверкой
    # модуля и давала «✗ НЕ НАЙДЕН» вместе с «✓» про одно и то же — человек шёл
    # доустанавливать то, что ему не нужно. Модуль проверяется ниже, в своём блоке.
    for tool, why in (("ffmpeg", "разбор видео"), ("ffprobe", "метаданные")):
        path = shutil.which(tool)
        print(f"  {'✓' if path else '✗'} {tool:<8} {path or 'НЕ НАЙДЕН'}   — {why}")
        if not path:
            ok = False
    # JS-runtime. yt-dlp по умолчанию включает только deno, а без рабочего
    # рантайма YouTube отдаёт не все форматы и падает на «n challenge solving
    # failed». Ошибка вылезала посреди загрузки, и понять её было нельзя.
    рантайм = next((b for b in ("deno", "node", "bun", "quickjs") if shutil.which(b)), None)
    print(f"  {'✓' if рантайм else '✗'} JS       {рантайм or 'НЕ НАЙДЕН'}   — "
          f"{'YouTube отдаёт все форматы' if рантайм else 'YouTube отдаст НЕ ВСЕ форматы: поставьте deno или node'}")
    try:
        import numpy
        print(f"  ✓ numpy    {numpy.__version__}")
    except ImportError:
        print("  ✗ numpy    НЕ НАЙДЕН — pip install numpy")
        ok = False
    for mod, why in (("mlx_whisper", "быстрая расшифровка на Apple Silicon"),
                     ("yt_dlp", "загрузка по ссылке (нужен только для ссылок)")):
        try:
            __import__(mod)
            print(f"  ✓ {mod:<12} {why}")
        except ImportError:
            print(f"  · {mod:<12} нет — {why} недоступна")
    # openai-whisper вызывается бинарём из PATH, а не модулем: проверяем то, что зовём.
    whisper_bin = shutil.which("whisper")
    print(f"  {'✓' if whisper_bin else '·'} whisper      "
          + (f"{whisper_bin} — расшифровка везде" if whisper_bin
             else "нет бинаря — расшифровка везде недоступна (pip install -U openai-whisper)"))
    from . import ocr as ocr_mod
    print(f"  {'✓' if ocr_mod.available() else '·'} Apple Vision OCR "
          f"{'доступен' if ocr_mod.available() else 'нет swiftc — OCR пропустится'}")
    print()
    print("готов к работе" if ok else "не хватает обязательного — см. выше")
    return 0 if ok else 1


def _link_or_copy(src: str, dst: str) -> None:
    """Симлинк, а где нельзя — копия. На Windows симлинки требуют режима разработчика."""
    import shutil

    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def cmd_install(args: argparse.Namespace) -> int:
    """Ставит скилл для Claude Code симлинком на канонический каталог."""
    pkg = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(pkg, "assets", "skill")
    if not os.path.exists(os.path.join(src, "SKILL.md")):
        print(f"не нашёл SKILL.md в дистрибутиве ({src})", file=sys.stderr)
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
    _link_or_copy(src, dst)
    print(f"✓ скилл поставлен: {dst} → {src}")

    # Субагент-проверяющий. Claude Code ищет субагентов только в ~/.claude/agents/,
    # рядом со скиллом он их не видит.
    agent_src = os.path.join(pkg, "assets", "agents", "frameproof-adversary.md")
    if os.path.exists(agent_src):
        agents_dir = os.path.expanduser("~/.claude/agents")
        fresh = not os.path.isdir(agents_dir)
        os.makedirs(agents_dir, exist_ok=True)
        agent_dst = os.path.join(agents_dir, "frameproof-adversary.md")
        if os.path.islink(agent_dst) or os.path.exists(agent_dst):
            if args.force:
                os.remove(agent_dst)
            else:
                print(f"  · {agent_dst} уже есть, пропускаю (--force чтобы перезаписать)")
                agent_dst = ""
        if agent_dst:
            _link_or_copy(agent_src, agent_dst)
            print(f"✓ проверяющий поставлен: {agent_dst}")
            if fresh:
                print("  ⚠ каталог ~/.claude/agents создан впервые — нужен рестарт Claude Code")

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
    i.add_argument("--max-frames", type=int, default=0,
                   help="потолок кадров (0 = считать от длительности)")
    i.add_argument("--fast", action="store_true",
                   help="в 38 раз быстрее: кандидаты берутся из ключевых кадров. "
                        "Плата — кадр встаёт туда, куда его поставил кодировщик, "
                        "а не туда, где на экране дописалась мысль")
    i.add_argument("--width", type=int, default=1280, help="ширина кадра (1280 = 1196 токенов)")
    i.add_argument("--max-height", type=int, default=1080, help="качество скачиваемого потока")
    i.add_argument("--lang", default=None, help="язык для расшифровки, напр. ru")
    i.add_argument("--ocr", action="store_true", help="распознать текст на кадрах (macOS)")
    i.add_argument("--ocr-width", type=int, default=0,
                   help="ширина копии для распознавания (0 = родное разрешение видео). "
                        "Кадры показа остаются лёгкими, крупная копия удаляется сразу")
    i.add_argument("--ocr-command", default=None,
                   help="чужой распознаватель: принимает пути к картинкам, "
                        "печатает строки «путь<TAB>текст». Для Windows и Linux")
    i.add_argument("--subs", default=None,
                   help="готовые субтитры (.vtt/.srt/.json3) — вместо расшифровки")
    i.add_argument("--no-cues", action="store_true",
                   help="не ставить кадры по указательным репликам («вот здесь», «смотрите»)")
    i.add_argument("--no-transcribe", action="store_true",
                   help="не расшифровывать, если нет субтитров")
    from .fetch import БРАУЗЕРЫ_С_COOKIES  # локально: не тянуть fetch.py ради других команд

    i.add_argument(
        "--cookies-from-browser", metavar="БРАУЗЕР", default=None,
        help="cookies живой сессии браузера — ДОСТУП К ВАШЕМУ АККАУНТУ YouTube, "
             "не анонимная загрузка. Нужно только когда 403 держится на каждом "
             "переспробованном формате (перебор форматов такое не лечит — это "
             "YouTube просит авторизацию, а не режет конкретный формат). По "
             "умолчанию выключено. Браузеры: " + ", ".join(БРАУЗЕРЫ_С_COOKIES),
    )
    i.add_argument(
        "--js-runtime", metavar="ИМЯ", default=None,
        help="чем исполнять JS при загрузке с YouTube: deno, node, bun, quickjs. "
             "Без флага берётся первый найденный. Нужен, если yt-dlp жалуется "
             "на «n challenge solving failed» или «No supported JavaScript runtime».",
    )
    i.add_argument(
        "--proxy", metavar="АДРЕС", default=None,
        help="прокси для загрузки: socks5://127.0.0.1:1080 или http://host:port. "
             "Нужен там, где хост закрыт. Без флага берётся из ALL_PROXY, "
             "HTTPS_PROXY или HTTP_PROXY: yt-dlp как библиотека сам их НЕ читает, "
             "поэтому мы читаем за него.",
    )
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
    f.add_argument("--exact", action="store_true",
                   help="снять кадр ровно в момент --at, если готового рядом нет")
    f.add_argument("--sheet", action="store_true",
                   help="обзорный лист: 16 миниатюр отрезка одной картинкой")
    f.add_argument("--from", dest="t_from", help="начало отрезка для --sheet")
    f.add_argument("--to", dest="t_to", help="конец отрезка для --sheet")
    f.set_defaults(func=cmd_frames)

    c = sub.add_parser("caption", help="записать заметку к кадру после просмотра")
    c.add_argument("frame_id", help="например f0043")
    c.add_argument("text", help="что на кадре, своими словами")
    c.add_argument("--out", required=True, help="папка индекса")
    c.set_defaults(func=cmd_caption)

    r = sub.add_parser("report", help="показать покрытие готового индекса")
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_report)

    v = sub.add_parser("verify", help="проверить утверждения разбора против индекса")
    v.add_argument("answer", help="файл с разбором, который написал агент")
    v.add_argument("--out", required=True, help="папка индекса")
    v.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    v.add_argument("--plan", action="store_true",
                   help="выдать задания для слепого проверяющего вместо отчёта")
    v.add_argument("--limit", type=int, default=8,
                   help="потолок утверждений для второго взгляда (по умолчанию 8)")
    v.set_defaults(func=cmd_verify)

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
