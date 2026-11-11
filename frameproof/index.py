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
import hashlib
import os
import re
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
        # Заметка — слова агента о кадре, а не доказательство. Помечаем это прямо.
        kind = "note (заметка агента)" if self.kind == "note" else self.kind
        return f"[{tc_short(self.t)} / {self.ref}] {kind}: {self.text}"



def gaps_near_hits(index: dict, hits: list, *, near_sec: float = 180.0) -> dict:
    """Какие участки без кадров стоят рядом с найденным.

    Поиск отвечает на вопрос и молчит о том, где искать было НЕ ПО ЧЕМУ.
    Человек получает совпадение на 12:30, отвечает уверенно, а на 15:00
    двадцать минут без единого кадра, и там тот же разговор мог продолжиться
    с другим ответом. Инструмент это знает: разрывы посчитаны при индексации
    и лежат в coverage.gaps. Просто их никто не видит, потому что смотрят
    в search, а покрытие печатает report.

    Возвращает разрывы целиком и отдельно те, что ближе near_sec к любому
    совпадению: близкий разрыв это не общая статистика, а прямая оговорка
    к конкретному ответу.

    Чистая функция: ничего не печатает, индекс не меняет.
    """
    разрывы = (index.get("coverage") or {}).get("gaps") or []
    if not разрывы or not hits:
        return {"all": разрывы, "near": [], "nearest_sec": None}

    моменты = [h.t for h in hits]
    рядом, ближайший = [], None
    for g in разрывы:
        a, b = float(g["from"]), float(g["to"])
        # Расстояние от совпадения до разрыва: ноль, если совпадение внутри.
        d = min(0.0 if a <= t <= b else min(abs(t - a), abs(t - b)) for t in моменты)
        if ближайший is None or d < ближайший:
            ближайший = d
        if d <= near_sec:
            рядом.append(dict(g, distance_sec=round(d, 1)))

    рядом.sort(key=lambda g: g["distance_sec"])
    return {"all": разрывы, "near": рядом,
            "nearest_sec": None if ближайший is None else round(ближайший, 1)}

def _fragment(source_url: str | None, t: float) -> str:
    """Ссылка на момент. YouTube понимает ?t=, локальный файл — #t=."""
    sec = int(t)
    if source_url and ("youtube.com" in source_url or "youtu.be" in source_url):
        sep = "&" if "?" in source_url else "?"
        return f"{source_url}{sep}t={sec}"
    return f"{source_url or ''}#t={sec}"



def frame_row(f, *, out_dir: str, source_url: str | None, segment_i: int | None) -> dict:
    """Строка frames.jsonl для снятого кадра. Одна форма для индекса и для --exact."""
    return {
        "id": f.id,
        "t": round(f.t, 3),
        "tc": tc(f.t),
        "path": os.path.relpath(f.path, out_dir),
        "w": f.width,
        "h": f.height,
        "bytes": f.bytes,
        "est_tokens": effective_tokens(f.width, f.height),
        "reason": f.reason,
        "segment_i": segment_i,
        # Заметка агента о кадре: пишется командой `caption` после просмотра.
        # На второй вопрос про тот же момент картинка уже не грузится.
        "caption": None,
        "ocr": None,
        "url": _fragment(source_url, f.t),
    }


def build_fingerprint(*, duration: float, frame_count: int, source: str,
                      times: list[float] | None = None) -> str:
    """Короткий отпечаток сборки индекса.

    Зачем. Идентификатор кадра — порядковый номер ВНУТРИ сборки (`f0084`), и при
    пересборке с другими параметрами тот же номер указывает на другой момент.
    Отзыв с живого использования: индекс на 97 кадров дал `f0084 = 13:27`,
    индекс на 150 кадров — `f0084 = 5:16`. Разбор, написанный по первому,
    после пересборки показывает не туда, а `verify` называл это TIME_MISMATCH,
    то есть фактически обвинял автора во лжи.

    Отпечаток даёт `verify` возможность сказать правду: разбор писался по другой
    сборке. Берём то, что задаёт нарезку: длительность, число кадров и источник.
    """
    сырьё = f"{duration:.3f}|{frame_count}|{os.path.basename(source or '')}"
    # Моменты кадров — то, что на деле задаёт нумерацию. Без них две сборки,
    # упёршиеся в один потолок кадров, получали один отпечаток при разных моментах.
    if times is not None:
        сырьё += "|" + ",".join(f"{t:.3f}" for t in times)
    return "idx_" + hashlib.sha256(сырьё.encode("utf-8")).hexdigest()[:8]


