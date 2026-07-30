"""Отчёт покрытия — то, чего нет ни у одного аналога.

Смысл простой: агент не должен рассуждать об экране там, где кадра не было.
Инструмент обязан сказать вслух, что именно он видел, а что пропустил.
Молчаливая слепота хуже честного «здесь я не смотрел».
"""

from __future__ import annotations

from .budget import DEFAULT_FRAMES_PER_CALL, effective_tokens
from .select import Selection
from .util import tc_short


def coverage_lines(sel: Selection, *, frame_w: int = 0, frame_h: int = 0) -> list[str]:
    lines: list[str] = []
    total = len(sel.picks)
    caught = total - sel.safety_count

    lines.append(f"кадров: {total}  (переходы: {caught}, страховка покрытия: {sel.safety_count})")
    lines.append(
        f"хронометраж: {tc_short(sel.duration)}   "
        f"1 кадр на {sel.duration / total:.1f} с" if total else "хронометраж: —"
    )

    if not sel.gaps:
        lines.append(
            f"покрытие: 100 % — ни одного промежутка длиннее {sel.max_gap_target:.0f} с. "
            f"Максимальный разрыв {sel.actual_max_gap:.1f} с."
        )
    else:
        uncovered = sum(b - a for a, b in sel.gaps)
        lines.append(
            f"покрытие: {sel.coverage * 100:.0f} % — "
            f"{len(sel.gaps)} участ{'ок' if len(sel.gaps) == 1 else 'ка/ов'} "
            f"без кадров ({uncovered:.0f} с). НЕ утверждай, что показано на экране в них."
        )
        for a, b in sel.gaps[:8]:
            lines.append(f"    БЕЗ КАДРА  {tc_short(a)} – {tc_short(b)}   ({b - a:.0f} с)")
        if len(sel.gaps) > 8:
            lines.append(f"    ... и ещё {len(sel.gaps) - 8}")

    if sel.dropped_duplicates:
        lines.append(f"схлопнуто дублей: {sel.dropped_duplicates}")
    if sel.dropped_budget:
        lines.append(
            f"прорежено бюджетом: {sel.dropped_budget} "
            f"(самые избыточные, гарантия покрытия сохранена)"
        )

    if sel.cap_exceeded:
        lines.append(
            f"потолок кадров {sel.cap} не выдержан: чтобы закрыть таймлайн без разрывов, "
            f"нужно {len(sel.picks)}. Ужиматься дальше значило бы ослепнуть."
        )

    if frame_w and frame_h:
        per = effective_tokens(frame_w, frame_h)
        lines.append(
            f"токены: {per} на кадр ({frame_w}x{frame_h}), "
            f"{per * DEFAULT_FRAMES_PER_CALL} за показ {DEFAULT_FRAMES_PER_CALL} кадров, "
            f"{per * total} если показать все"
        )
    return lines


def render(sel: Selection, *, title: str = "", frame_w: int = 0, frame_h: int = 0) -> str:
    head = f"ОТЧЁТ ПОКРЫТИЯ{' · ' + title if title else ''}"
    body = coverage_lines(sel, frame_w=frame_w, frame_h=frame_h)
    return "\n".join([head, "─" * len(head), *body])


def compare(before: list[float], after: Selection, duration: float, *,
            label_before: str = "наивный порог сцен", label_after: str = "frameproof") -> str:
    """Таблица «до/после» на одном и том же материале."""

    def stats(times: list[float]) -> tuple[int, float, float]:
        if not times:
            return 0, duration, duration
        edges = [0.0] + sorted(times) + [duration]
        gaps = [b - a for a, b in zip(edges, edges[1:])]
        return len(times), max(gaps), sum(g for g in gaps if g > 30.0)

    n_b, max_b, blind_b = stats(before)
    n_a, max_a, blind_a = stats(after.times)

    rows = [
        ("кадров", f"{n_b}", f"{n_a}"),
        ("максимальный разрыв", f"{max_b:.0f} с", f"{max_a:.0f} с"),
        ("в зонах >30 с без кадра", f"{blind_b:.0f} с ({blind_b / duration * 100:.0f} %)",
         f"{blind_a:.0f} с ({blind_a / duration * 100:.0f} %)"),
    ]
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(label_before), max(len(r[1]) for r in rows))
    w2 = max(len(label_after), max(len(r[2]) for r in rows))
    out = [
        f"{'':<{w0}}  {label_before:<{w1}}  {label_after:<{w2}}",
        f"{'':─<{w0}}  {'':─<{w1}}  {'':─<{w2}}",
    ]
    out += [f"{a:<{w0}}  {b:<{w1}}  {c:<{w2}}" for a, b, c in rows]
    return "\n".join(out)
