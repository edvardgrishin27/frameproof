"""Тесты отбора кадров — здесь живёт вся ценность инструмента."""

from __future__ import annotations

import numpy as np
import pytest

from frameproof.analyze import GRID, THUMB_H, THUMB_W, Signal
from frameproof.select import MAX_GAP, legacy_scene_times, select_frames


def make_signal(
    duration: float,
    *,
    fps: float = 4.0,
    events: list[float] | None = None,
    cell: tuple[int, int] = (0, 1),
    noisy_cell: tuple[int, int] | None = None,
    noise: float = 0.0,
    magnitude: float = 0.30,
) -> Signal:
    """Синтетический сигнал.

    `events` — моменты, когда в ячейке `cell` резко меняется картинка.
    `noisy_cell` с `noise` — вечно шевелящаяся область (говорящая голова).
    """
    n = int(duration * fps)
    times = np.arange(n) / fps
    cells = np.zeros((n, GRID, GRID), dtype=np.float64)
    if noisy_cell is not None:
        rng = np.random.default_rng(0)
        cells[:, noisy_cell[0], noisy_cell[1]] = noise + rng.normal(0, noise * 0.15, n).clip(0)
    for t in events or []:
        i = int(round(t * fps))
        if 0 <= i < n:
            cells[i, cell[0], cell[1]] = magnitude

    # Глобальная средняя дельта — то, что считает ffmpeg `scene`.
    # Изменение в одной ячейке размазывается по всему кадру: делим на число ячеек.
    change_global = cells.reshape(n, -1).sum(axis=1) / (GRID * GRID)

    thumbs = np.zeros((n, THUMB_H, THUMB_W), dtype=np.uint8)
    for i in range(n):
        thumbs[i] = (i % 251)          # каждый кадр отличается от соседнего
    return Signal(
        times=times, cells=cells, change_global=change_global, thumbs=thumbs,
        width=320, height=180, fps=fps,
    )


def test_наивный_порог_слепнет_на_скринкасте():
    """Появление строки текста меняет малую долю кадра — средняя дельта не дотягивает."""
    events = [10.0, 25.0, 40.0, 55.0, 70.0]
    sig = make_signal(90.0, events=events, magnitude=0.30)

    # Изменение 30 % одной ячейки из девяти = 3.3 % кадра. Порог 0.3 недостижим.
    assert legacy_scene_times(sig, 0.3) == []

    picked = [p.t for p in select_frames(sig, 90.0).picks if p.reason == "change"]
    for e in events:
        assert any(abs(p - e) <= 2.0 for p in picked), f"пропущен переход на {e} с"


def test_говорящая_голова_не_забивает_сигнал():
    """Вечно шевелящаяся ячейка не должна делать кандидатом каждый кадр."""
    sig = make_signal(
        120.0, events=[30.0, 60.0, 90.0],
        cell=(0, 1), noisy_cell=(2, 0), noise=0.12, magnitude=0.30,
    )
    changes = [p for p in select_frames(sig, 120.0).picks if p.reason == "change"]
    assert len(changes) <= 8, f"шум прошёл как переходы: {len(changes)}"
    for e in (30.0, 60.0, 90.0):
        assert any(abs(p.t - e) <= 2.0 for p in changes), f"пропущен переход на {e} с"


def test_гарантия_покрытия_держится_на_пустом_видео():
    """Даже если не изменилось вообще ничего — разрывов быть не должно."""
    sig = make_signal(300.0, events=[])
    sel = select_frames(sig, 300.0)
    assert sel.gaps == []
    assert sel.actual_max_gap <= MAX_GAP + 1e-6
    assert sel.coverage == 1.0
    assert sel.safety_count == len(sel.picks)


@pytest.mark.parametrize("duration", [60.0, 600.0, 3600.0])
def test_разрыв_никогда_не_больше_цели(duration):
    sig = make_signal(duration, events=[duration * 0.3, duration * 0.7])
    sel = select_frames(sig, duration, max_gap=20.0, cap=500)
    assert sel.actual_max_gap <= 20.0 + 1e-6
    assert not sel.gaps


def test_бюджет_не_ломает_покрытие():
    """Прореживание под потолок обязано беречь гарантию, а не рвать таймлайн.

    Регрессия: наивное «взять каждый N-й» давало дыры по 22-29 с при цели 15.
    """
    events = [float(t) for t in range(5, 600, 7)]
    sig = make_signal(600.0, events=events)
    sel = select_frames(sig, 600.0, max_gap=15.0, cap=45)
    assert sel.actual_max_gap <= 15.0 + 1e-6, "потолок кадров порвал гарантию покрытия"
    assert len(sel.picks) <= 60


def test_дедуп_оставляет_последний_кадр():
    """На слайде и в терминале максимум текста — в последнем кадре кластера."""
    sig = make_signal(60.0, events=[10.0, 12.0, 14.0])
    # Три одинаковых экрана подряд.
    sig.thumbs[:] = 7
    sel = select_frames(sig, 60.0)
    changes = [p for p in sel.picks if p.reason == "change"]
    assert len(changes) == 1, "одинаковые экраны не схлопнулись"
    assert changes[0].t >= 14.0, "оставили не последний кадр кластера"
    assert sel.dropped_duplicates == 2


