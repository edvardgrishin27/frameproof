"""`_fetch_kinescope`: субтитры сначала, звук — только когда без них. Без сети:
все сетевые функции `kinescope.py` подменяются подделками.

Три вещи здесь важны ровно так же, как на YouTube-ветке этого же модуля (см.
`test_fetch_retry.py` и docstring `fetch.py`, пункт «СНАЧАЛА СУБТИТРЫ»):

1. Субтитры нашлись — аудио вообще не качаем, это половина веса впустую.
2. Субтитров не нашлось (или их получение упало с ошибкой) — качаем звук как раньше.
3. Ошибка при получении субтитров не должна ронять весь разбор видео — только
   печатать предупреждение в stderr, ровно как раньше уже сделано для YouTube-аудио.
"""

from __future__ import annotations

import os

import pytest

from frameproof import fetch as f
from frameproof import kinescope as ks

ВИДЕО = ks.Track(kind="video", url="https://cdn.example/video.mp4", size=1000,
                  height=720, codec="avc1", bandwidth=500)
АУДИО = ks.Track(kind="audio", url="https://cdn.example/audio_0.mp4", size=200,
                  codec="mp4a.40.2", bandwidth=128)
СУБТИТРЫ_RU = ks.SubtitleTrack(lang="ru", name="Русский (Автоматические)",
                                url="https://kinescope.io/vid/media.m3u8", auto=True)


def _база(monkeypatch, *, hls_master=None, subs=None, pick_audio_ok=True):
    """Подменяет сетевые функции kinescope на предсказуемые подделки.

    `download`/`download_subtitle` не ходят в сеть — просто создают пустой файл по
    месту назначения и возвращают путь, как это делают настоящие функции.
    """
    вызовы = {"download": [], "download_subtitle": [], "pick_audio": 0}

    monkeypatch.setattr(ks, "video_id", lambda url: ("vid123", "sign=abc"))
    monkeypatch.setattr(ks, "manifest", lambda vid, sign: b"<manifest/>")
    monkeypatch.setattr(ks, "parse", lambda mpd: ([ВИДЕО, АУДИО], 123.0))
    monkeypatch.setattr(ks, "pick_video", lambda tracks, max_height: ВИДЕО)

    def fake_pick_audio(tracks):
        вызовы["pick_audio"] += 1
        return АУДИО if pick_audio_ok else None
    monkeypatch.setattr(ks, "pick_audio", fake_pick_audio)

    def fake_download(track, dest, *, progress=None, chunk=None):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").close()
        вызовы["download"].append((track, dest))
        return dest
    monkeypatch.setattr(ks, "download", fake_download)

    if hls_master is None:
        monkeypatch.setattr(ks, "hls_master", lambda vid, sign: b"#EXTM3U\n")
    else:
        monkeypatch.setattr(ks, "hls_master", hls_master)

    monkeypatch.setattr(ks, "parse_subtitles", lambda m3u8, vid: subs or [])

    def fake_download_subtitle(track, dest):
        open(dest, "wb").close()
        вызовы["download_subtitle"].append((track, dest))
        return dest
    monkeypatch.setattr(ks, "download_subtitle", fake_download_subtitle)

    return вызовы


def test_субтитры_нашлись_звук_не_качаем(monkeypatch, tmp_path):
    вызовы = _база(monkeypatch, subs=[СУБТИТРЫ_RU])

    result = f._fetch_kinescope(
        "https://kinescope.io/vid123", str(tmp_path),
        max_height=1080, want_video=True, want_audio=True,
    )

    assert result.subtitle_path is not None
    assert result.subtitle_lang == "ru"
    assert result.subtitle_auto is True
    assert result.audio_path is None
    # download() вызван только для видео — аудио с готовыми субтитрами не нужно.
    assert len(вызовы["download"]) == 1
    assert вызовы["download"][0][0] is ВИДЕО
    assert вызовы["pick_audio"] == 0, "pick_audio не должен вызываться при готовых субтитрах"


def test_ошибка_субтитров_не_роняет_разбор_и_качает_звук(monkeypatch, tmp_path, capsys):
    def падает(vid, sign):
        raise ks.KinescopeError("HLS-мастер закрыт подписью (vid123): 403")
    вызовы = _база(monkeypatch, hls_master=падает)

    result = f._fetch_kinescope(
        "https://kinescope.io/vid123", str(tmp_path),
        max_height=1080, want_video=True, want_audio=True,
    )

    # Разбор не упал: видео и звук на месте, просто без субтитров.
    assert result.video_path
    assert result.audio_path is not None
    assert result.subtitle_path is None
    assert result.subtitle_lang is None
    assert result.subtitle_auto is False
    # download() вызван дважды — видео и звук (звук нужен, раз субтитров нет).
    assert len(вызовы["download"]) == 2

    ошибка = capsys.readouterr().err
    assert "не удалось получить субтитры" in ошибка, (
        "ошибка обязана быть видна, а не проглочена молча — как раньше было "
        "с YouTube-аудио"
    )


def test_субтитров_в_hls_нет_это_не_ошибка(monkeypatch, tmp_path, capsys):
    """Пустой список от parse_subtitles — штатный случай, не exception."""
    вызовы = _база(monkeypatch, subs=[])

    result = f._fetch_kinescope(
        "https://kinescope.io/vid123", str(tmp_path),
        max_height=1080, want_video=True, want_audio=True,
    )

    assert result.subtitle_path is None
    assert result.audio_path is not None       # субтитров нет — звук качаем
    assert "не удалось получить субтитры" not in capsys.readouterr().err


def test_want_audio_false_не_отменяется_субтитрами(monkeypatch, tmp_path):
    """Явный отказ от звука сильнее решения «субтитров нет — качаем звук»."""
    вызовы = _база(monkeypatch, subs=[])   # субтитров нет, но звук всё равно не нужен

    result = f._fetch_kinescope(
        "https://kinescope.io/vid123", str(tmp_path),
        max_height=1080, want_video=True, want_audio=False,
    )

    assert result.audio_path is None
    assert вызовы["pick_audio"] == 0


def test_want_audio_true_субтитры_не_отменяют_явный_запрос_если_их_нет(monkeypatch, tmp_path):
    """Обратная сторона: субтитры сами по себе не выключают звук без want_audio."""
    вызовы = _база(monkeypatch, subs=[])

    result = f._fetch_kinescope(
        "https://kinescope.io/vid123", str(tmp_path),
        max_height=1080, want_video=True, want_audio=True,
    )

    assert result.audio_path is not None
    assert вызовы["pick_audio"] == 1


def test_langs_доходит_до_pick_subtitle(monkeypatch, tmp_path):
    """`langs` из `fetch()` должен реально влиять на выбор дорожки, не теряться."""
    СУБТИТРЫ_EN = ks.SubtitleTrack(lang="en", name="English",
                                    url="https://kinescope.io/vid/en.m3u8", auto=False)
    _база(monkeypatch, subs=[СУБТИТРЫ_RU, СУБТИТРЫ_EN])

    result = f._fetch_kinescope(
        "https://kinescope.io/vid123", str(tmp_path),
        max_height=1080, want_video=True, want_audio=True,
        langs=("en", "ru"),
    )

    assert result.subtitle_lang == "en", "приоритет языков из langs проигнорирован"
