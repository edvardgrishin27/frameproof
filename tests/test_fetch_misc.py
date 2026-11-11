"""Сеть: что раньше путалось, терялось или качалось зря."""

from __future__ import annotations

import pytest

from frameproof import fetch as F
from frameproof import kinescope as KS


# --- 429 и 403 по признаку ошибки, а не по цифре где-то в тексте ---------------------

@pytest.mark.parametrize("текст", [
    "HTTP Error 429: Too Many Requests",
    "ERROR: unable to download: Too Many Requests",
])
def test_лимит_узнаётся(текст):
    assert F._это_лимит(Exception(текст))


@pytest.mark.parametrize("текст", [
    "unable to extract: video id abc4290xyz",
    "got 4293 bytes, expected more",
])
def test_цифра_429_в_тексте_не_лимит(текст):
    """Подстрока «429» в id ролика или в числе байт давала ложные повторы до 260 с."""
    assert not F._это_лимит(Exception(текст))


def test_цифра_403_в_тексте_не_запрет():
    assert not F._это_запрет(Exception("fragment 1403 of 2000 failed: timeout"))
    assert F._это_запрет(Exception("HTTP Error 403: Forbidden"))


# --- Субтитры srv3 и ttml ------------------------------------------------------------

def test_srv3_разбирается():
    """srv3 сохранялся как .vtt и разбирался как VTT — в ноль реплик."""
    from frameproof.transcribe import parse_subtitles

    raw = (b'<?xml version="1.0" encoding="utf-8" ?><timedtext format="3"><body>'
           b'<p t="1500" d="2000">hello &amp; world</p><p t="4000" d="1000">next</p>'
           b'</body></timedtext>')
    segs = parse_subtitles(raw)
    assert [(s.t0, s.t1, s.text) for s in segs] == [(1.5, 3.5, "hello & world"), (4.0, 5.0, "next")]


def test_ttml_разбирается():
    from frameproof.transcribe import parse_subtitles

    raw = (b'<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           b'<p begin="00:00:01.000" end="00:00:02.500">first<br/>line</p>'
           b'<p begin="00:01:00.000" end="00:01:01.000">second</p></div></body></tt>')
    segs = parse_subtitles(raw)
    assert [(s.t0, s.t1, s.text) for s in segs] == [(1.0, 2.5, "first line"), (60.0, 61.0, "second")]


def test_расширение_субтитров_по_содержимому():
    assert F._расширение_субтитров(b'{"events": []}') == "json3"
    assert F._расширение_субтитров(b'<?xml version="1.0"?><timedtext>') == "srv3"
    assert F._расширение_субтитров(b'<tt xmlns="x">') == "ttml"
    assert F._расширение_субтитров(b"WEBVTT\n\n") == "vtt"


# --- Kinescope через прокси ----------------------------------------------------------

def test_kinescope_ходит_через_http_прокси():
    """--proxy не доходил до Kinescope: его загрузка шла мимо yt-dlp и мимо флага."""
    KS.set_proxy("http://127.0.0.1:3128")
    try:
        handlers = [h for h in KS._opener().handlers if h.__class__.__name__ == "ProxyHandler"]
        assert handlers and handlers[0].proxies.get("https") == "http://127.0.0.1:3128"
    finally:
        KS.set_proxy(None)


def test_kinescope_честно_говорит_про_socks():
    with pytest.raises(ValueError, match="socks"):
        KS.set_proxy("socks5://127.0.0.1:1080")
    KS.set_proxy(None)


# --- Опечатка в пути ------------------------------------------------------------------

def test_опечатка_в_пути_это_не_ссылка(tmp_path, capsys):
    """Раньше несуществующий файл шёл «качаться» и падал на «нужен yt-dlp»."""
    from frameproof.__main__ import main

    assert main(["index", str(tmp_path / "zoom" / "recordng.mp4")]) == 2
    err = capsys.readouterr().err
    assert "файл не найден" in err and "качаю" not in err


@pytest.mark.parametrize("цель", ["https://youtu.be/abc", "youtube.com/watch?v=abc",
                                  "www.loom.com/share/x", "kinescope.io/embed/123"])
def test_ссылка_без_схемы_остаётся_ссылкой(цель):
    from frameproof.__main__ import _похоже_на_ссылку

    assert _похоже_на_ссылку(цель)


def test_путь_не_похож_на_ссылку():
    from frameproof.__main__ import _похоже_на_ссылку

    assert not _похоже_на_ссылку("recordings/call.mp4")
    assert not _похоже_на_ссылку(r"C:\video\call.mp4")
