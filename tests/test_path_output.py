# -*- coding: utf-8 -*-
"""Пути в выводе должны быть в одном стиле.

Живой отзыв с Windows:

    ...scratchpad/proba_watch2\\fp_index\\frames\\f0084.jpg

Прямые слеши пришли из того, что ввёл человек, обратные добавил os.path.join.
Питон так работает всегда, но строку из подсказки предлагается скопировать
в командную строку, и в таком виде она выглядит как ошибка.
"""

import os

from frameproof.util import display_path


def test_смешанные_разделители_приводятся_к_одному_виду():
    смесь = "scratchpad/proba_watch2" + os.sep + "fp_index" + os.sep + "frames"
    вышло = display_path(смесь)
    assert not ("/" in вышло and "\\" in вышло), "остались оба разделителя: %r" % вышло


def test_путь_остаётся_рабочим():
    """Нормализация не должна ломать сам путь: по нему ещё открывают файлы."""
    п = os.path.join("a", "b", "c.jpg")
    assert os.path.normpath(display_path(п)) == os.path.normpath(п)


def test_пустое_и_none_не_роняют():
    assert display_path("") == ""
    assert display_path(None) == ""


def test_относительный_путь_не_превращается_в_абсолютный():
    """Подсказку копируют как есть, и она должна работать из той же папки."""
    assert not os.path.isabs(display_path(os.path.join("out", "frames")))


def test_вывод_кадров_использует_нормализацию():
    import inspect
    from frameproof import __main__ as M
    src = inspect.getsource(M)
    assert "display_path" in src, "вывод путей идёт мимо нормализации"
