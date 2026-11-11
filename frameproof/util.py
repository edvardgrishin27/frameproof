"""Общие мелочи: запуск процессов, тайм-коды, безопасные пути."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unicodedata


class ToolMissing(RuntimeError):
    """Внешний бинарник не найден. Сообщаем человеку, а не падаем стеком."""


def _install_hint(tool: str) -> str:
    """Как поставить инструмент на ЭТОЙ системе. Раньше везде советовался brew."""
    if tool not in ("ffmpeg", "ffprobe"):
        return f"{tool} не найден в PATH"
    if sys.platform == "darwin":
        return f"{tool} не найден. Установите: brew install ffmpeg"
    if sys.platform.startswith("win"):
        return f"{tool} не найден. Установите: winget install Gyan.FFmpeg"
    return f"{tool} не найден. Установите: sudo apt install ffmpeg (или пакет вашей системы)"


def which(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ToolMissing(_install_hint(tool))
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
    # Секунд 60 и больше не бывает ни в какой записи, и минут сверх 59 не бывает
    # в часовой. Раньше лишнее молча переливалось в старший разряд: «99:99»
    # превращалось в «1:40:39», и опечатка автора уезжала в правдоподобный момент,
    # который verify потом честно проверял против индекса.
    # А вот минуты сверх 60 в записи MM:SS оставляем: «90:00» для полутора часов
    # люди пишут постоянно, и ломать это значит чинить не ту проблему.
    if float(parts[-1]) >= 60:
        raise ValueError(
            f"не понял тайм-код: {value!r} — секунд не бывает больше 59. "
            f"Возможно, опечатка в метке разбора")
    if len(parts) == 3 and float(parts[1]) >= 60:
        raise ValueError(
            f"не понял тайм-код: {value!r} — в записи Ч:ММ:СС минут не бывает больше 59")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def display_path(path: str | None) -> str:
    """Путь для показа человеку: разделители в одном стиле.

    Отзыв с Windows: в подсказке печаталось
    `scratchpad/proba_watch2\\fp_index\\frames\\f0084.jpg`. Прямые слеши пришли
    из того, что человек ввёл сам, обратные добавил `os.path.join`. Так работает
    любой питон, но строку из подсказки предлагается скопировать в командную
    строку, и в смешанном виде она читается как опечатка инструмента.

    Приводим к нативному для системы виду: на Windows обратные, на остальных
    прямые. Относительный путь остаётся относительным: подсказку копируют как
    есть и запускают из той же папки.
    """
    if not path:
        return ""
    return os.path.normpath(path)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма при числе: 1 кадр, 2 кадра, 5 кадров, 11 кадров.

    Вывод читает человек, и «1 кадров» сразу выдаёт машину, которая не считает,
    а склеивает строки. Правило стандартное: 11-14 всегда множественное,
    дальше решает последняя цифра.
    """
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    tail = n % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SPACE = re.compile(r"[\s_]+")


def slugify(text: str, *, limit: int = 60) -> str:
    """Имя папки из названия видео. Кириллицу сохраняем — она читаемая."""
    text = unicodedata.normalize("NFKC", str(text)).strip()
    text = _SLUG_STRIP.sub("", text)
    text = _SLUG_SPACE.sub("-", text).strip("-")
    return (text[:limit].rstrip("-") or "video").lower()
