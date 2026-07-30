"""Токен-бюджет кадров.

Формула Anthropic: изображение стоит ceil(w/28) * ceil(h/28) визуальных токенов
(патч 28x28 пикселей). Сверена с официальной таблицей vision-документации —
совпадает на всех контрольных значениях.

Важно и неочевидно: токены считаются ТОЛЬКО от пикселей. Вес файла на них не влияет
вообще, поэтому PNG не даёт ни одного лишнего токена — но жрёт лимит запроса и
латентность. Отсюда JPEG.
"""

from __future__ import annotations

import math

PATCH = 28

#: Максимальная длинная сторона до автоужатия на стороне API.
TIER_STANDARD_MAX_EDGE = 1568
TIER_HIGHRES_MAX_EDGE = 2576

#: Ширина кадра по умолчанию. 1280x720 = 1196 токенов: терминальный шрифт 14-16 px
#: с исходника 1080p ужимается до ~9-11 px и ещё читаем. Ниже 1280 код разваливается.
DEFAULT_WIDTH = 1280

#: Сколько кадров разумно отдавать модели за один заход.
DEFAULT_FRAMES_PER_CALL = 8


def image_tokens(width: int, height: int) -> int:
    """Стоимость одного изображения в визуальных токенах."""
    if width <= 0 or height <= 0:
        raise ValueError("размеры изображения должны быть положительными")
    return math.ceil(width / PATCH) * math.ceil(height / PATCH)


def fit_to_tier(width: int, height: int, *, highres: bool = False) -> tuple[int, int]:
    """Как API ужмёт изображение, если оно больше лимита тира.

    Возвращает размеры ПОСЛЕ возможного ужатия — по ним и надо считать токены,
    иначе оценка разойдётся с фактическим счётом.
    """
    max_edge = TIER_HIGHRES_MAX_EDGE if highres else TIER_STANDARD_MAX_EDGE
    long_edge = max(width, height)
    if long_edge <= max_edge:
        return width, height
    k = max_edge / long_edge
    return max(1, int(width * k)), max(1, int(height * k))


def effective_tokens(width: int, height: int, *, highres: bool = False) -> int:
    """Честная стоимость с учётом ужатия на стороне API."""
    w, h = fit_to_tier(width, height, highres=highres)
    return image_tokens(w, h)


def scaled_height(src_w: int, src_h: int, dst_w: int) -> int:
    """Высота при ресайзе по ширине, чётная (требование yuv420p-кодеков)."""
    if src_w <= 0:
        raise ValueError("исходная ширина должна быть положительной")
    h = round(src_h * dst_w / src_w)
    return max(2, h - (h % 2))


def plan(
    frame_count: int,
    *,
    width: int = DEFAULT_WIDTH,
    src_w: int = 1920,
    src_h: int = 1080,
    highres: bool = False,
) -> dict:
    """Сколько будет стоить показать N кадров при данной ширине."""
    h = scaled_height(src_w, src_h, width)
    per = effective_tokens(width, h, highres=highres)
    return {
        "frame_width": width,
        "frame_height": h,
        "tokens_per_frame": per,
        "frames": frame_count,
        "tokens_all_frames": per * frame_count,
        "tokens_per_call": per * DEFAULT_FRAMES_PER_CALL,
    }
