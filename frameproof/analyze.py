"""Сигнал изменения экрана — то место, где наивный детектор врёт.

Почему не `select='gt(scene,0.3)'`. Фильтр `scene` в ffmpeg считает СРЕДНЮЮ дельту по
всему кадру. Замер на реальных цветах терминала (#cccccc на #1e1e1e, кадр 640x360):
одна строка текста во всю ширину даёт 0.058, три строки — 0.149, а порог 0.3 требует
изменения около 14% площади кадра. Реальные глифы покрывают 10-15% площади строки,
поэтому набранная команда даёт скор порядка 0.006 — промах примерно в 40 раз.
Понижение порога не спасает: то, что вытянет скринкаст, взорвёт динамичное видео.

Мы считаем не среднюю дельту, а ДОЛЮ ИЗМЕНИВШИХСЯ ПИКСЕЛЕЙ, и считаем её ПО ЯЧЕЙКАМ
сетки. Появившаяся строка меняет ~1% площади кадра, но внутри своей ячейки — заметно
больше, и сигнал берётся как максимум по ячейкам. Заодно всплывающая нижняя панель или
бегущий таймер не маскируют смену основного контента.

Анализ идёт по дешёвой копии (320 px, 4 fps, серый). Финальные кадры извлекаются из
исходника в полном качестве — по тайм-кодам, найденным здесь.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np

from .probe import VideoInfo
from .util import which

#: Ширина кадра для анализа. Больше не нужно: мы ищем ГДЕ изменилось, а не ЧТО написано.
ANALYZE_WIDTH = 320

#: Частота анализа. 4 к/с ловит появление строки в терминале и не тянет полный декод.
ANALYZE_FPS = 4.0

#: Насколько должна измениться яркость пикселя, чтобы считаться изменившимся.
#: 24 из 255 — выше шума кодека, ниже реальной смены контента.
PIXEL_DELTA = 24

#: Сетка ячеек. 3x3 — компромисс: локальное изменение не размазывается, но и
#: одиночный курсор не поднимает сигнал до потолка.
GRID = 3

#: Во сколько раз изменение должно превысить собственный фон ячейки, чтобы
#: считаться событием. Вечно шевелящаяся область глушится, редко меняющаяся — нет.
BASELINE_K = 3.0


#: Размер миниатюры, по которой сравниваются кадры-кандидаты между собой.
#: Перцептивный хэш 8x8 здесь не годится в принципе: на такой сетке терминал с новой
#: строкой текста неотличим от терминала без неё — та же болезнь усреднения, что и у
#: порога сцен. Держим настоящую миниатюру и сравниваем её той же метрикой.
THUMB_W, THUMB_H = 64, 36


@dataclass
class Signal:
    """Покадровый сигнал изменения. Всё выровнено по одному индексу."""

    times: np.ndarray       # секунды, float
    cells: np.ndarray       # (N, GRID, GRID) доля изменившихся пикселей по ячейкам
    change_global: np.ndarray  # доля по всему кадру — метрика ffmpeg `scene`, для сравнения
    thumbs: np.ndarray      # (N, THUMB_H, THUMB_W) uint8 — для честного дедупа
    width: int
    height: int
    fps: float

    def __len__(self) -> int:
        return len(self.times)

    @cached_property
    def baseline(self) -> np.ndarray:
        """Собственный фон каждой ячейки — её медианная активность за весь ролик.

        Зачем. В кадре почти всегда есть вечно шевелящаяся область: говорящая голова,
        анимированный логотип, бегущий таймер, курсор. Её ячейка шумит постоянно, и
        если брать максимум по ячейкам «в лоб», она забивает сигнал целиком — на живом
        материале так и вышло: выше порога оказались 78 % кадров.

        Вычитая собственный фон ячейки, мы спрашиваем не «где шевелится», а
        «где стало НЕОБЫЧНО много движения для этого места кадра».
        """
        return np.median(self.cells, axis=0)

    @cached_property
    def change(self) -> np.ndarray:
        """Итоговый сигнал: насколько ячейка вышла за пределы своего обычного поведения.

        Порог ячейки — кратное её собственного фона. Ячейка с ненулевым фоном активна
        больше половины времени, то есть шевелится ПОСТОЯННО: камера с ведущим, бегущий
        таймер, анимация. От неё требуем сильно больше обычного. Ячейка с нулевым фоном
        молчит большую часть ролика, и любое заметное изменение в ней — событие.

        Различает эти два случая именно медиана, а не амплитуда: слайд и терминал
        меняются ВСПЫШКАМИ на фоне долгой неподвижности, а камера — непрерывно.
        Попытка отстроиться по разбросу глушила заодно и настоящий контент.
        """
        floor = (1.0 + BASELINE_K) * self.baseline
        excess = self.cells - floor[None, :, :]
        return excess.reshape(len(self.times), -1).max(axis=1).clip(0.0, 1.0)

    def thumb_diff(self, i: int, j: int) -> float:
        """Доля изменившихся пикселей между двумя кадрами — та же метрика, что и в сигнале."""
        a = self.thumbs[i].astype(np.int16)
        b = self.thumbs[j].astype(np.int16)
        return float((np.abs(a - b) > PIXEL_DELTA).mean())

    def index_at(self, t: float) -> int:
        return int(np.argmin(np.abs(self.times - t)))


def _thumb(gray: np.ndarray) -> np.ndarray:
    """Миниатюра ближайшим соседом — без Pillow и OpenCV."""
    h, w = gray.shape
    ys = (np.arange(THUMB_H) * h // THUMB_H).clip(0, h - 1)
    xs = (np.arange(THUMB_W) * w // THUMB_W).clip(0, w - 1)
    return gray[np.ix_(ys, xs)]


def _cell_bounds(size: int, parts: int) -> list[tuple[int, int]]:
    edges = [size * i // parts for i in range(parts + 1)]
    return [(edges[i], edges[i + 1]) for i in range(parts)]


def analyze(info: VideoInfo, *, fps: float = ANALYZE_FPS, width: int = ANALYZE_WIDTH) -> Signal:
    """Один проход декодирования: считаем сигнал изменения и хэши."""
    height = round(info.height * width / info.width) if info.width else 180
    height = max(2, height - (height % 2))
    frame_bytes = width * height

    cmd = [
        which("ffmpeg"),
        "-v", "error",
        "-i", info.path,
        "-vf", f"fps={fps},scale={width}:{height},format=gray",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "-",
    ]

    rows = _cell_bounds(height, GRID)
    cols = _cell_bounds(width, GRID)
    cell_area = np.array(
        [[(r1 - r0) * (c1 - c0) for (c0, c1) in cols] for (r0, r1) in rows],
        dtype=np.float64,
    )

    times: list[float] = []
    cells_seq: list[np.ndarray] = []
    change_global: list[float] = []
    thumbs: list[np.ndarray] = []

    prev: np.ndarray | None = None
    idx = 0

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            cur = np.frombuffer(buf, dtype=np.uint8).reshape(height, width)

            if prev is None:
                # Первый кадр менять не с чем — считаем его полностью новым.
                cell_frac = np.ones((GRID, GRID), dtype=np.float64)
                glob = 1.0
            else:
                diff = np.abs(cur.astype(np.int16) - prev.astype(np.int16)) > PIXEL_DELTA
                counts = np.array(
                    [[diff[r0:r1, c0:c1].sum() for (c0, c1) in cols] for (r0, r1) in rows],
                    dtype=np.float64,
                )
                cell_frac = counts / cell_area
                glob = float(diff.sum() / (height * width))

            times.append(idx / fps)
            cells_seq.append(cell_frac)
            change_global.append(glob)
            thumbs.append(_thumb(cur))
            prev = cur
            idx += 1
    finally:
        if proc.stdout:
            proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        code = proc.wait()

    if not times:
        raise RuntimeError(f"ffmpeg не отдал ни одного кадра (код {code}). {err.strip()[:300]}")

    return Signal(
        times=np.asarray(times, dtype=np.float64),
        cells=np.asarray(cells_seq, dtype=np.float64),
        change_global=np.asarray(change_global, dtype=np.float64),
        thumbs=np.asarray(thumbs, dtype=np.uint8),
        width=width,
        height=height,
        fps=fps,
    )
