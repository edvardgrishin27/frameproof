# -*- coding: utf-8 -*-
"""Прокси: без него инструмент недоступен там, где YouTube закрыт.

Живой отзыв: «Из России YouTube без прокси не открывается. Переменные окружения
не помогают — проверял». Проверил он верно: yt-dlp дёргается как библиотека,
а библиотека берёт прокси из своей опции, не из окружения процесса.

Поддерживаем оба пути. Флаг важнее переменной: явное указание должно побеждать
то, что случайно осталось в окружении.
"""

import os

import pytest

from frameproof import fetch as F


def test_флаг_прокси_доезжает_до_ytdlp():
    opts = F._опции_прокси("socks5://127.0.0.1:10808")
    assert opts.get("proxy") == "socks5://127.0.0.1:10808", opts


@pytest.mark.parametrize("переменная", ["ALL_PROXY", "HTTPS_PROXY", "https_proxy", "http_proxy"])
def test_переменные_окружения_подхватываются(переменная, monkeypatch):
    """Человек ожидал именно этого, и ожидание разумное."""
    for имя in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(имя, raising=False)
    monkeypatch.setenv(переменная, "socks5://127.0.0.1:1080")
    assert F._опции_прокси(None).get("proxy") == "socks5://127.0.0.1:1080"


def test_флаг_побеждает_переменную(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://из-окружения:1080")
    assert F._опции_прокси("socks5://из-флага:9999")["proxy"] == "socks5://из-флага:9999"


def test_без_прокси_опция_не_появляется(monkeypatch):
    """Пустой ключ proxy в yt-dlp означает «прямое соединение» и ломает системный."""
    for имя in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(имя, raising=False)
    assert F._опции_прокси(None) == {}


def test_fetch_принимает_прокси():
    """Параметр обязан быть в сигнатуре, иначе CLI некуда его отдать."""
    import inspect
    assert "proxy" in inspect.signature(F.fetch).parameters
    assert "proxy" in inspect.signature(F.probe_remote).parameters


def test_прокси_подмешан_во_все_вызовы_ytdlp():
    """Четыре места создают YoutubeDL. Прокси нужен в каждом: метаданные,
    субтитры, видео и аудио идут разными запросами, и промах в одном месте
    ломает загрузку целиком."""
    import inspect
    src = inspect.getsource(F)
    создания = src.count("yt_dlp.YoutubeDL(")
    с_прокси = src.count("**proxy_opts")
    assert с_прокси >= создания, (
        "YoutubeDL создаётся %d раз, прокси подмешан %d раз" % (создания, с_прокси))


def test_флаг_есть_в_cli():
    import inspect
    from frameproof import __main__ as M
    assert "--proxy" in inspect.getsource(M), "флаг --proxy не объявлен в CLI"
