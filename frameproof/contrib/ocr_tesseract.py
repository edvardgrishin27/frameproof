#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Распознавание текста для frameproof через tesseract. Linux, macOS, Windows.

Как подключить:

    # Debian/Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-rus
    # macOS:          brew install tesseract tesseract-lang
    # Windows:        https://github.com/UB-Mannheim/tesseract/wiki
    frameproof index video.mp4 --ocr --ocr-command "python contrib/ocr_tesseract.py"

Язык задаётся переменной FRAMEPROOF_OCR_LANG, по умолчанию `rus+eng`: на
скринкастах русский интерфейс и английский код соседствуют постоянно.

Договор тот же, что у остальных распознавателей: пути аргументами, ответ
строками `путь<TAB>текст`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ЯЗЫК = os.environ.get("FRAMEPROOF_OCR_LANG", "rus+eng")


def main(пути: list[str]) -> int:
    бинарь = shutil.which("tesseract")
    if not бинарь:
        print("tesseract не найден в PATH. Debian: apt install tesseract-ocr "
              "tesseract-ocr-rus · macOS: brew install tesseract tesseract-lang",
              file=sys.stderr)
        return 2

    for путь in пути:
        try:
            r = subprocess.run(
                [бинарь, путь, "stdout", "-l", ЯЗЫК, "--psm", "6"],
                capture_output=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            print(f"{путь}: tesseract не уложился в минуту", file=sys.stderr)
            continue
        if r.returncode != 0:
            # чаще всего это отсутствующий языковой пакет — говорим прямо
            print(f"{путь}: {r.stderr.decode('utf-8', 'replace').strip()[:200]}",
                  file=sys.stderr)
            continue
        текст = " ".join(r.stdout.decode("utf-8", "replace").split())
        if текст:
            print(f"{путь}\t{текст}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
