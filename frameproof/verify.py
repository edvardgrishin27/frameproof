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
from difflib import SequenceMatcher

from .util import parse_tc, plural, tc_short

#: Насколько тайм-код в метке может расходиться с реальным моментом кадра.
#: Кадры стоят не чаще чем раз в MIN_GAP=0.75 с, так что 2 с — это заведомо «тот же кадр».
TIME_TOLERANCE = 2.0

#: Доля слов цитаты, которую надо найти в OCR, чтобы счесть её подтверждённой.
#: Не 100 %: распознавание системно путает пунктуацию и отдельные буквы.
QUOTE_OVERLAP = 0.6

#: Слово такой длины и длиннее, которого нет в тексте даже с опечаткой, — выдумка,
#: какая бы доля остальных слов ни совпала. `class Dragon(Reptile)` при
#: `class Snake(Reptile)` давал два слова из трёх и проходил по доле.
DISTINCT_WORD = 4

#: С какой длины слово сверяется нечётко и насколько похожим оно должно быть.
#: OCR путает отдельные буквы (`rn`↔`m`, `l`↔`I`), и одна буква мимо в длинном
#: слове — ошибка распознавания, а не выдумка автора.
FUZZY_MIN_LEN = 5
FUZZY_RATIO = 0.8

FAIL = "FAIL"
WARN = "WARN"
OK = "OK"

#: [4:12 / f0043] — утверждение об экране с привязкой к кадру.
_TAG_FRAME = re.compile(r"\[\s*(\d{1,3}(?::\d{2}){1,2})\s*/\s*(f\d{3,})\s*\]")
#: [4:12] — утверждение о моменте без кадра (обычно про речь).
_TAG_TIME = re.compile(r"\[\s*(\d{1,3}(?::\d{2}){1,2})\s*\]")

#: Цитаты: «ёлочки», "кавычки", 'одинарные', `бэктики`. Одинарная кавычка считается
#: только на границе слова: иначе английский апостроф (`it's ... don't`) давал
#: ложную «цитату» `s the config, don`.
_QUOTES = re.compile(
    r"«([^«»\n]{2,200})»"
    r"|\"([^\"\n]{2,200})\""
    r"|(?<!\w)'([^'\n]{2,200})'(?!\w)"
    r"|`([^`\n]{2,200})`"
)

#: Слова, по которым строка без метки похожа на утверждение об экране.
_SCREEN_WORDS = (
    "на экран", "на слайд", "на кадр", "на скрин", "показан", "показыва", "видно",
    "открыт", "в терминал", "в интерфейс", "в окне", "кнопк", "на странице",
    "on screen", "on the screen", "the slide", "is shown", "shows", "displayed",
    "in the terminal", "the window", "button",
)

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
    #: Тайм-код так, как его написал автор. Нужен, когда прочитать его нельзя.
    tc_raw: str = ""
    #: Строка похожа на утверждение об экране, но метки у неё нет.
    untagged: bool = False
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
    def has_time(self) -> bool:
        """Есть ли у утверждения момент, по которому вообще можно что-то сверять."""
        return not self.untagged and not any(f.code == "BAD_TIMECODE" for f in self.findings)

    @property
    def needs_second_look(self) -> bool:
        """Механика молчит, а утверждение визуальное — тут и нужен слепой проверяющий."""
        return self.severity != FAIL and self.frame_id is not None


def _normalize(text: str) -> str:
    return " ".join(str(text).lower().replace("ё", "е").split())


def _words(text: str) -> list[str]:
    out = (w.strip(".,;:!?()[]{}\"'/") for w in _WORD.findall(_normalize(text)))
    return [w for w in out if len(w) > 1]


def _word_found(word: str, pool: set[str]) -> bool:
    """Слово есть в тексте — точно или с одной-двумя буквами мимо (ошибка OCR)."""
    if word in pool:
        return True
    if len(word) < FUZZY_MIN_LEN:
        return False
    return any(
        abs(len(w) - len(word)) <= 2 and SequenceMatcher(None, word, w).ratio() >= FUZZY_RATIO
        for w in pool
    )


