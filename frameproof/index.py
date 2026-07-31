"""Индекс видео: карта + два потока строк + поиск.

Раскладка намеренно из трёх файлов, а не одного большого JSON:

  index.json      — карта видео. Маленькая, агент читает её ЦЕЛИКОМ.
  segments.jsonl  — по строке на реплику. Грепается, а не парсится.
  frames.jsonl    — по строке на кадр. Тоже грепается.

Один объект на строку — раскладка из `openai/whisper` (класс `WriteJSONL`), поля
`start`/`end`/`text` совместимы с любым whisper-пайплайном.

Тайм-код — секунды float, единственный источник истины. Строка `4:11` рядом только
для человека. Ссылка на момент — синтаксис W3C Media Fragments (`#t=251`).

Поиск — SQLite FTS5 с токенайзером `trigram`. Триграммы важны именно для русского:
снимают падежи и стемминг без единой внешней зависимости. Векторный индекс не нужен:
транскрипт часа это около 50 КБ.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from .budget import effective_tokens
from .util import tc, tc_short

SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class Hit:
    kind: str          # 'speech' | 'screen'
    t: float
    text: str
    ref: str           # 'seg#142' | 'f0043'

    def line(self) -> str:
        return f"[{tc_short(self.t)} / {self.ref}] {self.kind}: {self.text}"


def _fragment(source_url: str | None, t: float) -> str:
    """Ссылка на момент. YouTube понимает ?t=, локальный файл — #t=."""
    sec = int(t)
    if source_url and ("youtube.com" in source_url or "youtu.be" in source_url):
        sep = "&" if "?" in source_url else "?"
        return f"{source_url}{sep}t={sec}"
    return f"{source_url or ''}#t={sec}"


