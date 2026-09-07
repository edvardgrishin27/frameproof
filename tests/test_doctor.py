# -*- coding: utf-8 -*-
"""doctor не должен противоречить сам себе.

Живой отзыв: в одном выводе стояло

    ✗ yt-dlp   НЕ НАЙДЕН   — ссылки (для локальных файлов не нужен)
    ✓ yt_dlp   загрузка по ссылке

Первая строка искала БИНАРЬ в PATH, вторая импортировала МОДУЛЬ. Код при этом
работает только с модулем: бинарь не вызывается нигде. Человек видел крестик
и шёл доустанавливать то, что ему не нужно.
"""

import os
import re
import subprocess
import sys

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctor():
    r = subprocess.run([sys.executable, "-m", "frameproof", "doctor"],
                       capture_output=True, cwd=КОРЕНЬ)
    return r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")


def test_ytdlp_упомянут_ровно_один_раз():
    """Две строки об одном и том же — источник паники на пустом месте."""
    вывод = _doctor()
    строки = [l for l in вывод.split("\n") if re.search(r"yt[-_]dlp", l)]
    assert len(строки) == 1, "yt-dlp в выводе %d раз:\n%s" % (len(строки), "\n".join(строки))


def test_проверяется_именно_модуль_а_не_бинарь():
    """Код дёргает yt_dlp как библиотеку. Проверять надо то, что используется."""
    import inspect
    from frameproof import __main__ as M
    src = inspect.getsource(M)
    assert 'which("yt-dlp")' not in src and '"yt-dlp"' not in src.split("def cmd_doctor")[-1], \
        "doctor всё ещё ищет бинарь yt-dlp, которого код не вызывает"


def test_бинарь_ytdlp_не_нужен_коду():
    """Сторож на будущее: если бинарь однажды понадобится, проверка вернётся осознанно."""
    import glob, io
    вызовы = []
    for f in glob.glob(os.path.join(КОРЕНЬ, "frameproof", "*.py")):
        t = io.open(f, encoding="utf-8").read()
        for m in re.finditer(r'which\(\s*"yt-dlp"|\[\s*"yt-dlp"', t):
            вызовы.append(os.path.basename(f))
    assert not вызовы, "бинарь yt-dlp вызывается в %s — проверку в doctor надо вернуть" % вызовы


def test_doctor_не_врёт_про_готовность():
    """Строка итога обязана сходиться с крестиками выше."""
    вывод = _doctor()
    есть_крестик = "✗" in вывод
    assert "ffmpeg" in вывод, вывод[:200]
    if not есть_крестик:
        assert "не хватает" not in вывод.lower(), "крестиков нет, а doctor жалуется:\n%s" % вывод[-300:]