def test_дедуп_не_трогает_страховочные_кадры():
    sig = make_signal(200.0, events=[])
    sig.thumbs[:] = 3                      # все кадры одинаковые
    sel = select_frames(sig, 200.0)
    assert not sel.gaps, "дедуп съел кадры покрытия"


def test_потолок_кадров_не_жертвует_покрытием_и_говорит_об_этом():
    """Если потолок и гарантия несовместимы — побеждает гарантия, но молчать нельзя.

    Ослепнуть тихо хуже, чем честно превысить бюджет: человек просил 10 кадров,
    получил больше и должен узнать почему.
    """
    sig = make_signal(600.0, events=[])
    sel = select_frames(sig, 600.0, max_gap=5.0, cap=10)
    assert not sel.gaps, "покрытие принесено в жертву потолку"
    assert sel.cap_exceeded, "инструмент молча превысил потолок"
    assert len(sel.picks) > 10


def test_первый_кадр_существует_всегда():
    """Ролик, начинающийся с движения, не должен терять первые секунды.

    Регрессия: `_fill_gaps` делит промежуток до первого пика поровну и никогда не
    ставит точку в нуле — первые 14 секунд живого ролика не показывались вообще.
    """
    sig = make_signal(120.0, events=[40.0, 80.0])
    sel = select_frames(sig, 120.0)
    assert sel.picks[0].t == 0.0, "начало ролика не покрыто"


def test_якорь_начала_переживает_прореживание():
    """Регрессия: для нулевого индекса разрыв слева считался от 0.0, поэтому
    удаление первого кадра выглядело бесплатным, и бюджет выкидывал его первым."""
    events = [float(t) for t in range(2, 300, 3)]
    sig = make_signal(300.0, events=events)
    sel = select_frames(sig, 300.0, cap=40)
    assert sel.picks[0].t == 0.0


def test_быстрый_монтаж_не_схлопывается_в_один_кадр():
    """Очередь смен без единой паузы — это один длинный всплеск.

    На живом материале интро 0-158 с не давало ни одного спокойного кадра, всплеск
    получался один, и из него брался ОДИН кадр: десятки разных экранов терялись.
    """
    fps = 4.0
    n = int(60 * fps)
    times = np.arange(n) / fps
    cells = np.zeros((n, GRID, GRID))
    cells[:, 0, 1] = 0.02                       # лёгкое движение между склейками
    for k in range(0, n, int(fps)):             # склейка раз в секунду
        cells[k, 0, 1] = 0.5
    thumbs = np.zeros((n, THUMB_H, THUMB_W), dtype=np.uint8)
    for i in range(n):
        thumbs[i] = (i * 7) % 251
    sig = Signal(times=times, cells=cells,
                 change_global=cells.reshape(n, -1).sum(axis=1) / (GRID * GRID),
                 thumbs=thumbs, width=320, height=180, fps=fps)

    sel = select_frames(sig, 60.0)
    caught = [p for p in sel.picks if p.reason == "change"]
    # Планка намеренно скромная. Бюджет новизны — компромисс: опустить его значит
    # брать больше кадров с монтажа, но на ролике с длинным b-roll или геймплеем
    # весь потолок уедет туда, а информативный статичный экран потеряет свою долю.
    # Здесь проверяется именно регрессия: всплеск больше НЕ схлопывается в один кадр.
    assert len(caught) >= 4, f"длинный всплеск отдал всего {len(caught)} кадров"
    assert sel.actual_max_gap <= MAX_GAP + 1e-6


def test_короткий_ролик_не_получает_вакуумную_гарантию():
    """На 14-секундном видео гарантия «не дольше 15 с» выполняется одним кадром.

    Отчёт при этом гордо писал «покрытие 100 %», что формально верно и практически бесполезно.
    """
    sig = make_signal(14.0, events=[7.0])
    sel = select_frames(sig, 14.0)
    assert len(sel.picks) >= 4, f"на коротком ролике всего {len(sel.picks)} кадров"
    assert sel.actual_max_gap < 15.0


def test_шумящая_ячейка_не_мешает_дедупу():
    """Камера ведущего в углу шумит постоянно и численно равна полезному сигналу.

    Без маски дедуп работал по случайности: он не выкашивал контент лишь потому,
    что камера мешала ему это делать.
    """
    fps = 4.0
    n = 200
    cells = np.zeros((n, GRID, GRID))
    cells[:, 2, 0] = 0.05                       # вечно шевелящаяся ячейка
    sig = Signal(times=np.arange(n) / fps, cells=cells,
                 change_global=np.zeros(n),
                 thumbs=np.zeros((n, THUMB_H, THUMB_W), dtype=np.uint8),
                 width=320, height=180, fps=fps)
    # только шумящая ячейка отличается — экраны считаются одинаковыми
    sig.thumbs[5, THUMB_H * 2 // 3:, : THUMB_W // 3] = 200
    assert sig.noisy_mask.any(), "шумящая ячейка не определилась"
    assert sig.thumb_diff(0, 5) == 0.0, "шум камеры протёк в сравнение экранов"
