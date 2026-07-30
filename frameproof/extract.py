"""Извлечение отобранных кадров в полном качестве.

Анализ шёл по дешёвой серой копии 320 px — там искали ГДЕ. Здесь достаём ЧТО,
из исходника, по найденным тайм-кодам.

JPEG, а не PNG: токены считаются только от пикселей, вес файла на них не влияет,
поэтому PNG даёт ровно ноль выигрыша и только жрёт лимит запроса и латентность.
`-q:v 3` — компромисс: ниже 5 опускаться нельзя, тяжёлое сжатие делает текст на
экране нечитаемым.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .budget import DEFAULT_WIDTH, scaled_height
from .probe import VideoInfo
from .util import run, tc_short, which

JPEG_QUALITY = 3


@dataclass(frozen=True)
class ExtractedFrame:
    id: str
    t: float
    path: str
    width: int
    height: int
    bytes: int
    reason: str


def extract(
    info: VideoInfo,
    picks,
    out_dir: str,
    *,
    width: int = DEFAULT_WIDTH,
    quality: int = JPEG_QUALITY,
) -> list[ExtractedFrame]:
    os.makedirs(out_dir, exist_ok=True)
    width = min(width, info.width) if info.width else width
    height = scaled_height(info.width or 1920, info.height or 1080, width)
    ffmpeg = which("ffmpeg")

    frames: list[ExtractedFrame] = []
    for i, pick in enumerate(picks):
        fid = f"f{i:04d}"
        path = os.path.join(out_dir, f"{fid}.jpg")
        # -ss ДО -i это input seeking: прыжок по ключевым кадрам, а не декод с начала.
        run(
            [
                ffmpeg, "-v", "error", "-y",
                "-ss", f"{pick.t:.3f}",
                "-i", info.path,
                "-frames:v", "1",
                "-vf", f"scale={width}:{height}:flags=lanczos",
                "-q:v", str(quality),
                path,
            ]
        )
        if not os.path.exists(path):
            continue
        frames.append(
            ExtractedFrame(
                id=fid,
                t=pick.t,
                path=path,
                width=width,
                height=height,
                bytes=os.path.getsize(path),
                reason=pick.reason,
            )
        )
    return frames


def contact_sheet(frames: list[ExtractedFrame], out_path: str, *, cols: int = 4, rows: int = 4,
                  cell_width: int = 320) -> str | None:
    """Обзорный лист: 16 миниатюр в одном изображении.

    Стоит столько же токенов, сколько один полноразмерный кадр, но даёт агенту
    временную динамику целиком — по нему он выбирает, какие кадры смотреть в упор.
    """
    if not frames:
        return None
    take = frames[: cols * rows]
    inputs: list[str] = []
    for f in take:
        inputs += ["-i", f.path]
    n = len(take)
    cell_h = scaled_height(take[0].width, take[0].height, cell_width)
    layout = "|".join(
        f"{(i % cols) * cell_width}_{(i // cols) * cell_h}" for i in range(n)
    )
    scale_chain = "".join(f"[{i}:v]scale={cell_width}:{cell_h}[s{i}];" for i in range(n))
    refs = "".join(f"[s{i}]" for i in range(n))
    filt = f"{scale_chain}{refs}xstack=inputs={n}:layout={layout}[out]"
    try:
        run([which("ffmpeg"), "-v", "error", "-y", *inputs,
             "-filter_complex", filt, "-map", "[out]", "-q:v", "3", out_path])
    except Exception:
        return None
    return out_path if os.path.exists(out_path) else None


def label(frame: ExtractedFrame) -> str:
    return f"[{tc_short(frame.t)} / {frame.id}]"