def write(
    out_dir: str,
    *,
    info,
    selection,
    frames,
    transcript=None,
    source_url: str | None = None,
    title: str = "",
) -> dict:
    os.makedirs(out_dir, exist_ok=True)

    seg_path = os.path.join(out_dir, "segments.jsonl")
    n_segments = 0
    if transcript is not None:
        with open(seg_path, "w", encoding="utf-8") as fh:
            for s in transcript.segments:
                row = s.as_row()
                row["tc"] = tc(s.t0)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                n_segments += 1

    # Каждому кадру — реплика, звучавшая в этот момент. Это и есть сшивка.
    seg_lookup = list(transcript.segments) if transcript is not None else []

    def segment_at(t: float) -> int | None:
        for s in seg_lookup:
            if s.t0 <= t <= s.t1:
                return s.i
        return None

    frames_path = os.path.join(out_dir, "frames.jsonl")
    per_frame_tokens = 0
    with open(frames_path, "w", encoding="utf-8") as fh:
        for f in frames:
            per_frame_tokens = effective_tokens(f.width, f.height)
            fh.write(
                json.dumps(
                    {
                        "id": f.id,
                        "t": round(f.t, 3),
                        "tc": tc(f.t),
                        "path": os.path.relpath(f.path, out_dir),
                        "w": f.width,
                        "h": f.height,
                        "bytes": f.bytes,
                        "est_tokens": per_frame_tokens,
                        "reason": f.reason,
                        "segment_i": segment_at(f.t),
                        # Заполняется ПОСЛЕ того, как агент один раз посмотрел кадр.
                        # На второй вопрос про тот же момент картинка уже не грузится.
                        "caption": None,
                        "ocr": None,
                        "url": _fragment(source_url, f.t),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    index = {
        "schema_version": SCHEMA_VERSION,
        "video": {
            "title": title,
            "source_url": source_url,
            "duration_sec": round(info.duration, 3),
            "width": info.width,
            "height": info.height,
            "fps": round(info.fps, 3),
            "vfr": info.vfr,
        },
        "transcript": {
            "source": getattr(transcript, "source", None),
            "language": getattr(transcript, "language", None),
            "segment_count": n_segments,
            "file": "segments.jsonl",
        },
        "frames": {
            "count": len(frames),
            "caught_changes": len(frames) - selection.safety_count,
            "safety_fills": selection.safety_count,
            "dropped_duplicates": selection.dropped_duplicates,
            "dropped_budget": selection.dropped_budget,
            "est_tokens_per_frame": per_frame_tokens,
            "file": "frames.jsonl",
            "dir": "frames",
        },
        # Главное поле файла. Агент обязан прочитать его до любых утверждений об экране.
        "coverage": {
            "max_gap_target_sec": selection.max_gap_target,
            "actual_max_gap_sec": round(selection.actual_max_gap, 1),
            "ratio": round(selection.coverage, 4),
            "complete": not selection.gaps,
            "gaps": [
                {"from": round(a, 1), "to": round(b, 1), "tc": f"{tc_short(a)}–{tc_short(b)}"}
                for a, b in selection.gaps
            ],
        },
    }
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)

    # Журнал выдачи относится к ПРЕЖНЕМУ набору кадров. После переиндексации
    # идентификаторы означают другие моменты, и проверка «кадр не запрашивался»
    # начала бы врать в безопасную сторону — молча признавая всё чистым.
    served = os.path.join(out_dir, "served.jsonl")
    if os.path.exists(served):
        os.remove(served)

    build_search(out_dir)
    return index


def build_search(out_dir: str) -> str:
    """FTS5 с триграммами: подстрочный поиск, устойчивый к русским падежам."""
    db_path = os.path.join(out_dir, "search.sqlite3")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE VIRTUAL TABLE fts USING fts5("
            "kind UNINDEXED, t UNINDEXED, ref UNINDEXED, body, tokenize='trigram')"
        )
        rows: list[tuple[str, float, str, str]] = []
        seg_file = os.path.join(out_dir, "segments.jsonl")
        if os.path.exists(seg_file):
            with open(seg_file, encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    rows.append(("speech", r["t0"], f"seg#{r['i']}", r["text"]))
        fr_file = os.path.join(out_dir, "frames.jsonl")
        if os.path.exists(fr_file):
            with open(fr_file, encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    if r.get("ocr"):
                        rows.append(("screen", r["t"], r["id"], r["ocr"]))
        con.executemany("INSERT INTO fts (kind, t, ref, body) VALUES (?, ?, ?, ?)", rows)
        con.commit()
    finally:
        con.close()
    return db_path


#: Токенайзер trigram физически не умеет искать короче трёх символов.
TRIGRAM_MIN = 3


def _search_plain(out_dir: str, query: str, limit: int) -> list[Hit]:
    """Прямой проход по строкам — для запросов, которые триграммам не по зубам.

    Без этого `search "AI"` или `search "v2"` молча возвращали «не найдено»:
    FTS5 с токенайзером trigram на запросе короче трёх символов не находит ничего
    и об этом не сообщает. Для канала про ИИ запрос «AI» — не экзотика.
    """
    needle = query.lower().replace("ё", "е")
    hits: list[Hit] = []
    for name, kind, ref_key, text_key in (
        ("segments.jsonl", "speech", "i", "text"),
        ("frames.jsonl", "screen", "id", "ocr"),
    ):
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                body = row.get(text_key) or ""
                if needle not in body.lower().replace("ё", "е"):
                    continue
                ref = f"seg#{row['i']}" if kind == "speech" else row["id"]
                hits.append(Hit(kind=kind, t=float(row.get("t0", row.get("t", 0.0))),
                                text=body, ref=ref))
                if len(hits) >= limit:
                    return sorted(hits, key=lambda h: h.t)
    return sorted(hits, key=lambda h: h.t)


def search(out_dir: str, query: str, *, limit: int = 20) -> list[Hit]:
    query = query.strip()
    if len(query) < TRIGRAM_MIN:
        return _search_plain(out_dir, query, limit)

    db_path = os.path.join(out_dir, "search.sqlite3")
    if not os.path.exists(db_path):
        build_search(out_dir)
    con = sqlite3.connect(db_path)
    try:
        # Кавычка внутри запроса рвала строку FTS5 и роняла команду.
        safe = query.replace('"', '""')
        cur = con.execute(
            "SELECT kind, t, ref, body FROM fts WHERE fts MATCH ? ORDER BY t LIMIT ?",
            (f'"{safe}"', limit),
        )
        return [Hit(kind=k, t=float(t), text=b, ref=r) for k, t, r, b in cur.fetchall()]
    except sqlite3.OperationalError:
        return _search_plain(out_dir, query, limit)
    finally:
        con.close()


def load_index(out_dir: str) -> dict:
    with open(os.path.join(out_dir, "index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def frames_near(out_dir: str, t: float, *, count: int = 1) -> list[dict]:
    """Кадры, ближайшие к моменту. Единственный путь, отдающий картинки."""
    rows: list[dict] = []
    with open(os.path.join(out_dir, "frames.jsonl"), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    rows.sort(key=lambda r: abs(r["t"] - t))
    return sorted(rows[:count], key=lambda r: r["t"])


def frames_by_ids(out_dir: str, ids: list[str]) -> list[dict]:
    wanted = set(ids)
    out: list[dict] = []
    with open(os.path.join(out_dir, "frames.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["id"] in wanted:
                out.append(r)
    return out


def set_caption(out_dir: str, frame_id: str, caption: str) -> bool:
    """Записать описание кадра обратно в индекс.

    Смысл: агент посмотрел картинку один раз и оставил, что на ней. Следующий вопрос
    про тот же момент отвечается по тексту, без повторной загрузки изображения.
    """
    path = os.path.join(out_dir, "frames.jsonl")
    rows: list[dict] = []
    found = False
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["id"] == frame_id:
                r["caption"] = caption
                found = True
            rows.append(r)
    if found:
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return found
