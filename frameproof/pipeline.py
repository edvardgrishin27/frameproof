"""Сборка индекса: от ссылки или файла до index.json с отчётом покрытия.

Вынесено из CLI, чтобы команда `index` не тонула среди остальных: здесь всё,
что происходит до первой строки отчёта, — папка индекса, загрузка, звук,
субтитры, окно, анализ, отбор, извлечение и OCR.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from .util import display_path, parse_tc, plural, slugify, tc_short


#: На сколько секунд анализ может не дойти до конца ролика, прежде чем это обрыв.
DECODE_SLACK = 5.0


_ССЫЛКА = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|www\.)|^[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/", re.I)


_ВИДЕО = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mpg", ".mpeg", ".ts")


def _похоже_на_ссылку(target: str) -> bool:
    """Ссылка со схемой, с www. или вида `домен.зона/путь`. Путь к файлу — нет.

    `my.videos/clip.mp4` формально похож на домен, поэтому без схемы имя с
    расширением видео считаем путём: опечатку в пути лучше назвать, чем «качать».
    """
    t = target.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://|^www\.", t, re.I):
        return True
    if t.lower().endswith(_ВИДЕО):
        return False
    return bool(_ССЫЛКА.match(t))


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

    window = _window(args, info.duration)
    if window is False:
        return 2

    if transcript is None and not args.no_transcribe:
        transcript = _local_transcript(audio_path or video_path, out_dir, args.lang)
    if transcript is not None and window:
        # Речь вне окна без кадров только путала бы поиск: оставляем реплики окна.
        from dataclasses import replace as _replace

        a, b = window
        transcript = _replace(transcript, segments=[
            s for s in transcript.segments if s.t1 >= a and s.t0 <= b])
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

    start, end = window or (0.0, info.duration)
    try:
        # -t только для явного окна: на полном проходе он обрезал бы хвост,
        # если probe занизил длительность.
        sig = analyze(info, fast=args.fast, start=start, end=end if window else None)
    except FastModeUnreliable as exc:
        print(f"⚠ быстрый проход ненадёжен ({exc}) — делаю полный", file=sys.stderr)
        sig = analyze(info, fast=False, start=start, end=end if window else None)
    # Декод мог оборваться на битом файле: анализ дошёл до середины и замолчал.
    # Кадры дальше поставит только сетка — об этом надо сказать вслух.
    if not args.fast and len(sig.times) and end - sig.times[-1] > DECODE_SLACK:
        print(f"⚠ декод оборвался на {tc_short(sig.times[-1])} из {tc_short(end)}: "
              "дальше смены экрана не отслеживались, кадры там только по сетке, "
              "а снять их может не выйти — смотри отчёт покрытия", file=sys.stderr)

    cues: list[float] = []
    if transcript is not None and not args.no_cues:
        from .transcribe import pointing_cues

        cues = pointing_cues(transcript)
        if cues:
            print(f"якорей по речи («вот здесь», «смотрите»): {len(cues)}", file=sys.stderr)

    sel = select_frames(
        sig, end, max_gap=args.max_gap, cap=args.max_frames, cues=cues, start=start
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
        window=window or None,
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


def _window(args: argparse.Namespace, duration: float):
    """Окно --from/--to в секундах; None — весь ролик; False — окно задано с ошибкой."""
    if not (args.t_from or args.t_to):
        return None
    try:
        a = parse_tc(args.t_from) if args.t_from else 0.0
        b = parse_tc(args.t_to) if args.t_to else duration
    except ValueError as exc:
        print(f"окно: {exc}", file=sys.stderr)
        return False
    b = min(b, duration)
    if not 0.0 <= a < b:
        print(f"окно {args.t_from or '0:00'}–{args.t_to or tc_short(duration)} пустое или "
              f"вне ролика ({tc_short(duration)})", file=sys.stderr)
        return False
    print(f"окно {tc_short(a)}–{tc_short(b)} из {tc_short(duration)}: кадры только в нём",
          file=sys.stderr)
    return a, b


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
