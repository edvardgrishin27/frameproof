"""Общие мелочи: запуск процессов, тайм-коды, безопасные пути."""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata


class ToolMissing(RuntimeError):
    """Внешний бинарник не найден. Сообщаем человеку, а не падаем стеком."""


def which(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ToolMissing(
            f"{tool} не найден. Установите: brew install {tool}"
            if tool in ("ffmpeg", "ffprobe", "yt-dlp")
            else f"{tool} не найден в PATH"
        )
    return path


def run(cmd: list[str], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """Запуск без shell — аргументы никогда не склеиваются в строку."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=capture,
        check=check,
    )


def tc(seconds: float) -> str:
    """3671.5 -> '1:01:11.5'. Для человека, не для машины."""
    if seconds is None:
        return "?"
    neg = seconds < 0
    seconds = abs(float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    out = f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"
    return ("-" + out) if neg else out


def tc_short(seconds: float) -> str:
    """3671 -> '1:01:11'. Без долей — для подписей и имён файлов."""
    seconds = int(round(float(seconds)))
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_tc(value: str) -> float:
    """'4:12' | '1:04:12' | '252' | '252.5' -> секунды."""
    value = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)
    parts = value.split(":")
    if not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts) or len(parts) > 3:
        raise ValueError(f"не понял тайм-код: {value!r} (ожидаю 4:12, 1:04:12 или 252)")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SPACE = re.compile(r"[\s_]+")


def slugify(text: str, *, limit: int = 60) -> str:
    """Имя папки из названия видео. Кириллицу сохраняем — она читаемая."""
    text = unicodedata.normalize("NFKC", str(text)).strip()
    text = _SLUG_STRIP.sub("", text)
    text = _SLUG_SPACE.sub("-", text).strip("-")
    return (text[:limit].rstrip("-") or "video").lower()
