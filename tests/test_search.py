"""Поиск по речи и по тексту с экрана.

Поиск — главный способ ответить без картинок, поэтому его слепые зоны дороже
всего: агент получает «не найдено» или первые 20 совпадений и уверенно отвечает.
"""

from __future__ import annotations

import json

import pytest

from frameproof.index import build_search, search, search_with_total


@pytest.fixture
def idx(tmp_path):
    d = tmp_path / "idx"
    d.mkdir()
    segs = [
        {"i": 0, "t0": 10.0, "t1": 14.0, "text": "открываем таблицу маршрутизации моделей"},
        {"i": 1, "t0": 30.0, "t1": 34.0, "text": "модель отвечает быстро"},
        {"i": 2, "t0": 50.0, "t1": 54.0, "text": "всё про память агента"},
        {"i": 3, "t0": 70.0, "t1": 74.0, "text": "Ёлка и елка — одно слово"},
    ] + [{"i": 10 + k, "t0": 100.0 + k, "t1": 100.5 + k, "text": f"повтор номер {k}"}
         for k in range(30)]
    (d / "segments.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in segs) + "\n", encoding="utf-8")
    frames = [
        {"id": "f0001", "t": 12.0, "ocr": "Таблица маршрутизации ⏎ MAIN ⏎ AUXILIARY"},
        {"id": "f0002", "t": 40.0, "ocr": "AI agent memory"},
        {"id": "f0003", "t": 60.0, "ocr": None},
    ]
    (d / "frames.jsonl").write_text(
        "\n".join(json.dumps(f, ensure_ascii=False) for f in frames) + "\n", encoding="utf-8")
    (d / "index.json").write_text(json.dumps({"transcript": {"segment_count": len(segs)},
                                              "coverage": {"gaps": []}}), encoding="utf-8")
    build_search(str(d))
    return str(d)


def refs(hits):
    return [h.ref for h in hits]


def test_падеж_в_запросе_не_мешает(idx):
    """«таблица» не находила «таблицу»: подстрока целого слова не совпадает."""
    assert "seg#0" in refs(search(idx, "таблица"))


def test_несколько_слов_через_И_а_не_фразой(idx):
    """«таблица маршрутизации» искалась одной подстрокой и давала 0 по речи."""
    got = refs(search(idx, "маршрутизация таблица"))
    assert "seg#0" in got and "f0001" in got


def test_слова_через_И_не_находят_лишнего(idx):
    assert refs(search(idx, "таблица память")) == []


def test_ё_и_е_одно_и_то_же(idx):
    assert "seg#3" in refs(search(idx, "ёлка"))
    assert "seg#3" in refs(search(idx, "елка"))


def test_короткий_запрос_находит_и_экран(idx):
    """Запасной поиск шёл сначала по речи и мог не дойти до экрана."""
    assert "f0002" in refs(search(idx, "AI"))


def test_поиск_знает_сколько_всего(idx):
    hits, total = search_with_total(idx, "повтор", limit=20)
    assert len(hits) == 20 and total == 30


def test_вывод_говорит_что_показано_не_всё(idx, capsys):
    from frameproof.__main__ import main

    main(["search", "повтор", "--out", idx])
    out = capsys.readouterr().out
    assert "показано 20 из 30" in out
    assert "--limit 30" in out
