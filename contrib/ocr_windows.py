#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Распознавание текста для frameproof на Windows, без установки и без ключей.

Использует Windows.Media.Ocr — движок, встроенный в саму Windows 10 и 11.
Работает офлайн, ничего не отправляет наружу, отдельной программы не требует.

Как подключить:

    pip install winsdk
    frameproof index video.mp4 --ocr --ocr-command "python contrib/ocr_windows.py"

Договор простой и общий для всех внешних распознавателей: программа получает
пути к картинкам аргументами и печатает по строке на каждую в виде
`путь<TAB>текст`. Переводы строк внутри текста заменяются на пробел, иначе
строка разъедется. Картинки, которые распознать не удалось, пропускаются
молча: у frameproof это означает «текста на кадре нет», и разбор продолжается.
"""

from __future__ import annotations

import asyncio
import sys


def _язык() -> str:
    """Языки распознавания. Русский первым: у frameproof аудитория русская."""
    return "ru"


async def _распознать(путь: str) -> str:
    try:
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except ImportError:
        print("нужен пакет winsdk: pip install winsdk", file=sys.stderr)
        raise SystemExit(2)

    файл = await StorageFile.get_file_from_path_async(путь)
    поток = await файл.open_async(FileAccessMode.READ)
    декодер = await BitmapDecoder.create_async(поток)
    картинка = await декодер.get_software_bitmap_async()

    движок = OcrEngine.try_create_from_language(Language(_язык()))
    if движок is None:                      # нужного языкового пакета нет
        движок = OcrEngine.try_create_from_user_profile_languages()
    if движок is None:
        print("Windows.Media.Ocr недоступен: не установлено ни одного языка "
              "распознавания. Параметры, Время и язык, Язык и регион", file=sys.stderr)
        raise SystemExit(3)

    результат = await движок.recognize_async(картинка)
    return результат.text or ""


def main(пути: list[str]) -> int:
    for путь in пути:
        try:
            текст = asyncio.run(_распознать(путь))
        except SystemExit:
            raise
        except Exception as e:                # один битый кадр не должен ронять прогон
            print(f"{путь}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        текст = " ".join(текст.split())
        if текст:
            print(f"{путь}\t{текст}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
