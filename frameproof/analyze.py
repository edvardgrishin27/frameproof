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

import re
import subprocess
import tempfile
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

#: Фон ячейки, выше которого она считается вечно шевелящейся и исключается из
#: сравнения кадров между собой (камера ведущего, бегущий таймер, анимация).
NOISY_CELL = 0.005


#: Размер миниатюры, по которой сравниваются кадры-кандидаты между собой.
#: Перцептивный хэш 8x8 здесь не годится в принципе: на такой сетке терминал с новой
#: строкой текста неотличим от терминала без неё — та же болезнь усреднения, что и у
#: порога сцен. Держим настоящую миниатюру и сравниваем её той же метрикой.
THUMB_W, THUMB_H = 64, 36

#: showinfo печатает тайм-код каждого кадра в stderr.
_PTS = re.compile(r"pts_time:([0-9.]+)")


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
    #: Сигнал снят по ключевым кадрам, а не по равномерной сетке.
    keyframes: bool = False

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

    @cached_property
    def noisy_mask(self) -> np.ndarray:
        """Маска миниатюры: True там, где ячейка шевелится постоянно.

        Замер на живом ролике: медианное различие миниатюр на НАСТОЯЩЕМ переходе —
        0.0072, а вклад одной только камеры ведущего в углу — 0.0070. То есть шум
        камеры численно равен полезному сигналу, и дедуп работал по случайности:
        он не выкашивал контент лишь потому, что камера мешала ему это делать.

        Вырезаем шумящие ячейки из сравнения — и тогда порог можно опустить туда,
        где он действительно различает экраны.
        """
        mask = np.zeros((THUMB_H, THUMB_W), dtype=bool)
        rows = [(THUMB_H * r // GRID, THUMB_H * (r + 1) // GRID) for r in range(GRID)]
        cols = [(THUMB_W * c // GRID, THUMB_W * (c + 1) // GRID) for c in range(GRID)]
        for r in range(GRID):
            for c in range(GRID):
                if self.baseline[r, c] > NOISY_CELL:
                    r0, r1 = rows[r]
                    c0, c1 = cols[c]
                    mask[r0:r1, c0:c1] = True
        return mask

    def thumb_diff(self, i: int, j: int) -> float:
        """Доля изменившихся пикселей между двумя кадрами, без вечно шумящих областей."""
        a = self.thumbs[i].astype(np.int16)
        b = self.thumbs[j].astype(np.int16)
        diff = np.abs(a - b) > PIXEL_DELTA
        keep = ~self.noisy_mask
        if not keep.any():
            return float(diff.mean())     # весь кадр шумит — сравнивать целиком
        return float(diff[keep].mean())

    def index_at(self, t: float) -> int:
        return int(np.argmin(np.abs(self.times - t)))


def _thumb(gray: np.ndarray) -> np.ndarray:
    """Миниатюра ближайшим соседом — без Pillow и OpenCV."""
    h, w = gray.shape
    ys = (np.arange(THUMB_H) * h // THUMB_H).clip(0, h - 1)
    xs = (np.arange(THUMB_W) * w // THUMB_W).clip(0, w - 1)
    return gray[np.ix_(ys, xs)]


def keyframe_times(path: str) -> list[float]:
    """Тайм-коды ключевых кадров — один вызов ffprobe, без декодирования."""
    from .util import run

    proc = run([
        which("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
        "-of", "csv=p=0", path,
    ])
    out: list[float] = []
    for line in (proc.stdout or "").splitlines():
        value = line.strip().rstrip(",")
        if value and value != "N/A":
            try:
                out.append(float(value))
            except ValueError:
                continue
    return sorted(set(out))


def _cell_bounds(size: int, parts: int) -> list[tuple[int, int]]:
    edges = [size * i // parts for i in range(parts + 1)]
    return [(edges[i], edges[i + 1]) for i in range(parts)]


def analyze(info: VideoInfo, *, fps: float = ANALYZE_FPS, width: int = ANALYZE_WIDTH,
            fast: bool = False) -> Signal:
    """Проход декодирования: считаем сигнал изменения и миниатюры.

    `fast=True` декодирует ТОЛЬКО ключевые кадры. На 38-минутном ролике это 473 кадра
    за секунду вместо 9249 за тридцать шесть — в 38 раз быстрее. Плата честная: кадр
    встанет туда, где ключевой кадр поставил кодировщик, а не туда, где на экране
    дописалась мысль. Замерено: 1.7 полезного термина на кадр против 3.6.
    """
    height = round(info.height * width / info.width) if info.width else 180
    height = max(2, height - (height % 2))
    frame_bytes = width * height

    if fast:
        # Тайм-коды берём из showinfo той же команды, а не отдельным вызовом ffprobe:
        # второй проход по файлу стоил дороже самого декодирования (5.7 с против 1.0).
        cmd = [
            which("ffmpeg"), "-v", "info",
            "-skip_frame", "nokey", "-i", info.path,
            "-vf", f"scale={width}:{height},format=gray,showinfo",
            "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ]
    else:
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

    # stderr в файл, а не в пайп: в быстром проходе showinfo пишет строку на кадр,
    # и пайп на пару сотен килобайт заблокировал бы ffmpeg насмерть.
    log = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log)
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
        code = proc.wait()
        log.seek(0)
        err = log.read().decode("utf-8", "replace")
        log.close()

    if not times:
        raise RuntimeError(f"ffmpeg не отдал ни одного кадра (код {code}). {err.strip()[-300:]}")

    if fast:
        stamps = [float(m) for m in _PTS.findall(err)]
        if len(stamps) >= len(times):
            times = stamps[: len(times)]
        elif stamps:
            times = stamps + times[len(stamps):]

    span = times[-1] - times[0]
    return Signal(
        times=np.asarray(times, dtype=np.float64),
        cells=np.asarray(cells_seq, dtype=np.float64),
        change_global=np.asarray(change_global, dtype=np.float64),
        thumbs=np.asarray(thumbs, dtype=np.uint8),
        width=width,
        height=height,
        # В быстром проходе шаг неравномерный — храним средний, он нужен только
        # как масштаб для окна успокоения.
        fps=(len(times) / span if fast and span > 0 else fps),
        keyframes=fast,
    )
