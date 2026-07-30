"""Транскрипт: сначала готовые субтитры, потом локальная расшифровка.

Каскад по убыванию выгоды:
  1. Ручные субтитры  — бесплатно, с тайм-кодами, обычно точнее ASR.
  2. Авто-субтитры    — бесплатно, качество ниже; берём json3, потому что в vtt
                        строки «катятся» и дублируются.
  3. mlx-whisper      — локально на Apple Silicon, реально считает на Metal-GPU.
  4. whisper.cpp      — локально, один бинарник, без torch.
  5. openai-whisper   — последний рубеж: работает везде, но на Mac упирается в CPU.

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
