"""ffprobe: узнать про файл то, без чего нельзя считать тайм-коды."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .util import run, which


@dataclass(frozen=True)
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    vfr: bool
    codec: str

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9


def _ratio(value: str | None) -> float:
    """'30000/1001' -> 29.97. ffprobe отдаёт дроби строкой."""
    if not value or value in ("0/0", "N/A"):
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return 0.0
        return num_f / den_f if den_f else 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe(path: str) -> VideoInfo:
    proc = run(
        [
            which("ffprobe"),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,codec_name",
            "-show_entries", "format=duration",
            "-of", "json",
            path,
        ]
    )
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"в файле нет видеодорожки: {path}")
    st = streams[0]

    r_fps = _ratio(st.get("r_frame_rate"))
    avg_fps = _ratio(st.get("avg_frame_rate"))
    fps = avg_fps or r_fps or 25.0

    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0:
        raise ValueError(f"ffprobe не смог определить длительность: {path}")

    # Заметное расхождение между номинальным и средним fps — признак VFR.
    # Для VFR тайм-коды НЕЛЬЗЯ считать как номер_кадра / fps: они поедут.
    vfr = bool(r_fps and avg_fps and abs(r_fps - avg_fps) / max(r_fps, avg_fps) > 0.01)

    return VideoInfo(
        path=path,
        duration=duration,
        width=int(st.get("width") or 0),
        height=int(st.get("height") or 0),
        fps=fps,
        vfr=vfr,
        codec=str(st.get("codec_name") or "?"),
    )
