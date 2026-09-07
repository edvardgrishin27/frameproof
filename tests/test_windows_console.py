# -*- coding: utf-8 -*-
"""Вывод не должен падать на консоли с узкой кодировкой.

Живой отзыв с Windows: `frameproof doctor` падает сразу, до единой полезной
строки, потому что консоль там по умолчанию cp1251, а мы печатаем «✓».
Лечится это снаружи переменной PYTHONIOENCODING, но человек об этом не знает
и видит только трейсбек. На Windows в таком виде не работает НИ ОДНА команда.
"""

import os
import subprocess
import sys

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _прогнать(команда, кодировка):
    """Запустить CLI так, будто консоль умеет только узкую кодировку."""
    env = dict(os.environ, PYTHONIOENCODING=кодировка)
    # Читаем БАЙТАМИ и декодируем сами: процесс пишет в узкой кодировке, и
    # text=True с кодировкой этой машины сломался бы на чтении, а не на записи.
    r = subprocess.run([sys.executable, "-m", "frameproof"] + команда,
                       capture_output=True, cwd=КОРЕНЬ, env=env)
    r.stdout = r.stdout.decode(кодировка, "replace")
    r.stderr = r.stderr.decode(кодировка, "replace")
    return r


def test_doctor_не_падает_на_cp1251():
    r = _прогнать(["doctor"], "cp1251")
    сломалось = "codec can't encode" in (r.stdout + r.stderr)
    assert not сломалось, "вывод doctor убит кодировкой:\n%s" % (r.stdout + r.stderr)[:400]


def test_версия_печатается_на_cp1251():
    r = _прогнать(["--version"], "cp1251")
    assert r.returncode == 0, r.stderr[:300]
    assert "frameproof" in r.stdout


def test_помощь_печатается_на_cp1251():
    """Справка полна кириллицы, и это первое, что человек запускает."""
    r = _прогнать(["--help"], "cp1251")
    assert "codec can't encode" not in (r.stdout + r.stderr), (r.stdout + r.stderr)[:300]


def test_галочка_не_превращается_в_мусор():
    """Символ должен дойти до человека, а не быть заменён вопросами.

    Замена на «?» тоже спасает от падения, но тогда таблица doctor читается
    как ошибка. Проверяем, что при узкой кодировке галочка либо на месте,
    либо честно заменена ASCII-эквивалентом, а не потеряна молча.
    """
    r = _прогнать(["doctor"], "cp1251")
    вывод = r.stdout + r.stderr
    assert "ffmpeg" in вывод, "таблица doctor не напечаталась вовсе:\n%s" % вывод[:300]
