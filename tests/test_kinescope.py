"""Разбор манифеста Kinescope. Без сети — на срезе настоящего master.mpd."""

from __future__ import annotations

import pytest

from frameproof import kinescope as ks

# Срез живого манифеста 82-минутной лекции. Важны две вещи, ради которых модуль и
# написан: «сегментов» много, а уникальных файлов единицы, и путь сегмента — это
# буквально «<начало>/<конец>/<файл>» в байтах.
MPD = b"""<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"
     mediaPresentationDuration="PT1H22M49.364S">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <Representation id="v720" codecs="avc1.4D401F" width="1280" height="720" bandwidth="425480">
        <BaseURL>https://edge.example.net/assets/v720/</BaseURL>
        <SegmentList timescale="1000" duration="4001">
          <Initialization sourceURL="0/78776408/720p.mp4" range="36-770"/>
          <SegmentURL media="0/78776408/720p.mp4?kcd=A" mediaRange="771-764489"/>
          <SegmentURL media="0/78776408/720p.mp4?kcd=A" mediaRange="764490-1528978"/>
          <SegmentURL media="78776408/264296127/720p.mp4?kcd=A" mediaRange="0-764489"/>
        </SegmentList>
      </Representation>
      <Representation id="v360" codecs="avc1.4D401E" width="640" height="360" bandwidth="194000">
        <BaseURL>https://edge.example.net/assets/v360/</BaseURL>
        <SegmentList timescale="1000" duration="4001">
          <SegmentURL media="0/121079383/360p.mp4?kcd=A" mediaRange="771-764489"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <Representation id="a1" codecs="mp4a.40.2" bandwidth="172000">
        <BaseURL>https://edge.example.net/assets/a1/</BaseURL>
        <SegmentList timescale="1000" duration="4001">
          <SegmentURL media="0/107024609/audio_0.mp4" mediaRange="0-9999"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""

ENCRYPTED = MPD.replace(
    b'<AdaptationSet mimeType="video/mp4">',
    b'<AdaptationSet mimeType="video/mp4">'
    b'<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc"/>',
)


def test_ссылка_ведёт_на_весь_файл_а_не_на_сегмент():
    """Из «<начало>/<конец>» берём наибольший конец — это размер всей дорожки.

    Ровно здесь ломается yt-dlp (заявка 12687): он идёт по списку сегментов, но не
    читает mediaRange и качает файл целиком на каждый. На живом ролике это 96 ГБ
    вместо 121 МБ. Сервер отдаёт любой диапазон, поэтому весь файл берётся одним
    запросом по «0/<размер>/».
    """
    tracks, duration = ks.parse(MPD)
    v720 = next(t for t in tracks if t.height == 720)
    assert v720.url == "https://edge.example.net/assets/v720/0/264296127/720p.mp4?kcd=A"
    assert v720.size == 264296127
    assert duration == pytest.approx(4969.364)


def test_выбор_качества_не_превышает_потолок():
    tracks, _ = ks.parse(MPD)
    assert ks.pick_video(tracks, 1080).height == 720
    assert ks.pick_video(tracks, 360).height == 360
    # Потолок ниже самого низкого качества — берём что есть, а не падаем.
    assert ks.pick_video(tracks, 144).height in (360, 720)
    assert ks.pick_audio(tracks).size == 107024609


def test_зашифрованное_видео_отвергается_вслух():
    """Битый файл молча отдавать нельзя — для ClearKey нужен mp4decrypt, его тут нет."""
    with pytest.raises(ks.KinescopeError, match="ClearKey"):
        ks.parse(ENCRYPTED)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://kinescope.io/embed/eb36cfa7-56eb-4330-a329-0e9aed8d1439",
         "eb36cfa7-56eb-4330-a329-0e9aed8d1439"),
        ("https://kinescope.io/eb36cfa7-56eb-4330-a329-0e9aed8d1439",
         "eb36cfa7-56eb-4330-a329-0e9aed8d1439"),
        ("https://kinescope.io/200597093/", "200597093"),
    ],
)
def test_идентификатор_достаётся_из_ссылки_без_сети(url, expected):
    assert ks.video_id(url)[0] == expected


def test_идентификатор_читается_в_обеих_формах_разметки():
    """Плеер отдаёт `id: "…"`, JSON-состояние страницы — `"id":"…"`.

    Отзыв с Windows: на ссылке kinescope.io/<slug>/<slug> разбор падал с «не нашёл
    идентификатор», хотя идентификатор на странице был — просто во второй форме.
    """
    vid = "eb36cfa7-56eb-4330-a329-0e9aed8d1439"
    assert ks._ID_IN_PAGE.search(f'id: "{vid}"').group(1) == vid
    assert ks._ID_IN_PAGE.search(f'{{"id":"{vid}","title":"x"}}').group(1) == vid


def test_подпись_ссылки_собирается_из_ссылки_и_страницы():
    """Закрытому видео манифест без expires/sign отдаёт 403 — и в DASH, и в HLS."""
    signed = "https://kinescope.io/abc/def?expires=1786000000&sign=deadbeef"
    assert ks.signature(signed) == "expires=1786000000&sign=deadbeef"
    # Открытая ссылка — пустая подпись, она никому не мешает.
    assert ks.signature("https://kinescope.io/embed/abc") == ""
    # Со страницы подпись тоже достаётся.
    assert "sign=cafe" in ks.signature('', '<source src="master.mpd?expires=1&sign=cafe">')


def test_чужие_ссылки_не_перехватываются():
    assert ks.is_kinescope("https://kinescope.io/embed/abc") is True
    assert ks.is_kinescope("https://youtube.com/watch?v=abc") is False

# Амперсанд на странице плеера записан не символом, а escape-последовательностью.
# Здесь это важно буквально: если написать её в обычной строке, Python раскроет её
# ещё при разборе исходника, и тест перестанет проверять то, ради чего написан.
# Поэтому строим кусок страницы из raw-литерала и проверяем это отдельным assert.
СТРАНИЦА_С_ЭКРАНИРОВАНИЕМ = (
    r'"player","url":"https://kinescope.io/x/master.mpd'
    r'?expires=1786372715\u0026sign=0ada12de7d904732"'
)


def test_экранированный_амперсанд_не_задваивает_подпись():
    """0.5.1 отдавал 410 на закрытом видео, хотя идентификатор находил.

    В коде страницы адрес лежит в JS-виде, и амперсанд там —
    escape-последовательность. `_SIGN` обрывает значение только по настоящему
    амперсанду, поэтому хвост утекал внутрь `expires`, а `sign` находился ещё раз
    и дописывался повторно:

        expires=1786372715\u0026sign=0ada12de7d904732&sign=0ada12de7d904732

    С такой строкой `master.mpd` отвечает 410. Пользователь проверил на живой
    странице: после раскрытия манифест отдаётся целиком.
    """
    # Сначала убедимся, что в самом тесте лежит ЭКРАНИРОВАННАЯ форма, а не символ.
    assert "\\u0026" in СТРАНИЦА_С_ЭКРАНИРОВАНИЕМ
    assert "&" not in СТРАНИЦА_С_ЭКРАНИРОВАНИЕМ

    подпись = ks.signature(СТРАНИЦА_С_ЭКРАНИРОВАНИЕМ)
    assert подпись == "expires=1786372715&sign=0ada12de7d904732", подпись
    assert подпись.count("sign=") == 1, "sign задвоился"
    assert "\\u" not in подпись, "escape-последовательность утекла в подпись"


def test_html_мнемоника_амперсанда_тоже_раскрывается():
    """`&amp;` встречается на той же странице рядом с JS-формой."""
    страница = ('"url":"https://kinescope.io/x/master.mpd'
                '?expires=1786372715&amp;sign=0ada12de7d904732"')
    assert ks.signature(страница) == "expires=1786372715&sign=0ada12de7d904732"


def test_экранированное_равно_тоже_раскрывается():
    """`=` приходит экранированным реже, но тогда параметр не находился ВОВСЕ."""
    страница = r'"url":"master.mpd?expires\u003d123\u0026sign\u003dabc"'
    assert "\\u003d" in страница
    assert ks.signature(страница) == "expires=123&sign=abc"


def test_настоящие_амперсанды_работают_как_прежде():
    """Обратная сторона: обычная ссылка не должна пострадать от раскрытия."""
    assert ks.signature("https://kinescope.io/a/b?expires=123&sign=abc") == (
        "expires=123&sign=abc")
    assert ks.signature("https://kinescope.io/embed/abc") == ""
