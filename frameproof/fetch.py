"""Загрузка видео и субтитров через yt-dlp.

Два решения, которые здесь важнее, чем кажутся.

1. СНАЧАЛА СУБТИТРЫ. Если у ролика есть готовая дорожка — она бесплатна, уже с
   тайм-кодами и обычно точнее ASR. Расшифровка запускается только когда их нет.

2. VIDEO-ONLY ПОТОК В ВЫСОКОМ РАЗРЕШЕНИИ. Прогрессивный формат у YouTube часто
   ограничен 360p — на нём мелкий текст в терминале и код нечитаемы, а именно они
   и нужны. Звук в этом файле не нужен: он уже взят субтитрами или отдельной дорожкой.

yt-dlp дёргается как БИБЛИОТЕКА, а не через CLI: в песочнице у CLI бывает
`curl: (6) Could not resolve host`, тогда как библиотечный путь работает.

Kinescope идёт мимо yt-dlp целиком — экстрактора там нет, а через манифест он качает
не то. Своя загрузка в `kinescope.py`, туда же вынесены измерения и ссылки на заявки.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

SUB_LANGS = ("ru", "en")


@dataclass
class Fetched:
    video_path: str
    title: str
    duration: float
    source_url: str
    subtitle_path: str | None = None
    subtitle_lang: str | None = None
    subtitle_auto: bool = False
    audio_path: str | None = None
    extra: dict = field(default_factory=dict)


def _ydl():
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "нужен yt-dlp: pip install yt-dlp  (или brew install yt-dlp)"
        ) from exc
    return yt_dlp


def probe_remote(url: str) -> dict:
    yt_dlp = _ydl()
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        return ydl.extract_info(url, download=False)


def _pick_subtitle(info: dict, langs=SUB_LANGS) -> tuple[str | None, str | None, bool]:
    """Возвращает (url, язык, авто?). Ручные субтитры приоритетнее автоматических."""
    for auto in (False, True):
        table = info.get("automatic_captions" if auto else "subtitles") or {}
        for lang in langs:
            for key in (lang, f"{lang}-orig"):
                tracks = table.get(key)
                if not tracks:
                    continue
                # json3 предпочтительнее vtt: у авто-субтитров YouTube в vtt
                # строки «катятся» и дублируются.
                for ext in ("json3", "vtt", "srv3", "ttml"):
                    for tr in tracks:
                        if tr.get("ext") == ext and tr.get("url"):
                            return tr["url"], key, auto
                if tracks and tracks[0].get("url"):
                    return tracks[0]["url"], key, auto
    return None, None, False


def fetch(url: str, work_dir: str, *, max_height: int = 1080,
          langs=SUB_LANGS, want_video: bool = True, want_audio: bool = True) -> Fetched:
    os.makedirs(work_dir, exist_ok=True)

    from .kinescope import is_kinescope

    if is_kinescope(url):
        return _fetch_kinescope(
            url, work_dir, max_height=max_height, want_video=want_video, want_audio=want_audio
        )

    yt_dlp = _ydl()
    info = probe_remote(url)

    sub_path = None
    sub_url, sub_lang, sub_auto = _pick_subtitle(info, langs)
    if sub_url:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            raw = ydl.urlopen(sub_url).read()
        ext = "json3" if b'"events"' in raw[:400] else "vtt"
        sub_path = os.path.join(work_dir, f"subs.{sub_lang}.{ext}")
        with open(sub_path, "wb") as fh:
            fh.write(raw)

    # Видео тянем БЕЗ звука — так поток чётче при том же весе. Но если субтитров
    # не нашлось, расшифровывать будет нечего, поэтому аудиодорожку берём отдельно.
    audio_path = None
    if not sub_path:
        audio_path = os.path.join(work_dir, "audio.m4a")
        if not os.path.exists(audio_path):
            try:
                with yt_dlp.YoutubeDL({
                    "quiet": True, "no_warnings": True,
                    "outtmpl": audio_path, "format": "ba/bestaudio/best",
                }) as ydl:
                    ydl.download([url])
            except Exception:
                audio_path = None
        if audio_path and not os.path.exists(audio_path):
            audio_path = next(
                (os.path.join(work_dir, n) for n in os.listdir(work_dir)
                 if n.startswith("audio.") and not n.endswith((".part", ".ytdl"))),
                None,
            )

    video_path = ""
    if want_video:
        video_path = os.path.join(work_dir, "video.mp4")
        if not os.path.exists(video_path):
            opts = {
                "quiet": True,
                "no_warnings": True,
                "outtmpl": video_path,
                # Только видеодорожка: звук здесь не нужен, а качество картинки нужно.
                "format": (
                    f"bv*[height<={max_height}][ext=mp4]/"
                    f"bv*[height<={max_height}]/"
                    f"b[height<={max_height}]"
                ),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        if not os.path.exists(video_path):
            for name in os.listdir(work_dir):
                if name.startswith("video.") and not name.endswith((".part", ".ytdl")):
                    video_path = os.path.join(work_dir, name)
                    break

    return Fetched(
        video_path=video_path,
        title=str(info.get("title") or "video"),
        duration=float(info.get("duration") or 0.0),
        source_url=url,
        subtitle_path=sub_path,
        subtitle_lang=sub_lang,
        subtitle_auto=sub_auto,
        audio_path=audio_path,
        extra={
            "id": info.get("id"),
            "channel": info.get("channel"),
            "upload_date": info.get("upload_date"),
        },
    )


def _fetch_kinescope(url: str, work_dir: str, *, max_height: int,
                     want_video: bool, want_audio: bool = True) -> Fetched:
    """Kinescope мимо yt-dlp: экстрактора там нет, а через манифест он качает не то.

    Подробности устройства и ссылки на заявки — в `kinescope.py`. Субтитров хостинг
    не отдаёт, поэтому звук берём всегда: расшифровывать иначе будет нечего.
    """
    import sys

    from . import kinescope as ks

    vid = ks.video_id(url)
    tracks, duration = ks.parse(ks.manifest(vid))

    def show(label: str, size: int):
        def cb(done: int, total: int):
            if total and (done % (16 << 20) < (1 << 20) or done >= total):
                print(f"\r{label}: {done * 100 // total} % из {total >> 20} МБ",
                      end="", file=sys.stderr, flush=True)
        return cb

    video_path = ""
    if want_video:
        track = ks.pick_video(tracks, max_height)
        print(f"kinescope: {track.height}p, {track.size >> 20} МБ", file=sys.stderr)
        video_path = ks.download(
            track, os.path.join(work_dir, "video.mp4"), progress=show("видео", track.size)
        )
        print("", file=sys.stderr)

    # Звук весит как половина видео, а при готовых субтитрах или --no-transcribe
    # он не нужен вовсе. Качать его «на всякий случай» — сто мегабайт впустую.
    audio_path = None
    audio = ks.pick_audio(tracks) if want_audio else None
    if audio:
        audio_path = ks.download(
            audio, os.path.join(work_dir, "audio.m4a"), progress=show("звук", audio.size)
        )
        print("", file=sys.stderr)

    return Fetched(
        video_path=video_path,
        title=f"kinescope {vid}",
        duration=duration,
        source_url=url,
        audio_path=audio_path,
        extra={"id": vid, "host": "kinescope"},
    )
