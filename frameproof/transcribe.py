"""Транскрипт: сначала готовые субтитры, потом локальная расшифровка.

Каскад по убыванию выгоды:
  1. Свои субтитры    — файл рядом с видео через --subs: уже есть, ничего не стоит.
  2. Ручные субтитры  — бесплатно, с тайм-кодами, обычно точнее ASR.
  3. Авто-субтитры    — бесплатно, качество ниже; берём json3, потому что в vtt
                        строки «катятся» и дублируются.
  4. mlx-whisper      — локально на Apple Silicon, реально считает на Metal-GPU.
  5. openai-whisper   — последний рубеж: работает везде, но на Mac упирается в CPU.

whisper.cpp тут раньше стоял четвёртым пунктом, но в коде его не было ни дня — это
был план, записанный как факт. Убран из списка: пока не реализован, обещать нечего.
На системах без Apple Silicon его место закрывает --subs.

faster-whisper сознательно не в приоритете на Mac: CTranslate2 в списке поддерживаемого
железа перечисляет CPU (x86-64/AArch64) и NVIDIA GPU — Apple GPU/Metal там нет,
то есть на M-серии это CPU-путь.

Ключи и сеть не нужны ни на одной ступени.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
from dataclasses import dataclass


@dataclass
class Segment:
    i: int
    t0: float
    t1: float
    text: str

    def as_row(self) -> dict:
        return {"i": self.i, "t0": round(self.t0, 3), "t1": round(self.t1, 3), "text": self.text}


@dataclass
class Transcript:
    segments: list[Segment]
    source: str          # 'subs-manual' | 'subs-auto' | 'mlx-whisper' | ...
    language: str | None

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)


_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")


def _vtt_time(value: str) -> float | None:
    m = _TS.search(value)
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_json3(raw: bytes | str) -> list[Segment]:
    data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
    out: list[Segment] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        t0 = float(ev.get("tStartMs", 0)) / 1000.0
        t1 = t0 + float(ev.get("dDurationMs", 0)) / 1000.0
        out.append(Segment(i=len(out), t0=t0, t1=t1, text=" ".join(text.split())))
    return _dedup_rolling(out)


def parse_vtt(raw: bytes | str) -> list[Segment]:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    out: list[Segment] = []
    t0 = t1 = None
    buf: list[str] = []

    def flush():
        nonlocal t0, t1, buf
        if t0 is not None and buf:
            body = " ".join(" ".join(buf).split())
            body = re.sub(r"<[^>]+>", "", html.unescape(body)).strip()
            if body:
                out.append(Segment(i=len(out), t0=t0, t1=t1 if t1 is not None else t0, text=body))
        t0 = t1 = None
        buf = []

    for line in text.splitlines():
        line = line.strip()
        if "-->" in line:
            flush()
            left, _, right = line.partition("-->")
            t0, t1 = _vtt_time(left), _vtt_time(right)
        elif not line:
            flush()
        elif line.upper().startswith(("WEBVTT", "KIND:", "LANGUAGE:", "NOTE")):
            continue
        elif line.isdigit():
            continue
        else:
            buf.append(line)
    flush()
    return _dedup_rolling(out)


def _dedup_rolling(segs: list[Segment]) -> list[Segment]:
    """Схлопывает «катящиеся» дубли авто-субтитров YouTube.

    Там каждая следующая реплика повторяет предыдущую целиком и дописывает хвост.
    Без этого транскрипт раздувается втрое и греп находит одно и то же по десять раз.
    """
    out: list[Segment] = []
    for s in segs:
        if out:
            prev = out[-1]
            if s.text == prev.text:
                out[-1] = Segment(prev.i, prev.t0, max(prev.t1, s.t1), prev.text)
                continue
            if s.text.startswith(prev.text) and len(prev.text) >= 10:
                out[-1] = Segment(prev.i, prev.t0, max(prev.t1, s.t1), s.text)
                continue
        out.append(Segment(i=len(out), t0=s.t0, t1=s.t1, text=s.text))
    return [Segment(i=i, t0=s.t0, t1=s.t1, text=s.text) for i, s in enumerate(out)]


def from_subtitles(path: str, *, auto: bool = False, lang: str | None = None) -> Transcript:
    with open(path, "rb") as fh:
        raw = fh.read()
    segs = parse_json3(raw) if path.endswith(".json3") or raw.lstrip()[:1] == b"{" else parse_vtt(raw)
    return Transcript(segments=segs, source="subs-auto" if auto else "subs-manual", language=lang)


def _mlx_whisper(audio: str, lang: str | None) -> Transcript | None:
    try:
        import mlx_whisper
    except ImportError:
        return None
    res = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
        language=lang,
        word_timestamps=False,
    )
    segs = [
        Segment(i=i, t0=float(s["start"]), t1=float(s["end"]), text=str(s["text"]).strip())
        for i, s in enumerate(res.get("segments") or [])
    ]
    return Transcript(segments=segs, source="mlx-whisper", language=res.get("language") or lang)


def _openai_whisper(audio: str, lang: str | None) -> Transcript | None:
    if not shutil.which("whisper"):
        return None
    from .util import run
    out_dir = os.path.dirname(audio) or "."
    cmd = ["whisper", audio, "--model", "small", "--output_format", "json",
           "--output_dir", out_dir, "--verbose", "False"]
    if lang:
        cmd += ["--language", lang]
    run(cmd)
    stem = os.path.splitext(os.path.basename(audio))[0]
    js = os.path.join(out_dir, f"{stem}.json")
    if not os.path.exists(js):
        return None
    with open(js, encoding="utf-8") as fh:
        data = json.load(fh)
    segs = [
        Segment(i=i, t0=float(s["start"]), t1=float(s["end"]), text=str(s["text"]).strip())
        for i, s in enumerate(data.get("segments") or [])
    ]
    return Transcript(segments=segs, source="openai-whisper", language=data.get("language") or lang)


def transcribe_audio(audio: str, *, lang: str | None = None) -> Transcript:
    """Локальная расшифровка. Пробуем от быстрого к медленному."""
    for engine in (_mlx_whisper, _openai_whisper):
        try:
            result = engine(audio, lang)
        except Exception:
            result = None
        if result and result.segments:
            return result
    raise RuntimeError(
        "не нашёл локального движка расшифровки. Поставьте один из:\n"
        "  pip install mlx-whisper      # быстро на Apple Silicon\n"
        "  pip install -U openai-whisper"
    )


#: Слова, которыми человек показывает на экран. Русские и английские.
#: Смысл: в такие моменты кадр нужен ГАРАНТИРОВАННО, потому что речь без картинки
#: там бессмысленна — «вот здесь ставим галочку» не значит ничего без экрана.
POINTING = (
    "вот тут", "вот здесь", "вот так", "вот эта", "вот этот", "вот это",
    "смотрите", "посмотрите", "обратите внимание", "видите", "как видите",
    "вот я", "вот сюда", "нажимаю", "нажимаем", "кликаю", "вставляю", "ввожу",
    "на экране", "здесь у нас", "тут у нас", "покажу", "показываю",
    "look here", "look at", "right here", "as you can see", "you can see",
    "notice", "watch this", "over here", "on screen", "i'm clicking", "i click",
)


def pointing_cues(transcript, *, words=POINTING) -> list[float]:
    """Моменты, где речь показывает на экран и кадр обязателен.

    Замер на живом 38-минутном гайде: таких реплик 81, и у четверти ближайший кадр
    был дальше пяти секунд. То есть агент читал «вот здесь ставим галочку» и не имел
    ни одного кадра, чтобы увидеть, где именно.

    Отбор кадров по изменению картинки этого не ловит в принципе: экран мог не
    поменяться вовсе, а важность момента задаётся голосом.
    """
    if transcript is None:
        return []
    out: list[float] = []
    for seg in transcript.segments:
        low = " ".join(seg.text.lower().replace("ё", "е").split())
        if any(w in low for w in words):
            # Показывают обычно чуть позже начала реплики — берём её середину.
            out.append(round((seg.t0 + min(seg.t1, seg.t0 + 6.0)) / 2.0, 3))
    return sorted(set(out))