def _quote_supported(quote: str, haystack: str) -> bool:
    """Сверка цитаты: строгая к выдумке, снисходительная к ошибкам OCR.

    Раньше цитата короче двух слов «не судилась» и проходила как подтверждённая,
    а длинная проходила по доле совпавших слов, даже если подменено главное имя.
    Теперь: точное вхождение — да; иначе каждое заметное слово цитаты должно
    найтись в тексте хотя бы с опечаткой, и общая доля найденных — не ниже порога.
    """
    if not haystack:
        return False
    q_norm, h_norm = _normalize(quote), _normalize(haystack)
    if q_norm in h_norm:
        return True
    q_words = _words(quote)
    if not q_words:
        return True          # в цитате одни знаки — судить не по чему
    pool = set(_words(haystack))
    found = [_word_found(w, pool) for w in q_words]
    if any(not ok and len(w) >= DISTINCT_WORD for w, ok in zip(q_words, found)):
        return False
    return sum(found) / len(q_words) >= QUOTE_OVERLAP


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


#: Метка сборки в разборе: строка «Индекс: idx_xxxxxxxx» где угодно в тексте.
#: Скилл ставит её в шапку, но искать по всему тексту дешевле, чем требовать место.
_МЕТКА_ИНДЕКСА = re.compile(r"\b(idx_[0-9a-f]{8})\b")


def extract_index_id(text: str) -> str | None:
    """Отпечаток сборки, по которой писался разбор, если автор его указал.

    Старые разборы метки не несут, и это не ошибка: возвращаем None и проверяем
    всё остальное как раньше.
    """
    m = _МЕТКА_ИНДЕКСА.search(text or "")
    return m.group(1) if m else None


def _new_claim(n: int, line: str, raw_tc: str, frame_id: str | None, line_no: int) -> Claim:
    """Утверждение по метке. Нечитаемый тайм-код — провал этой метки, а не всего аудита.

    Раньше `parse_tc` бросал ValueError прямо отсюда, и одна опечатка вроде
    `[1:75 / f0020]` обрывала `verify`: остальные утверждения не проверялись.
    """
    try:
        t, bad = parse_tc(raw_tc), None
    except ValueError as exc:
        t, bad = 0.0, str(exc)
    claim = Claim(n=n, text=_claim_text(line), t=t, frame_id=frame_id,
                  line_no=line_no, tc_raw=raw_tc)
    if bad:
        claim.findings.append(Finding("BAD_TIMECODE", FAIL, bad))
    return claim


def _looks_like_screen(line: str) -> bool:
    """Строка без метки, которая по словам говорит, что было на экране."""
    if re.match(r"^\s*#", line) or _МЕТКА_ИНДЕКСА.search(line):
        return False
    low = _normalize(line)
    return any(w in low for w in _SCREEN_WORDS)


