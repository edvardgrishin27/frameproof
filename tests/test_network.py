# -*- coding: utf-8 -*-
"""Сеть: IPv4 по умолчанию и таймаут сокета.

Живой замер 11.10.2026 на машине автора, один и тот же ролик, одна минута:
метаданные через IPv4 пришли за 2,5 с, через путь по умолчанию — за 502,9 с.
Человек видел «качаю: <ссылка>» и больше ничего восемь минут, а потом снял
задачу, решив, что инструмент завис. Дело было в IPv6-маршруте до YouTube.
"""

from frameproof import fetch as F


def test_по_умолчанию_ipv4_и_таймаут(monkeypatch):
    monkeypatch.delenv("FRAMEPROOF_IPV6", raising=False)
    opts = F._опции_сети()
    assert opts.get("source_address") == "0.0.0.0", opts
    assert opts.get("socket_timeout") == F.ТАЙМАУТ_СОКЕТА


def test_ipv6_можно_вернуть_переменной(monkeypatch):
    """Есть сети, где IPv6 как раз быстрее: выключатель обязан существовать."""
    monkeypatch.setenv("FRAMEPROOF_IPV6", "1")
    opts = F._опции_сети()
    assert "source_address" not in opts, opts
    assert opts.get("socket_timeout") == F.ТАЙМАУТ_СОКЕТА


def test_сеть_доезжает_до_метаданных(monkeypatch):
    """Опции сети должны попасть в настоящий вызов yt-dlp, а не остаться в функции."""
    monkeypatch.delenv("FRAMEPROOF_IPV6", raising=False)
    увидено = {}

    class ФейковыйYDL:
        def __init__(self, opts):
            увидено.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x"}

    class ФейковыйМодуль:
        YoutubeDL = ФейковыйYDL

    monkeypatch.setattr(F, "_ydl", lambda: ФейковыйМодуль)
    F.probe_remote("https://youtu.be/x")
    assert увидено.get("source_address") == "0.0.0.0", увидено
    assert увидено.get("socket_timeout") == F.ТАЙМАУТ_СОКЕТА
