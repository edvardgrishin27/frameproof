"""Механический аудит утверждений — без единого обращения к модели.

Метка `[MM:SS / fNNNN]`, которую скилл требует ставить у каждого утверждения об экране,
перестаёт быть украшением. Это ссылка на конкретную строку индекса, и её можно проверить
арифметикой: существует ли такой кадр, тот ли у него тайм-код, выдавался ли он агенту
вообще, встречается ли процитированная строка в его OCR-слое.

Что здесь НЕ проверяется: смысл. Инструмент подтверждает целостность указателя, а не
истинность прозы. Утверждение «на 18:38 показана таблица маршрутизации» может быть
осмысленно неверным при идеальной метке — за это отвечает слой 2, слепой проверяющий.

Идея механической проверки цитат не наша: `frizfealer/ground-check` делает ровно это
для кода (`file:line`, включая «файл существует, но сессия его не открывала»), а
`oxbshw/watch-skill` вырезает тайм-коды, не подтверждённые доказательствами. Здесь
эти проверки собраны поверх видео-индекса и работают по каждому утверждению отдельно.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .util import parse_tc, tc_short

#: Насколько тайм-код в метке может расходиться с реальным моментом кадра.
#: Кадры стоят не чаще чем раз в MIN_GAP=0.75 с, так что 2 с — это заведомо «тот же кадр».
TIME_TOLERANCE = 2.0

#: Доля слов цитаты, которую надо найти в OCR, чтобы счесть её подтверждённой.
#: Не 100 %: распознавание системно путает пунктуацию и отдельные буквы.
QUOTE_OVERLAP = 0.6

#: Цитаты короче этого не проверяем — на коротком куске совпадения случайны.
QUOTE_MIN_WORDS = 2

FAIL = "FAIL"
WARN = "WARN"
OK = "OK"

#: [4:12 / f0043] — утверждение об экране с привязкой к кадру.
_TAG_FRAME = re.compile(r"\[\s*(\d{1,2}(?::\d{2}){1,2})\s*/\s*(f\d{3,})\s*\]")
#: [4:12] — утверждение о моменте без кадра (обычно про речь).
_TAG_TIME = re.compile(r"\[\s*(\d{1,2}(?::\d{2}){1,2})\s*\]")

#: Цитаты: «ёлочки», "кавычки", `бэктики`.
_QUOTES = re.compile(r"[«\"']([^«»\"']{3,200})[»\"']|`([^`]{3,200})`")

_WORD = re.compile(r"[\w./:-]+", re.UNICODE)


@dataclass
class Finding:
    code: str
    severity: str
    detail: str


@dataclass
class Claim:
    n: int
    text: str
    t: float
    frame_id: str | None
    line_no: int
    quotes: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if any(f.severity == FAIL for f in self.findings):
            return FAIL
        if any(f.severity == WARN for f in self.findings):
            return WARN
        return OK

    @property
    def needs_second_look(self) -> bool:
        """Механика молчит, а утверждение визуальное — тут и нужен слепой проверяющий."""
        return self.severity != FAIL and self.frame_id is not None


def _normalize(text: str) -> str:
    return " ".join(str(text).lower().replace("ё", "е").split())


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(_normalize(text)) if len(w) > 1]


def _quote_supported(quote: str, haystack: str) -> bool:
    """Снисходительная сверка: точное вхождение либо достаточное перекрытие слов.

    Строгое сравнение здесь вредно. OCR путает пунктуацию (`[main` читается как `Imain`),
    а модель при пересказе меняет регистр и пробелы. Задача проверки — поймать выдуманную
    цитату, а не придраться к скобке.
    """
    if not haystack:
        return False
    q_norm, h_norm = _normalize(quote), _normalize(haystack)
    if q_norm in h_norm:
        return True
    q_words = _words(quote)
    if len(q_words) < QUOTE_MIN_WORDS:
        return True          # слишком короткая цитата — не судим
    h_words = set(_words(haystack))
    hit = sum(1 for w in q_words if w in h_words)
    return hit / len(q_words) >= QUOTE_OVERLAP


def _claim_text(line: str) -> str:
    """Текст утверждения — строка без метки, маркеров списка и служебной разметки."""
    text = _TAG_FRAME.sub("", line)
    text = _TAG_TIME.sub("", text)
    text = re.sub(r"^\s*[-*+>]\s*|^\s*\d+[.)]\s*|^#+\s*", "", text)
    return text.strip(" \t—–-·:")


#: Начало нового логического блока: маркер списка, нумерация, заголовок, цитата, строка таблицы.
_BLOCK_START = re.compile(r"^\s*(?:[-*+>]\s|\d+[.)]\s|#{1,6}\s|\|)")


def _logical_lines(answer: str) -> list[tuple[int, str]]:
    """Склеивает перенесённые строки в логические.

    В markdown утверждение часто занимает две-три строки, а метка стоит на последней.
    Без склейки текстом утверждения оказывался обрывок вроде «и быстрая», и человек
    в отчёте не понимал, о чём вообще речь.
    """
    out: list[tuple[int, str]] = []
    for n, raw in enumerate(answer.splitlines(), 1):
        if not raw.strip():
            out.append((n, ""))
            continue
        if out and out[-1][1] and not _BLOCK_START.match(raw):
            out[-1] = (out[-1][0], f"{out[-1][1]} {raw.strip()}")
        else:
            out.append((n, raw))
    return [(n, text) for n, text in out if text.strip()]


def extract_claims(answer: str) -> list[Claim]:
    """Достаёт утверждения из разбора по обязательным меткам."""
    claims: list[Claim] = []
    for line_no, line in _logical_lines(answer):
        seen_spans: list[tuple[int, int]] = []
        for m in _TAG_FRAME.finditer(line):
            seen_spans.append(m.span())
            claims.append(
                Claim(
                    n=len(claims) + 1,
                    text=_claim_text(line),
                    t=parse_tc(m.group(1)),
                    frame_id=m.group(2),
                    line_no=line_no,
                )
            )
        for m in _TAG_TIME.finditer(line):
            # Не считать дважды кусок «[4:12 / f0043]», внутри которого тоже есть время.
            if any(a <= m.start() < b for a, b in seen_spans):
                continue
            claims.append(
                Claim(
                    n=len(claims) + 1,
                    text=_claim_text(line),
                    t=parse_tc(m.group(1)),
                    frame_id=None,
                    line_no=line_no,
                )
            )
    for c in claims:
        c.quotes = [q for pair in _QUOTES.findall(c.text) for q in pair if q]
    return claims


def _load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def audit(answer: str, index_dir: str) -> list[Claim]:
    """Проверяет каждое утверждение против индекса. Модель не вызывается."""
    with open(os.path.join(index_dir, "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    frames = {r["id"]: r for r in _load_jsonl(os.path.join(index_dir, "frames.jsonl"))}
    segments = _load_jsonl(os.path.join(index_dir, "segments.jsonl"))
    served = {r["id"] for r in _load_jsonl(os.path.join(index_dir, "served.jsonl"))}
    gaps = index.get("coverage", {}).get("gaps", [])

    claims = extract_claims(answer)
    for c in claims:
        frame = frames.get(c.frame_id) if c.frame_id else None

        if c.frame_id and frame is None:
            c.findings.append(Finding(
                "FRAME_NOT_FOUND", FAIL,
                f"кадра {c.frame_id} в индексе нет — ссылка выдумана",
            ))

        if frame is not None and abs(frame["t"] - c.t) > TIME_TOLERANCE:
            c.findings.append(Finding(
                "TIME_MISMATCH", FAIL,
                f"метка говорит {tc_short(c.t)}, а кадр {c.frame_id} снят "
                f"в {tc_short(frame['t'])}",
            ))

        in_gap = next((g for g in gaps if g["from"] <= c.t <= g["to"]), None)
        if in_gap is not None:
            c.findings.append(Finding(
                "IN_COVERAGE_GAP", FAIL,
                f"момент попал в участок {in_gap['tc']}, откуда кадров не извлекалось",
            ))

        if frame is not None and c.frame_id not in served:
            c.findings.append(Finding(
                "NEVER_OPENED", WARN,
                f"кадр {c.frame_id} существует, но ни разу не запрашивался — "
                f"утверждение сделано, не посмотрев",
            ))

        for quote in c.quotes:
            ocr = (frame or {}).get("ocr") or ""
            near = " ".join(
                s["text"] for s in segments
                if s["t0"] - 8.0 <= c.t <= s["t1"] + 8.0
            )
            on_screen = _quote_supported(quote, ocr)
            in_speech = _quote_supported(quote, near)
            if on_screen or in_speech:
                continue
            if frame is not None and ocr:
                c.findings.append(Finding(
                    "QUOTE_NOT_ON_SCREEN", WARN,
                    f"цитаты «{quote[:60]}» нет ни в тексте кадра, ни в речи рядом",
                ))
            elif near:
                c.findings.append(Finding(
                    "QUOTE_NOT_IN_SPEECH", WARN,
                    f"цитаты «{quote[:60]}» нет в транскрипте рядом с этим моментом",
                ))
    return claims


def plan_second_look(claims: list[Claim], index_dir: str, *, limit: int = 8) -> list[dict]:
    """Задания для слепого проверяющего.

    Отдаём ТОЛЬКО текст утверждения и путь к кадру. Ни вопроса пользователя, ни
    рассуждений автора: проверяющий должен смотреть на пиксели, а не угадывать,
    какой ответ от него ждут.
    """
    frames = {r["id"]: r for r in _load_jsonl(os.path.join(index_dir, "frames.jsonl"))}
    tasks: list[dict] = []
    for c in claims:
        if not c.needs_second_look or len(tasks) >= limit:
            continue
        row = frames.get(c.frame_id)
        if not row:
            continue
        tasks.append({
            "claim_id": c.n,
            "claim_text": c.text,
            "frame_path": os.path.join(index_dir, row["path"]),
            "frame_id": c.frame_id,
        })
    return tasks


def render(claims: list[Claim], *, index_dir: str = "") -> str:
    if not claims:
        return (
            "Меток [MM:SS / fNNNN] в разборе не найдено — проверять нечего.\n"
            "Скилл требует помечать каждое утверждение об экране; без меток разбор "
            "непроверяем в принципе."
        )
    lines = ["АУДИТ УТВЕРЖДЕНИЙ", "─" * 17]
    mark = {OK: "✓", WARN: "?", FAIL: "✗"}
    for c in claims:
        ref = f"{tc_short(c.t)}" + (f" / {c.frame_id}" if c.frame_id else "")
        lines.append(f"{mark[c.severity]} [{ref}] {c.text[:100]}")
        for f in c.findings:
            lines.append(f"      {f.severity}  {f.code}: {f.detail}")
    fails = sum(1 for c in claims if c.severity == FAIL)
    warns = sum(1 for c in claims if c.severity == WARN)
    lines += [
        "",
        f"утверждений: {len(claims)}   провалов: {fails}   вопросов: {warns}",
    ]
    if fails:
        lines.append("Провалы — это сломанная ссылка, а не спорное мнение. Их надо чинить.")
    if warns:
        lines.append("Вопросы — повод посмотреть кадр глазами, а не приговор.")
    remaining = sum(1 for c in claims if c.needs_second_look)
    if remaining:
        lines += [
            "",
            f"механика молчит по {remaining} утверждениям — их смысл она проверить не может.",
            f"Слепой второй взгляд:  frameproof verify <файл> --out {index_dir or '<индекс>'} --plan",
        ]
    return "\n".join(lines)