def extract_claims(answer: str) -> list[Claim]:
    """Достаёт утверждения из разбора по обязательным меткам."""
    claims: list[Claim] = []
    for line_no, line in _logical_lines(answer):
        seen_spans: list[tuple[int, int]] = []
        for m in _TAG_FRAME.finditer(line):
            seen_spans.append(m.span())
            claims.append(_new_claim(len(claims) + 1, line, m.group(1), m.group(2), line_no))
        for m in _TAG_TIME.finditer(line):
            # Не считать дважды кусок «[4:12 / f0043]», внутри которого тоже есть время.
            if any(a <= m.start() < b for a, b in seen_spans):
                continue
            claims.append(_new_claim(len(claims) + 1, line, m.group(1), None, line_no))
        if not seen_spans and not _TAG_TIME.search(line) and _looks_like_screen(line):
            c = Claim(n=len(claims) + 1, text=_claim_text(line), t=0.0, frame_id=None,
                      line_no=line_no, untagged=True)
            c.findings.append(Finding(
                "UNTAGGED", WARN,
                "строка про экран без метки [MM:SS / fNNNN] — проверить её нельзя. "
                "Поставь метку кадра или скажи, что это по звуку"))
            claims.append(c)
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

    # Сборку разбора сверяем ДО остальных проверок: если она чужая, номера кадров
    # указывают в другие моменты, и все TIME_MISMATCH ниже — следствие, а не причина.
    заявленный = extract_index_id(answer)
    текущий = index.get("index_id")
    чужой_индекс = bool(заявленный and текущий and заявленный != текущий)

    claims = extract_claims(answer)
    if чужой_индекс and claims:
        claims[0].findings.append(Finding(
            "INDEX_MISMATCH", FAIL,
            f"разбор писался по сборке {заявленный}, а проверяется против {текущий}. "
            f"Номера кадров в разных сборках указывают на разные моменты, поэтому "
            f"расхождения времени ниже ожидаемы. Пересоберите разбор или возьмите "
            f"ту сборку индекса, по которой он написан",
        ))
    for c in claims:
        frame = frames.get(c.frame_id) if c.frame_id else None
        bad_tc = not c.has_time
        if c.untagged:
            continue

        if c.frame_id and frame is None:
            c.findings.append(Finding(
                "FRAME_NOT_FOUND", FAIL,
                f"кадра {c.frame_id} в индексе нет — ссылка выдумана",
            ))

        if frame is not None and not bad_tc and abs(frame["t"] - c.t) > TIME_TOLERANCE:
            хвост = (" — но индекс другой, см. INDEX_MISMATCH выше"
                     if чужой_индекс else "")
            c.findings.append(Finding(
                "TIME_MISMATCH", WARN if чужой_индекс else FAIL,
                f"метка говорит {tc_short(c.t)}, а кадр {c.frame_id} снят "
                f"в {tc_short(frame['t'])}{хвост}",
            ))

        window = index.get("window")
        if window and not (window["from"] - TIME_TOLERANCE <= c.t <= window["to"] + TIME_TOLERANCE):
            c.findings.append(Finding(
                "OUTSIDE_WINDOW", FAIL,
                f"индекс собран по окну {tc_short(window['from'])}–{tc_short(window['to'])}, "
                f"а момент {tc_short(c.t)} вне его: кадров там нет вовсе",
            ))
        in_gap = None if bad_tc else next(
            (g for g in gaps if g["from"] <= c.t <= g["to"]), None)
        if in_gap is not None:
            c.findings.append(Finding(
                "IN_COVERAGE_GAP", FAIL,
                f"момент попал в участок {in_gap['tc']}, откуда кадров не извлекалось",
            ))

        if frame is not None and c.frame_id not in served:
            c.findings.append(Finding(
                "NEVER_OPENED", WARN,
                f"кадр {c.frame_id} существует, но команда frames его ни разу не "
                f"выдавала — похоже, утверждение сделано не глядя",
            ))

        # Время не прочитано — сверять цитату с речью «рядом» не с чем.
        for quote in ([] if bad_tc else c.quotes):
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
            else:
                # Раньше здесь не писалось ничего, и вердикт выходил ✓: цитату
                # «подтверждало» отсутствие любых данных.
                c.findings.append(Finding(
                    "QUOTE_UNCHECKED", WARN,
                    f"цитату «{quote[:60]}» проверить нечем: у кадра нет текста OCR "
                    f"(индекс собран без --ocr?) и рядом нет речи",
                ))
    return claims


def plan_second_look(claims: list[Claim], index_dir: str, *, limit: int = 8) -> list[dict]:
    """Задания для слепого проверяющего.

    Отдаём ТОЛЬКО текст утверждения и путь к кадру. Ни вопроса пользователя, ни
    рассуждений автора: проверяющий должен смотреть на пиксели, а не угадывать,
    какой ответ от него ждут.
    """
    frames = {r["id"]: r for r in _load_jsonl(os.path.join(index_dir, "frames.jsonl"))}
    # Сначала сомнительные: на них второй взгляд нужнее всего. Раньше брались
    # первые по порядку, и при потолке 8 вопросы из конца разбора не попадали.
    order = sorted((c for c in claims if c.needs_second_look),
                   key=lambda c: (c.severity != WARN, c.n))
    tasks: list[dict] = []
    for c in order:
        if len(tasks) >= limit:
            break
        row = frames.get(c.frame_id)
        if not row:
            continue
        tasks.append({
            "claim_id": c.n,
            "claim_text": c.text,
            # Абсолютный путь: проверяющий читает файл через Read, а тому нужен
            # полный путь, относительный от папки агента не работает.
            "frame_path": os.path.abspath(os.path.join(index_dir, row["path"])),
            "frame_id": c.frame_id,
        })
    return sorted(tasks, key=lambda t: t["claim_id"])


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
        if c.untagged:
            ref = "без метки"
        else:
            ref = (tc_short(c.t) if c.has_time else c.tc_raw) + (
                f" / {c.frame_id}" if c.frame_id else "")
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
    untagged = sum(1 for c in claims if c.untagged)
    if untagged:
        lines.append(
            f"строк про экран без метки: {untagged}. Их нельзя проверить ни механикой, "
            "ни вторым взглядом — поставь метку [MM:SS / fNNNN].")
    remaining = sum(1 for c in claims if c.needs_second_look)
    if remaining:
        lines += [
            "",
            f"механика молчит по {remaining} "
            f"{plural(remaining, 'утверждению', 'утверждениям', 'утверждениям')}: "
            "смысл она проверить не может.",
            f"Слепой второй взгляд:  frameproof verify <файл> --out {index_dir or '<индекс>'} --plan",
        ]
    return "\n".join(lines)
