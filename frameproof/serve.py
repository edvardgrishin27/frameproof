"""Кадры по запросу: точный момент и обзорный лист.

Индекс снимает кадры заранее, и ближайший к нужному моменту может стоять в
8–15 секундах от него. Здесь кадр снимается ровно тогда, когда о нём спросили,
и дописывается в индекс, не сдвигая старых номеров: разборы по ним остаются верными.

Обзорный лист — 16 миниатюр одной картинкой по цене одного кадра. По нему видно
течение отрезка целиком и понятно, какие кадры смотреть в упор.
"""

from __future__ import annotations

import json
import math
import os

from .budget import DEFAULT_WIDTH, effective_tokens
from .extract import ExtractedFrame, contact_sheet, extract
from .index import frame_row, load_index
from .probe import probe
from .select import Pick
from .util import tc_short

#: Готовый кадр ближе этого к запрошенному моменту — тот же самый, новый не нужен.
EXACT_SAME = 0.25

#: Сколько миниатюр в обзорном листе и в сколько столбцов.
SHEET_CELLS = 16
SHEET_COLS = 4


def _rows(out_dir: str) -> list[dict]:
    with open(os.path.join(out_dir, "frames.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _video_source(out_dir: str, idx: dict) -> str | None:
    """Исходник для точного кадра: путь из индекса, а у ссылок — скачанный файл."""
    path = (idx.get("video") or {}).get("path")
    if path and os.path.exists(path):
        return path
    local = os.path.join(out_dir, "video.mp4")
    return local if os.path.exists(local) else None


def _segment_at(out_dir: str, t: float) -> int | None:
    path = os.path.join(out_dir, "segments.jsonl")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                s = json.loads(line)
                if s["t0"] <= t <= s.get("t1", s["t0"]):
                    return s["i"]
    return None


def exact_frame(out_dir: str, t: float) -> tuple[dict, bool]:
    """Кадр ровно в момент t. Возвращает строку индекса и признак «снят только что»."""
    idx = load_index(out_dir)
    duration = float((idx.get("video") or {}).get("duration_sec") or 0.0)
    if duration and not 0.0 <= t <= duration:
        raise ValueError(f"момент {tc_short(t)} за пределами ролика ({tc_short(duration)})")

    rows = _rows(out_dir)
    nearest = min(rows, key=lambda r: abs(r["t"] - t), default=None)
    if nearest is not None and abs(nearest["t"] - t) <= EXACT_SAME:
        return nearest, False

    source = _video_source(out_dir, idx)
    if source is None:
        raise RuntimeError(
            "исходного видео рядом с индексом нет — точный кадр снять не из чего. "
            "Пересоберите индекс: frameproof index <ссылка|файл>")

    next_id = 1 + max((int(r["id"][1:]) for r in rows), default=-1)
    width = rows[0]["w"] if rows else DEFAULT_WIDTH
    got = extract(probe(source), [Pick(t=round(t, 3), reason="exact", change=0.0)],
                  os.path.join(out_dir, "frames"), width=width, first_id=next_id)
    row = frame_row(got[0], out_dir=out_dir,
                    source_url=(idx.get("video") or {}).get("source_url"),
                    segment_i=_segment_at(out_dir, t))
    with open(os.path.join(out_dir, "frames.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row, True


def sheet(out_dir: str, t_from: float, t_to: float, *,
          cells: int = SHEET_CELLS) -> tuple[str, list[dict], int]:
    """Обзорный лист отрезка: путь к картинке, кадры в ячейках, цена в токенах."""
    rows = sorted((r for r in _rows(out_dir) if t_from <= r["t"] <= t_to),
                  key=lambda r: r["t"])
    if not rows:
        raise ValueError(f"на отрезке {tc_short(t_from)}–{tc_short(t_to)} кадров нет")
    if len(rows) > cells:
        step = len(rows) / cells
        rows = [rows[int(k * step)] for k in range(cells)]

    frames = [ExtractedFrame(id=r["id"], t=r["t"], path=os.path.join(out_dir, r["path"]),
                             width=r["w"], height=r["h"], bytes=r.get("bytes", 0),
                             reason=r.get("reason", ""))
              for r in rows]
    cols = min(SHEET_COLS, len(frames))
    grid_rows = math.ceil(len(frames) / cols)
    cell_w = max(2, (DEFAULT_WIDTH // cols) // 2 * 2)
    os.makedirs(os.path.join(out_dir, "sheets"), exist_ok=True)
    out_path = os.path.join(
        out_dir, "sheets", f"sheet_{int(t_from)}_{int(t_to)}_{len(frames)}.jpg")
    if contact_sheet(frames, out_path, cols=cols, rows=grid_rows, cell_width=cell_w) is None:
        raise RuntimeError("ffmpeg не собрал обзорный лист")
    cell_h = max(2, round(frames[0].height * cell_w / frames[0].width))
    return out_path, rows, effective_tokens(cell_w * cols, cell_h * grid_rows)
