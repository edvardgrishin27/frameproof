"""cookies из браузера (403 при аутентификации) и предупреждение об устаревшем
yt-dlp. Без сети: загрузчик подменяется подделкой, тем же приёмом, что и в
test_fetch_format_fallback.py.

Три вещи здесь важны:

1. cookies никогда не включаются сами по себе — только по явному имени
   браузера, и тогда уходят на все четыре точки загрузки (метаданные,
   субтитры, аудио, видео), а не на часть из них.
2. Оба предупреждения (приватность cookies, устаревший yt-dlp) печатаются
   один раз за процесс, а не на каждую точку загрузки — иначе одна загрузка
   показала бы человеку одно и то же до четырёх раз подряд.
3. Ошибка при исчерпании форматов советует cookies только тогда, когда их
   ещё не пробовали — иначе совет бессмыслен.
"""

from __future__ import annotations

import datetime
import types

import pytest

from frameproof import fetch as f


class _Запрет(Exception):
    """Как yt-dlp заворачивает 403: своим классом, с текстом внутри."""

    def __init__(self):
        super().__init__("HTTP Error 403: Forbidden")


def _без_сна(_секунд):
    """Паузы в тесте не спим — иначе баг в коде превратил бы тест в зависание."""


@pytest.fixture(autouse=True)
def _чистое_состояние(monkeypatch):
    """Оба предупреждения печатаются один раз за процесс (модульный флаг).

    Без сброса тесты в этом файле зависели бы от порядка запуска и от того,
    что уже успели сделать другие тесты, использующие тот же модуль
    `frameproof.fetch` в этой же pytest-сессии.
    """
    monkeypatch.setattr(f, "_ВЕРСИЯ_ПРОВЕРЕНА", False)
    monkeypatch.setattr(f, "_COOKIES_ПОКАЗАНО", False)


def _yt_dlp_журналирующий(журнал: list[tuple[str, dict]]):
    """Поддельный `yt_dlp`: каждый вызов `YoutubeDL(opts)` дописывает
    `(точка, opts)` в `журнал` и не ходит в сеть.

    В отличие от `_поддельный_yt_dlp` из test_fetch_format_fallback.py (там
    важен только `download`, потому что речь о переборе форматов), здесь
    важны опции НА ВСЕХ четырёх точках — метаданные и субтитры тоже
    записываются.
    """

    class _FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def extract_info(self, _url, download=False):
            журнал.append(("метаданные", dict(self._opts)))
            return {
                "id": "vid1", "title": "тестовое видео", "duration": 5.0,
                "channel": "канал", "upload_date": "20260101",
            }

        def urlopen(self, _url):
            журнал.append(("субтитры", dict(self._opts)))

            class _Ответ:
                def read(self):
                    return b'{"events": []}'
            return _Ответ()

        def download(self, _urls):
            outtmpl = self._opts["outtmpl"]
            точка = "аудио" if outtmpl.endswith("audio.m4a") else "видео"
            журнал.append((точка, dict(self._opts)))
            with open(outtmpl, "wb") as fh:
                fh.write(b"data")
            return 0

    return types.SimpleNamespace(YoutubeDL=_FakeYDL)


# ─────────────────────── cookies: опции на точках загрузки ───────────────────────


def test_cookies_не_задействуются_когда_флаг_не_передан(monkeypatch, tmp_path):
    журнал: list[tuple[str, dict]] = []
    monkeypatch.setattr(f, "_ydl", lambda: _yt_dlp_журналирующий(журнал))
    monkeypatch.setattr(
        f, "_pick_subtitle",
        lambda info, langs: ("http://sub.example/x.json3", "ru", False),
    )

    f.fetch("https://youtu.be/xxxxxxxxxxx", str(tmp_path))

    assert журнал, "хотя бы одна точка загрузки должна была отработать"
    for точка, opts in журнал:
        assert "cookiesfrombrowser" not in opts, (
            f"{точка}: ключа cookiesfrombrowser быть не должно — флаг не передавали"
        )