def write(
    out_dir: str,
    *,
    info,
    selection,
    frames,
    transcript=None,
    source_url: str | None = None,
    title: str = "",
    window: tuple[float, float] | None = None,
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
            row = frame_row(f, out_dir=out_dir, source_url=source_url,
                            segment_i=segment_at(f.t))
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    index = {
        "schema_version": SCHEMA_VERSION,
        "index_id": build_fingerprint(
            duration=info.duration, frame_count=len(frames), source=source_url or title or "",
            times=[f.t for f in frames]),
        "video": {
            "title": title,
            "source_url": source_url,
            # Откуда снимать точный кадр по запросу (`frames --exact`).
            "path": os.path.abspath(info.path),
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
    if window is not None:
        # Индекс по куску ролика: вне окна кадров нет, verify это знает.
        index["window"] = {"from": round(window[0], 3), "to": round(window[1], 3)}
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


def _fold(text: str) -> str:
    """Нижний регистр и ё→е: так хранится и так ищется."""
    return " ".join(str(text).lower().replace("ё", "е").split())


#: Окончания, которые срезаются у слов запроса: «таблица» должна находить
#: «таблицу», «маршрутизации» — «маршрутизация». Это не морфология, а грубая
#: основа: поиск всё равно подстрочный, лишняя буква основы стоит промаха.
_RU_ENDINGS = ("ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях",
               "ов", "ев", "ей", "ой", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие",
               "ую", "юю", "ом", "ем", "ам", "ям", "а", "я", "ы", "и", "у", "ю", "е",
               "о", "ь")
_EN_ENDINGS = ("ing", "ed", "es", "s")
#: Короче этого основа не обрезается: из «кот» вышло бы «ко».
_STEM_MIN = 4


def _stem(word: str) -> str:
    endings = _RU_ENDINGS if re.search("[а-я]", word) else _EN_ENDINGS
    for end in endings:
        if word.endswith(end) and len(word) - len(end) >= _STEM_MIN:
            return word[: -len(end)]
    return word


def _query_terms(query: str) -> list[str]:
    """Слова запроса в виде основ. Порядок слов не важен, важно наличие всех."""
    return [_stem(w) for w in re.findall(r"[\w.:/+#-]+", _fold(query)) if w.strip(".-")]


def build_search(out_dir: str) -> str:
    """FTS5 с триграммами: подстрочный поиск, устойчивый к русским падежам.

    Тело хранится сложенным (нижний регистр, ё→е), а показывается исходный текст.
    """
    db_path = os.path.join(out_dir, "search.sqlite3")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE VIRTUAL TABLE fts USING fts5("
            "kind UNINDEXED, t UNINDEXED, ref UNINDEXED, shown UNINDEXED, body, "
            "tokenize='trigram')"
        )
        rows = [(k, t, r, text, _fold(text)) for k, t, r, text in _rows(out_dir)]
        con.executemany(
            "INSERT INTO fts (kind, t, ref, shown, body) VALUES (?, ?, ?, ?, ?)", rows)
        con.commit()
    finally:
        con.close()
    return db_path


def _rows(out_dir: str):
    """Всё, по чему ищем: реплики и текст кадров. Один источник для FTS и запасного пути."""
    seg_file = os.path.join(out_dir, "segments.jsonl")
    if os.path.exists(seg_file):
        with open(seg_file, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    yield "speech", float(r["t0"]), f"seg#{r['i']}", r["text"]
    fr_file = os.path.join(out_dir, "frames.jsonl")
    if os.path.exists(fr_file):
        with open(fr_file, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    if r.get("ocr"):
                        yield "screen", float(r["t"]), r["id"], r["ocr"]
                    if r.get("caption"):
                        yield "note", float(r["t"]), r["id"], r["caption"]


#: Токенайзер trigram физически не умеет искать короче трёх символов.
TRIGRAM_MIN = 3


def _search_plain(out_dir: str, terms: list[str], limit: int) -> tuple[list[Hit], int]:
    """Прямой проход по строкам — для слов, которые триграммам не по зубам.

    Без этого `search "AI"` или `search "v2"` молча возвращали «не найдено».
    Совпадения собираются ВСЕ и только потом режутся по времени: раньше лимит
    набирался речью, и до текста с экрана дело не доходило.
    """
    hits = [Hit(kind=k, t=t, text=text, ref=r)
            for k, t, r, text in _rows(out_dir)
            if all(term in _fold(text) for term in terms)]
    hits.sort(key=lambda h: h.t)
    return hits[:limit], len(hits)


def search_with_total(out_dir: str, query: str, *, limit: int = 20) -> tuple[list[Hit], int]:
    """Совпадения по времени и общее их число: агент должен знать, что видит не всё."""
    terms = _query_terms(query)
    if not terms:
        return [], 0
    if any(len(t) < TRIGRAM_MIN for t in terms):
        return _search_plain(out_dir, terms, limit)

    db_path = os.path.join(out_dir, "search.sqlite3")
    if not os.path.exists(db_path):
        build_search(out_dir)
    # Каждое слово — отдельная подстрока, все через AND. Кавычка внутри слова
    # удваивается: иначе она рвала строку FTS5 и роняла команду.
    match = " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT kind, t, ref, shown FROM fts WHERE fts MATCH ? ORDER BY t LIMIT ?",
            (match, limit),
        )
        hits = [Hit(kind=k, t=float(t), text=b, ref=r) for k, t, r, b in cur.fetchall()]
        total = con.execute("SELECT count(*) FROM fts WHERE fts MATCH ?", (match,)).fetchone()[0]
        return hits, int(total)
    except sqlite3.OperationalError:
        # Старый индекс без колонки shown или иная схема — честный проход по строкам.
        return _search_plain(out_dir, terms, limit)
    finally:
        con.close()


def search(out_dir: str, query: str, *, limit: int = 20) -> list[Hit]:
    return search_with_total(out_dir, query, limit=limit)[0]


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
