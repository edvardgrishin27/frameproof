# -*- coding: utf-8 -*-
"""README не должен ссылаться на файлы, которых нет.

Живой отзыв: «--ocr-command — пустой контракт: пользователю нужен готовый пример».
Проверка показала хуже: README предлагал команду

    frameproof index video.mp4 --ocr --ocr-command "python ocr_windows.py"

а файла `ocr_windows.py` в репозитории не было вовсе. Человек копировал строку
из документации и получал ошибку.
"""

import io
import os
import re
import subprocess
import sys

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRIB = os.path.join(КОРЕНЬ, "contrib")


def test_скрипты_есть():
    for имя in ("ocr_windows.py", "ocr_tesseract.py"):
        assert os.path.exists(os.path.join(CONTRIB, имя)), "нет contrib/%s" % имя


def test_скрипты_компилируются():
    for имя in ("ocr_windows.py", "ocr_tesseract.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", os.path.join(CONTRIB, имя)],
                           capture_output=True)
        assert r.returncode == 0, "%s не компилируется: %s" % (имя, r.stderr.decode()[:300])


def test_tesseract_без_бинаря_говорит_понятно():
    """Скрипт обязан объяснить, что доустановить, а не упасть трейсбеком."""
    env = dict(os.environ, PATH="/nonexistent")
    r = subprocess.run([sys.executable, os.path.join(CONTRIB, "ocr_tesseract.py"), "кадр.jpg"],
                       capture_output=True, env=env)
    вывод = r.stderr.decode("utf-8", "replace")
    assert "tesseract" in вывод.lower(), вывод[:200]
    assert "apt install" in вывод or "brew install" in вывод, "нет подсказки по установке"
    assert r.returncode == 2, "код возврата должен отличать «нет движка» от успеха"


def test_readme_ссылается_только_на_существующие_файлы():
    """Сторож на будущее: любая упомянутая в README команда должна быть исполнима."""
    t = io.open(os.path.join(КОРЕНЬ, "README.md"), encoding="utf-8").read()
    for m in re.finditer(r'--ocr-command\s+"[^"]*?([\w/\\.-]+\.py)"', t):
        путь = m.group(1)
        полный = os.path.join(КОРЕНЬ, путь)
        assert os.path.exists(полный), (
            "README предлагает %s, а файла нет" % путь)


def test_оба_скрипта_держат_один_договор():
    """Формат ответа общий: путь, TAB, текст. Иначе frameproof их не прочтёт."""
    for имя in ("ocr_windows.py", "ocr_tesseract.py"):
        t = io.open(os.path.join(CONTRIB, имя), encoding="utf-8").read()
        assert '\\t' in t or "\t" in t, "%s не печатает TAB-разделитель" % имя
        assert "путь<TAB>текст" in t, "%s не описывает договор в шапке" % имя