def test_cookies_уходят_в_метаданные_субтитры_и_видео(monkeypatch, tmp_path):
    """Субтитры нашлись — аудио fetch() не качает вовсе (см. docstring fetch()),
    поэтому эта точка проверяется отдельным тестом ниже.
    """
    журнал: list[tuple[str, dict]] = []
    monkeypatch.setattr(f, "_ydl", lambda: _yt_dlp_журналирующий(журнал))
    monkeypatch.setattr(
        f, "_pick_subtitle",
        lambda info, langs: ("http://sub.example/x.json3", "ru", False),
    )

    f.fetch("https://youtu.be/xxxxxxxxxxx", str(tmp_path), cookies_from_browser="chrome")

    точки = {т for т, _ in журнал}
    assert точки == {"метаданные", "субтитры", "видео"}
    for точка, opts in журнал:
        assert opts.get("cookiesfrombrowser") == ("chrome",), (
            f"{точка}: cookies должны были уйти в опции yt-dlp"
        )


def test_cookies_уходят_в_аудио_когда_субтитров_нет(monkeypatch, tmp_path):
    журнал: list[tuple[str, dict]] = []
    monkeypatch.setattr(f, "_ydl", lambda: _yt_dlp_журналирующий(журнал))
    monkeypatch.setattr(f, "_pick_subtitle", lambda info, langs: (None, None, False))

    f.fetch("https://youtu.be/xxxxxxxxxxx", str(tmp_path), cookies_from_browser="firefox")

    точки = {т for т, _ in журнал}
    assert точки == {"метаданные", "аудио", "видео"}
    for точка, opts in журнал:
        assert opts.get("cookiesfrombrowser") == ("firefox",), (
            f"{точка}: cookies должны были уйти в опции yt-dlp"
        )


def test_probe_remote_передаёт_extra_opts(monkeypatch):
    журнал: list[tuple[str, dict]] = []
    monkeypatch.setattr(f, "_ydl", lambda: _yt_dlp_журналирующий(журнал))

    f.probe_remote(
        "https://youtu.be/xxxxxxxxxxx",
        extra_opts={"cookiesfrombrowser": ("safari",)},
    )

    assert len(журнал) == 1
    точка, opts = журнал[0]
    assert точка == "метаданные"
    assert opts.get("cookiesfrombrowser") == ("safari",)


def test_предупреждение_о_cookies_печатается_один_раз_за_загрузку(monkeypatch, tmp_path, capsys):
    """Опция уходит на три точки (метаданные, аудио, видео) за один fetch(), но
    предупреждение о приватности должно появиться только один раз, а не три.
    """
    журнал: list[tuple[str, dict]] = []
    monkeypatch.setattr(f, "_ydl", lambda: _yt_dlp_журналирующий(журнал))
    monkeypatch.setattr(f, "_pick_subtitle", lambda info, langs: (None, None, False))

    f.fetch("https://youtu.be/xxxxxxxxxxx", str(tmp_path), cookies_from_browser="chrome")

    assert {т for т, _ in журнал} == {"метаданные", "аудио", "видео"}
    захвачено = capsys.readouterr()
    assert захвачено.err.count("⚠ cookies") == 1, (
        "предупреждение о приватности должно быть одно на всю загрузку"
    )


# ─────────────────────── cookies: валидация имени браузера ───────────────────────


def test_неизвестный_браузер_даёт_понятную_ошибку():
    with pytest.raises(RuntimeError) as поймано:
        f._опции_cookies("chroma")  # опечатка

    текст = str(поймано.value)
    assert "chroma" in текст
    for браузер in f.БРАУЗЕРЫ_С_COOKIES:
        assert браузер in текст, f"{браузер} должен быть в списке поддерживаемых"


