"""ffprobe: узнать про файл то, без чего нельзя считать тайм-коды."""

from __future__ import annotations

import json
import os
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


def _rotation(stream: dict) -> int:
    """Поворот дорожки в градусах, 0..359. Новый ffprobe кладёт его в side data,
    старый (4.x) — в тег `rotate`."""
    raw = None
    for item in stream.get("side_data_list") or []:
        if "rotation" in item:
            raw = item["rotation"]
            break
    if raw is None:
        raw = (stream.get("tags") or {}).get("rotate")
    try:
        return int(round(float(raw))) % 360 if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def probe(path: str) -> VideoInfo:
    # Имя, начинающееся с минуса, ffprobe прочитал бы как ключ командной строки.
    if path.startswith("-"):
        path = os.path.join(os.curdir, path)
    proc = run(
        [
            which("ffprobe"),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,avg_frame_rate,codec_name,duration"
            ":stream_side_data=rotation:stream_tags=rotate",
            "-show_entries", "format=duration",
            "-of", "json",
            path,
        ],
        check=False,
    )
    if proc.returncode != 0:
        # Сырой дамп команды человеку ничего не говорит. Причина — в stderr ffprobe:
        # «moov atom not found» значит, что файл обрезан или ещё дописывается.
        why = (proc.stderr or "").strip().splitlines()
        raise ValueError(f"ffprobe не смог прочитать файл {path}: "
                         f"{why[-1] if why else 'код ' + str(proc.returncode)}. "
                         "Файл повреждён, обрезан или ещё записывается?")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"в файле нет видеодорожки: {path}")
    st = streams[0]

    r_fps = _ratio(st.get("r_frame_rate"))
    avg_fps = _ratio(st.get("avg_frame_rate"))
    fps = avg_fps or r_fps or 25.0

    # Длительность — по видеодорожке. Контейнер длиной в самую длинную дорожку,
    # а в записи экрана или созвона звук часто пишется дольше картинки. Кадры,
    # поставленные после конца видео, ffmpeg снять не может (код 234), и раньше
    # это роняло весь индекс. У mkv и webm длительности дорожки нет — тогда
    # берём контейнер.
    container = _ratio((data.get("format") or {}).get("duration"))
    stream = _ratio(st.get("duration"))
    duration = stream or container
    if duration <= 0:
        raise ValueError(f"ffprobe не смог определить длительность: {path}")

    # Заметное расхождение между номинальным и средним fps — признак VFR.
    # Для VFR тайм-коды НЕЛЬЗЯ считать как номер_кадра / fps: они поедут.
    vfr = bool(r_fps and avg_fps and abs(r_fps - avg_fps) / max(r_fps, avg_fps) > 0.01)

    width, height = int(st.get("width") or 0), int(st.get("height") or 0)
    # Телефон пишет кадр боком и ставит метку поворота. ffmpeg при извлечении
    # поворачивает сам, поэтому считать надо в размерах ПОКАЗА. Раньше брались
    # размеры дорожки, `scale=640:360` сплющивал повёрнутую картинку 360x640,
    # и кадр выходил растянутым боком.
    if _rotation(st) in (90, 270):
        width, height = height, width

    return VideoInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        vfr=vfr,
        codec=str(st.get("codec_name") or "?"),
    )
