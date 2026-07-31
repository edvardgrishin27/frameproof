"""Отбор кадров в четыре слоя. Ядро инструмента.

1. ДЕТЕКТ. Всплеск сигнала = переход. Близкие всплески склеиваются в один «взрыв»
   (в терминале текст появляется строка за строкой — это один переход, а не восемь).
   Берём кадр ПОСЛЕ того, как картинка успокоилась: на слайде и в терминале именно
   последний кадр содержит максимум набранного текста.

2. СТРАХОВКА ПО ВРЕМЕНИ. Жёсткая гарантия: ни одного промежутка длиннее max_gap.
   Детекторы молчат — ставим кадр по сетке. Слепая зона закрывается механически,
   а не «повезёт с порогом».

3. ДЕДУП. Соседние кадры с одинаковым экраном схлопываются, остаётся последний.
   Сравнение — по настоящим миниатюрам той же метрикой, что и сигнал: перцептивный
   хэш 8x8 здесь бесполезен, на такой сетке терминал с новой строкой неотличим от
   терминала без неё. Дедуп идёт ДО страховки, чтобы не съесть кадр покрытия.

4. БЮДЖЕТ. Потолок числа кадров. Удаляются самые ИЗБЫТОЧНЫЕ кадры — те, после
   удаления которых получается наименьший разрыв, и никогда те, чьё удаление порвало
   бы гарантию покрытия. Если под потолок так не ужаться — отчёт скажет честно.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .analyze import Signal

#: Превышение над собственным фоном ячейки, выше которого считаем это переходом.
#: Сигнал уже очищен от вечно шевелящихся областей (лицо, курсор, таймер),
#: поэтому порог низкий: появившаяся строка терминала уверенно перекрывает.
CHANGE_THRESHOLD = 0.015

#: Ниже этого — картинка считается успокоившейся.
QUIET_THRESHOLD = 0.004

#: Всплески ближе этого склеиваются в один переход, секунды.
MERGE_GAP = 1.2

#: Гарантия покрытия: без кадра дольше этого не остаётся ни один участок, секунды.
MAX_GAP = 15.0

#: Два кадра ближе этого не нужны — это один и тот же момент, секунды.
MIN_GAP = 0.75

#: Доля изменившихся пикселей между двумя кандидатами, ниже которой это один и тот же экран.
#: Считается по миниатюре БЕЗ вечно шумящих областей (см. Signal.noisy_mask), поэтому
#: порог низкий: 0.001 против прежних 0.01. Замер на живом ролике — со старым порогом
#: 29.7 % настоящих переходов признавались дублями, а 15.4 % одинаковых экранов —
#: разными; после маски и нового порога это 9.4 % и 0.8 %.
#: Перцептивный хэш 8x8 здесь не работает в принципе: на такой сетке терминал с новой
#: строкой неотличим от терминала без неё.
SAME_SCREEN = 0.001

#: Всплеск длиннее этого — уже не «набор команды в терминале», а другой класс контента:
#: быстрый монтаж, прокрутка, проигрывание видео внутри видео. Такой режем изнутри.
BURST_SPLIT = 2.5

#: Сколько накопленного изменения внутри длинного всплеска стоит одного кадра.
#: Замерено: на живом ролике интро 0-158 с не давало НИ ОДНОЙ паузы ниже порога,
#: превращалось в один всплеск и отдавало один кадр — теряя десятки разных экранов.
NOVELTY_BUDGET = 5.0

#: Ролик короче этого считается коротким: абсолютная гарантия покрытия на нём вырождается.
SHORT_VIDEO = 180.0

#: Потолок кадров по умолчанию.
MAX_FRAMES = 220


@dataclass(frozen=True)
class Pick:
    t: float
    reason: str          # 'change' — поймал переход; 'grid' — страховка покрытия
    change: float

    @property
    def is_safety(self) -> bool:
        return self.reason == "grid"


@dataclass(frozen=True)
class Selection:
    picks: list[Pick]
    duration: float
    max_gap_target: float
    gaps: list[tuple[float, float]]      # участки, где разрыв всё же больше цели
    dropped_duplicates: int
    dropped_budget: int
    cap: int = 0

    @property
    def times(self) -> list[float]:
        return [p.t for p in self.picks]

    @property
    def actual_max_gap(self) -> float:
        if not self.picks:
            return self.duration
        edges = [0.0] + self.times + [self.duration]
        return max(b - a for a, b in zip(edges, edges[1:]))

    @property
    def coverage(self) -> float:
        """Доля хронометража, попавшая в промежутки не длиннее цели."""
        if self.duration <= 0:
            return 0.0
        uncovered = sum(b - a for a, b in self.gaps)
        return max(0.0, 1.0 - uncovered / self.duration)

    @property
    def safety_count(self) -> int:
        return sum(1 for p in self.picks if p.is_safety)

    @property
    def cap_exceeded(self) -> bool:
        """Потолок кадров не выдержан, потому что иначе порвалась бы гарантия покрытия.

        Молчать об этом нельзя: человек просил N кадров и должен знать, что получил больше
        и почему. Обратный вариант — тихо ужаться и ослепнуть — хуже.
        """
        return bool(self.cap) and len(self.picks) > self.cap


def _bursts(sig: Signal, threshold: float, quiet: float, merge_gap: float) -> list[tuple[int, int]]:
    """Индексы (начало, конец_успокоения) для каждого всплеска активности."""
    active = sig.change >= threshold
    if not active.any():
        return []

    idx = np.flatnonzero(active)
    groups: list[list[int]] = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        i = int(i)
        if sig.times[i] - sig.times[groups[-1][1]] <= merge_gap:
            groups[-1][1] = i
        else:
            groups.append([i, i])

    out: list[tuple[int, int]] = []
    n = len(sig)
    for start, end in groups:
        # Досматриваем вперёд до первого спокойного кадра — там картинка устоялась.
        settle = end
        limit = min(n - 1, end + int(round(merge_gap * sig.fps)))
        while settle < limit and sig.change[settle + 1] > quiet:
            settle += 1
        out.append((start, min(settle + 1, n - 1)))
    return out


def _split_burst(sig: Signal, start: int, settle: int, budget: float, min_gap: float) -> list[int]:
    """Режет длинный всплеск изнутри по НАКОПЛЕННОЙ новизне.

    Короткий всплеск — это набор команды: экран меняется, пока человек печатает, и
    нужен один кадр в конце, когда строка дописана. Всплеск длиной в минуты — совсем
    другое: быстрый монтаж или прокрутка, где каждую секунду свой экран.

    Отличить их порогом активности нельзя — сигнал в обоих случаях высокий. Отличает
    длительность. Поэтому длинный всплеск идём насквозь, копим `change` и ставим кадр
    каждый раз, когда накопилось на целый новый экран.
    """
    if sig.times[settle] - sig.times[start] <= BURST_SPLIT:
        return [settle]

    picked: list[int] = []
    acc = 0.0
    last_t = -1e9
    for i in range(start, settle + 1):
        acc += float(sig.change[i])
        if acc >= budget and sig.times[i] - last_t >= min_gap:
            picked.append(i)
            last_t = float(sig.times[i])
            acc = 0.0
    if not picked or picked[-1] != settle:
        if settle - (picked[-1] if picked else start) >= 0 and (
            not picked or sig.times[settle] - sig.times[picked[-1]] >= min_gap
        ):
            picked.append(settle)
    return picked or [settle]


def _dedup(picks: list[Pick], sig: Signal, same_screen: float) -> tuple[list[Pick], int]:
    """Схлопывает соседние кадры с одинаковым экраном, оставляя последний.

    Последний — потому что на слайде и в терминале именно он содержит максимум
    набранного текста и завершённую анимацию появления.
    """
    if not picks:
        return picks, 0

    kept: list[Pick] = [picks[0]]
    dropped = 0
    for p in picks[1:]:
        if p.is_safety or kept[-1].is_safety:
            kept.append(p)
            continue
        diff = sig.thumb_diff(sig.index_at(kept[-1].t), sig.index_at(p.t))
        if diff <= same_screen:
            kept[-1] = p
            dropped += 1
        else:
            kept.append(p)
    return kept, dropped


def _enforce_min_gap(picks: list[Pick], min_gap: float) -> list[Pick]:
    out: list[Pick] = []
    for p in picks:
        if out and p.t - out[-1].t < min_gap:
            # Из двух слишком близких оставляем тот, что поймал более сильный переход.
            if p.change > out[-1].change:
                out[-1] = p
            continue
        out.append(p)
    return out


def _fill_gaps(picks: list[Pick], duration: float, max_gap: float) -> list[Pick]:
    """Страховка: закрывает любой промежуток длиннее max_gap кадрами по сетке."""
    times = sorted(p.t for p in picks)
    filled = list(picks)
    edges = [0.0] + times + [duration]
    for a, b in zip(edges, edges[1:]):
        span = b - a
        if span <= max_gap:
            continue
        n = int(span // max_gap)
        step = span / (n + 1)
        for k in range(1, n + 1):
            t = a + step * k
            if 0.0 <= t <= duration:
                filled.append(Pick(t=round(t, 3), reason="grid", change=0.0))
    filled.sort(key=lambda p: p.t)
    return filled


def _thin(picks: list[Pick], cap: int, duration: float, max_gap: float) -> tuple[list[Pick], int]:
    """Прореживание, которое НЕ ломает гарантию покрытия.

    Наивное «взять каждый N-й» выкидывает кадры вслепую и рвёт таймлайн: на живом
    материале это дало три дыры по 22-29 секунд при действующей гарантии в 15.

    Здесь на каждом шаге удаляется самый ИЗБЫТОЧНЫЙ кадр — тот, после удаления
    которого получается наименьший разрыв. Кадр, чьё удаление порвало бы гарантию,
    не трогается вообще. Если под потолок так не ужаться — оставляем как есть,
    и отчёт честно покажет перебор, вместо того чтобы молча ослепнуть.
    """
    if len(picks) <= cap:
        return picks, 0

    kept = list(picks)
    dropped = 0
    while len(kept) > cap:
        best_i, best_cost = None, float("inf")
        for i in range(len(kept)):
            # Якорь начала не трогаем. Для нулевого индекса разрыв слева считается
            # от 0.0, поэтому его удаление выглядит бесплатным — и прореживание
            # первым делом выкидывало ровно тот кадр, который мы только что поставили.
            if i == 0 and kept[i].t <= 1e-6:
                continue
            left = kept[i - 1].t if i > 0 else 0.0
            right = kept[i + 1].t if i + 1 < len(kept) else duration
            new_gap = right - left
            if new_gap > max_gap:
                continue                      # удаление порвало бы гарантию
            # При равном разрыве первым уходит страховочный кадр: пойманный
            # переход несёт больше смысла, чем точка сетки.
            cost = new_gap - (0.001 if kept[i].is_safety else 0.0)
            if cost < best_cost:
                best_i, best_cost = i, cost
        if best_i is None:
            break                             # дальше ужиматься нечем без потери покрытия
        kept.pop(best_i)
        dropped += 1
    return kept, dropped


def _measure_gaps(picks: list[Pick], duration: float, max_gap: float) -> list[tuple[float, float]]:
    times = [p.t for p in picks]
    edges = [0.0] + times + [duration]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b - a > max_gap + 1e-6]


def select_frames(
    sig: Signal,
    duration: float,
    *,
    threshold: float = CHANGE_THRESHOLD,
    quiet: float = QUIET_THRESHOLD,
    merge_gap: float = MERGE_GAP,
    max_gap: float = MAX_GAP,
    min_gap: float = MIN_GAP,
    same_screen: float = SAME_SCREEN,
    cap: int = MAX_FRAMES,
) -> Selection:
    # Гарантия в 15 секунд на 14-секундном ролике бессмысленна: один кадр, и отчёт
    # гордо пишет «покрытие 100 %». На коротком видео шаг должен быть от длительности.
    if duration < SHORT_VIDEO:
        max_gap = min(max_gap, max(1.0, duration / 12.0))

    # 1. Детект
    picks: list[Pick] = []
    for start, settle in _bursts(sig, threshold, quiet, merge_gap):
        strength = float(sig.change[start : settle + 1].max())
        for i in _split_burst(sig, start, settle, NOVELTY_BUDGET, min_gap):
            picks.append(Pick(t=float(sig.times[i]), reason="change", change=strength))
    picks.sort(key=lambda p: p.t)
    picks = _enforce_min_gap(picks, min_gap)

    # Первый кадр существует всегда. _fill_gaps делит промежуток до первого пика
    # поровну и никогда не ставит точку в нуле — начало ролика с движением
    # (то есть любое интро) не показывалось вообще.
    if not picks or picks[0].t > min_gap:
        picks.insert(0, Pick(t=0.0, reason="grid", change=0.0))

    # 3. Дедуп — ДО страховки, чтобы не съесть кадры покрытия
    picks, dropped_dup = _dedup(picks, sig, same_screen)

    # 2. Страховка по времени
    picks = _fill_gaps(picks, duration, max_gap)

    # 4. Бюджет
    picks, dropped_budget = _thin(picks, cap, duration, max_gap)

    return Selection(
        picks=picks,
        duration=duration,
        max_gap_target=max_gap,
        gaps=_measure_gaps(picks, duration, max_gap),
        dropped_duplicates=dropped_dup,
        dropped_budget=dropped_budget,
        cap=cap,
    )


def legacy_scene_times(sig: Signal, threshold: float = 0.3) -> list[float]:
    """Что дал бы наивный `select='gt(scene,0.3)'` — для честного сравнения «до/после».

    Приближение через среднюю дельту по всему кадру: ровно та метрика, которую
    считает фильтр `scene`, и ровно та, что промахивается на скринкастах.
    """
    hits = np.flatnonzero(sig.change_global >= threshold)
    return [float(sig.times[i]) for i in hits]