def test_fetch_с_неизвестным_браузером_падает_до_обращения_к_yt_dlp(monkeypatch, tmp_path):
    """Валидация должна сработать раньше первого сетевого вызова — иначе человек
    сначала увидел бы трейсбек yt-dlp про keyring, а не понятный список браузеров.
    """
    monkeypatch.setattr(f, "_ydl", lambda: types.SimpleNamespace())

    def нельзя_сюда(*_args, **_kwargs):
        raise AssertionError("дошли до probe_remote, хотя браузер не прошёл валидацию")

    monkeypatch.setattr(f, "probe_remote", нельзя_сюда)

    with pytest.raises(RuntimeError) as поймано:
        f.fetch(
            "https://youtu.be/xxxxxxxxxxx", str(tmp_path),
            cookies_from_browser="netbrowser",
        )

    текст = str(поймано.value)
    assert "netbrowser" in текст
    assert "chrome" in текст, "нет списка поддерживаемых браузеров в ошибке"


# ─────────────────────── ошибка при исчерпании форматов: совет про cookies ───────────────────────


def test_ошибка_403_советует_cookies_когда_их_не_было():
    def собрать(_формат):
        def сделать(_доп):
            raise _Запрет()
        return сделать

    with pytest.raises(RuntimeError) as поймано:
        f._с_перебором_форматов(собрать, ["f1", "f2"], "видео", спать=_без_сна)

    assert "--cookies-from-browser" in str(поймано.value)


def test_ошибка_403_не_советует_cookies_когда_уже_использовались():
    def собрать(_формат):
        def сделать(_доп):
            raise _Запрет()
        return сделать

    with pytest.raises(RuntimeError) as поймано:
        f._с_перебором_форматов(
            собрать, ["f1", "f2"], "видео", спать=_без_сна, cookies_used=True,
        )

    assert "--cookies-from-browser" not in str(поймано.value)


# ─────────────────────── версия yt-dlp: предупреждение об устаревании ───────────────────────


def _поддельная_версия(строка: str):
    return types.SimpleNamespace(version=types.SimpleNamespace(__version__=строка))


def test_версия_старая_даёт_предупреждение(capsys):
    старая = "2000.01.01"
    f._проверить_версию_ytdlp(_поддельная_версия(старая))

    захвачено = capsys.readouterr()
    assert "⚠" in захвачено.err
    assert старая in захвачено.err
    assert "pip install -U yt-dlp" in захвачено.err


def test_версия_свежая_не_даёт_предупреждения(capsys):
    свежая = datetime.date.today().strftime("%Y.%m.%d")
    f._проверить_версию_ytdlp(_поддельная_версия(свежая))

    захвачено = capsys.readouterr()
    assert захвачено.err == ""


def test_версия_нестандартная_строка_не_роняет(capsys):
    # Не должно бросить исключение — строка не разбирается как ГГГГ.ММ.ДД.
    f._проверить_версию_ytdlp(_поддельная_версия("не-версия"))

    захвачено = capsys.readouterr()
    assert захвачено.err == ""


def test_версия_без_атрибута_version_не_роняет(capsys):
    # Как поддельный yt_dlp в test_fetch_format_fallback.py — у него нет .version.
    f._проверить_версию_ytdlp(types.SimpleNamespace())

    захвачено = capsys.readouterr()
    assert захвачено.err == ""


def test_предупреждение_о_версии_печатается_один_раз(capsys):
    старая = "2000.01.01"
    f._проверить_версию_ytdlp(_поддельная_версия(старая))
    f._проверить_версию_ytdlp(_поддельная_версия(старая))

    захвачено = capsys.readouterr()
    assert захвачено.err.count("⚠ yt-dlp") == 1, "предупреждение должно быть одно, не два"


def test_fetch_проверяет_версию_один_раз_за_вызов(monkeypatch, tmp_path):
    вызовы = []
    monkeypatch.setattr(f, "_проверить_версию_ytdlp", lambda yt_dlp: вызовы.append(yt_dlp))
    журнал: list[tuple[str, dict]] = []
    monkeypatch.setattr(f, "_ydl", lambda: _yt_dlp_журналирующий(журнал))
    monkeypatch.setattr(
        f, "_pick_subtitle",
        lambda info, langs: ("http://sub.example/x.json3", "ru", False),
    )

    f.fetch("https://youtu.be/xxxxxxxxxxx", str(tmp_path))

    assert len(вызовы) == 1, "проверка версии не должна вызываться на каждую точку загрузки"
