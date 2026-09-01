"""Поиск обязан говорить, где искать было НЕ ПО ЧЕМУ.

`search` отвечает на вопрос и до сих пор молчал о границах ответа. Человек
получал совпадение на 12:30 и отвечал уверенно, не зная, что на 15:00 лежит
двадцать минут без единого кадра, где тот же разговор мог продолжиться
с другим ответом.

Разрывы считаются при индексации и лежат в coverage.gaps. Их печатала только
команда report, а смотрят люди в search. Здесь проверяется, что оговорка
доезжает до того места, где на неё смотрят, и что она относится к КОНКРЕТНОМУ
ответу, а не является общей статистикой.
"""

import json
import os
import subprocess
import sys

import pytest

from frameproof.index import Hit, gaps_near_hits

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def индекс(*разрывы):
    """Индекс с заданными разрывами: (от, до) в секундах."""
    return {"coverage": {"gaps": [{"from": a, "to": b, "tc": f"{a}–{b}"} for a, b in разрывы]}}


def хит(t):
    return Hit(kind="speech", t=t, text="цена", ref="seg#1")


def test_разрыв_рядом_с_находкой_попадает_в_оговорку():
    """Главный случай: нашли на 12:30, дыра начинается на 15:00.

    Две с половиной минуты между ними. Это тот самый провал, ради которого
    всё делается: ответ дан, а рядом лежит место, где ответ мог быть другим.
    """
    d = gaps_near_hits(индекс((900, 1080)), [хит(750)])
    assert len(d["near"]) == 1
    assert d["near"][0]["distance_sec"] == 150.0


def test_далекий_разрыв_в_оговорку_не_попадает():
    """Дыра на сороковой минуте не имеет отношения к находке на второй.

    Иначе предупреждение превращается в шум, который перестают читать,
    и вместе с ним перестают читать настоящие оговорки.
    """
    d = gaps_near_hits(индекс((2400, 2600)), [хит(120)])
    assert d["near"] == []
    assert d["all"], "сам разрыв обязан остаться в полном списке"


def test_находка_внутри_разрыва_это_нулевое_расстояние():
    """Совпадение по речи внутри участка без кадров.

    Речь есть, картинки нет: слышно, но не видно. Расстояние ноль,
    и это самый сильный повод предупредить."""
    d = gaps_near_hits(индекс((600, 900)), [хит(700)])
    assert d["near"][0]["distance_sec"] == 0.0


def test_считается_ближайшее_из_всех_совпадений():
    """Расстояние меряется до ближайшего совпадения, а не до первого.

    Иначе порядок выдачи менял бы вывод предупреждения.
    """
    d = gaps_near_hits(индекс((1000, 1100)), [хит(100), хит(950)])
    assert d["near"][0]["distance_sec"] == 50.0


def test_разрывы_отсортированы_по_близости():
    d = gaps_near_hits(индекс((1000, 1010), (200, 210)), [хит(150)])
    assert [g["distance_sec"] for g in d["near"]] == sorted(g["distance_sec"] for g in d["near"])


def test_без_разрывов_и_без_находок_не_падает():
    assert gaps_near_hits({}, [])["near"] == []
    assert gaps_near_hits(индекс(), [хит(10)])["near"] == []
    assert gaps_near_hits(индекс((10, 20)), [])["near"] == []


def test_ничего_не_печатает_и_не_меняет_индекс(capsys):
    """Чистая функция: её должно быть безопасно звать откуда угодно."""
    idx = индекс((100, 200))
    копия = json.dumps(idx, sort_keys=True)
    gaps_near_hits(idx, [хит(150)])
    assert json.dumps(idx, sort_keys=True) == копия
    assert capsys.readouterr().out == ""


def test_оговорка_доезжает_до_вывода_search(tmp_path):
    """Сквозная проверка через настоящий CLI, а не через функцию.

    Функция может быть верной, а в выводе её никто не увидит — ровно так
    и было до этой правки: разрывы считались, а печатал их только report.
    """
    d = tmp_path / "idx"
    d.mkdir()
    (d / "index.json").write_text(json.dumps({
        "transcript": {"segment_count": 1, "file": "transcript.jsonl"},
        "frames": {"count": 1, "file": "frames.jsonl", "dir": "frames"},
        "coverage": {"gaps": [{"from": 900.0, "to": 1080.0, "tc": "15:00–18:00"}]},
    }, ensure_ascii=False), encoding="utf-8")
    # Имена и поля обязаны совпадать с тем, что читает build_search:
    # segments.jsonl с полем t0, иначе поиск ничего не найдёт и тест
    # покраснеет не из-за оговорки, а из-за формата.
    (d / "segments.jsonl").write_text(
        json.dumps({"i": 1, "t0": 750.0, "text": "цена вопроса"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (d / "frames.jsonl").write_text("", encoding="utf-8")

    r = subprocess.run([sys.executable, "-m", "frameproof", "search", "цена", "--out", str(d)],
                       capture_output=True, text=True, cwd=КОРЕНЬ)
    assert r.returncode == 0, r.stderr
    assert "без кадров" in r.stdout, "search промолчал про разрыв рядом с находкой"
    assert "15:00–18:00" in r.stdout
    assert "мог быть и там" in r.stdout


def test_версия_одна_во_всех_трех_местах():
    """Версия живёт в трёх файлах, и разъезжается любая пара.

    Живой случай 0.6.0: подняли в pyproject, забыли в __init__ и в plugin.json.
    uv ставил `frameproof==0.6.0`, `frameproof --version` отвечал 0.5.6, а плагин
    Claude Code сообщал третье. Существующий тест сверял только __init__
    с plugin.json, поэтому пару pyproject↔__init__ не ловил никто.
    """
    import json as _json
    import re

    import frameproof

    манифест = open(os.path.join(КОРЕНЬ, "pyproject.toml"), encoding="utf-8").read()
    m = re.search(r'(?m)^version = "([\d.]+)"', манифест)
    assert m, "в pyproject.toml не нашлась строка version"

    плагин = _json.load(open(os.path.join(КОРЕНЬ, ".claude-plugin", "plugin.json"),
                             encoding="utf-8"))["version"]

    источники = {"pyproject.toml": m.group(1),
                 "frameproof/__init__.py": frameproof.__version__,
                 "plugin.json": плагин}
    assert len(set(источники.values())) == 1, "версии разъехались: %s" % источники
