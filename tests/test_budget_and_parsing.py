"""Токены, тайм-коды и разбор субтитров."""

from __future__ import annotations

import pytest

from frameproof.budget import effective_tokens, image_tokens, scaled_height
from frameproof.transcribe import parse_json3, parse_vtt
from frameproof.util import parse_tc, tc_short


@pytest.mark.parametrize(
    "w,h,expected",
    [
        (1000, 1000, 1296),
        (1092, 1092, 1521),
        (1456, 819, 1560),
        (1920, 1080, 2691),
        (2576, 1449, 4784),
        (1269, 952, 1564),
    ],
)
def test_формула_токенов_совпадает_с_официальной_таблицей(w, h, expected):
    """ceil(w/28) * ceil(h/28) — контрольные значения из vision-документации Anthropic."""
    assert image_tokens(w, h) == expected


@pytest.mark.parametrize(
    "w,h,expected",
    [(640, 360, 299), (960, 540, 700), (1280, 720, 1196)],
)
def test_стоимость_рабочих_разрешений(w, h, expected):
    assert image_tokens(w, h) == expected


def test_ужатие_до_лимита_тира():
    """Кадр больше лимита ужимается на стороне API — считать надо по ужатому."""
    standard = effective_tokens(2560, 1440, highres=False)
    highres = effective_tokens(2560, 1440, highres=True)
    assert standard < highres
    assert highres == image_tokens(2560, 1440)


def test_высота_при_ресайзе_чётная():
    for width in (640, 960, 1280, 1456):
        assert scaled_height(1920, 1080, width) % 2 == 0


@pytest.mark.parametrize(
    "text,seconds",
    [("4:12", 252.0), ("1:04:12", 3852.0), ("252", 252.0), ("252.5", 252.5), ("0:07", 7.0)],
)
def test_разбор_таймкода(text, seconds):
    assert parse_tc(text) == seconds


def test_кривой_таймкод_падает_явно():
    with pytest.raises(ValueError):
        parse_tc("4:12:33:44")
    with pytest.raises(ValueError):
        parse_tc("вчера")


def test_формат_таймкода():
    assert tc_short(252) == "4:12"
    assert tc_short(3852) == "1:04:12"


def test_json3_разбирается():
    raw = (
        '{"events":[{"tStartMs":1000,"dDurationMs":2000,"segs":[{"utf8":"привет "},'
        '{"utf8":"мир"}]},{"tStartMs":4000,"dDurationMs":1000,"segs":[{"utf8":"\\n"}]}]}'
    )
    segs = parse_json3(raw)
    assert len(segs) == 1
    assert segs[0].text == "привет мир"
    assert segs[0].t0 == 1.0 and segs[0].t1 == 3.0


def test_катящиеся_дубли_автосубтитров_схлопываются():
    """У авто-субтитров YouTube каждая реплика повторяет предыдущую и дописывает хвост."""
    raw = (
        '{"events":['
        '{"tStartMs":0,"dDurationMs":1000,"segs":[{"utf8":"ставим ноду"}]},'
        '{"tStartMs":1000,"dDurationMs":1000,"segs":[{"utf8":"ставим ноду и клод"}]},'
        '{"tStartMs":2000,"dDurationMs":1000,"segs":[{"utf8":"ставим ноду и клод код"}]},'
        '{"tStartMs":3000,"dDurationMs":1000,"segs":[{"utf8":"дальше ffmpeg"}]}]}'
    )
    segs = parse_json3(raw)
    assert [s.text for s in segs] == ["ставим ноду и клод код", "дальше ffmpeg"]
    assert segs[0].t0 == 0.0 and segs[0].t1 == 3.0


def test_vtt_разбирается_и_чистится_от_разметки():
    raw = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "<c.colorE5E5E5>привет</c> мир\n\n"
        "00:00:04.000 --> 00:00:05.000\n"
        "&gt;&gt; вторая строка\n"
    )
    segs = parse_vtt(raw)
    assert [s.text for s in segs] == ["привет мир", ">> вторая строка"]
    assert segs[0].t0 == 1.0


def test_субтитры_srt_читаются_как_и_vtt(tmp_path):
    """--subs принимает и .srt: в тайм-коде там запятая, а строки пронумерованы.

    До этого дотянуться до разбора субтитров можно было только через ссылку — своя
    расшифровка рядом с локальным файлом не подключалась никак.
    """
    from frameproof.transcribe import from_subtitles

    f = tmp_path / "речь.srt"
    f.write_text(
        "1\n00:00:01,000 --> 00:00:03,500\nПервая строка\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nВторая строка\n",
        encoding="utf-8",
    )
    tr = from_subtitles(str(f), lang="ru")
    assert [s.text for s in tr.segments] == ["Первая строка", "Вторая строка"]
    assert tr.segments[0].t0 == 1.0 and tr.segments[0].t1 == 3.5
    assert tr.language == "ru"


def test_чужой_распознаватель_вызывается_вместо_свифта(tmp_path):
    """--ocr-command закрывает Windows и Linux: договор тот же «путь<TAB>текст»."""
    import stat

    from frameproof import ocr

    fake = tmp_path / "ocr.sh"
    fake.write_text('#!/bin/sh\nfor p in "$@"; do printf "%s\\tтекст с экрана\\n" "$p"; done\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    assert ocr.available(str(fake)) is True
    assert ocr.available("/несуществующий/бинарник") is False

    got = ocr.recognize(["a.jpg", "b.jpg"], cache_dir=str(tmp_path), command=str(fake))
    assert got == {"a.jpg": "текст с экрана", "b.jpg": "текст с экрана"}
