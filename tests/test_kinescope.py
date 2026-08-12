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

# Срез живого манифеста с аудио в «форме Б»: `SegmentURL` без атрибута `media`,
# только `mediaRange`, а `BaseURL` указывает прямо на файл, не на каталог. Числа —
# из реальной проверки: последний диапазон 30908095-30913504, сервер на Range
# отвечает 206 с Content-Range: bytes .../30913505 — размер сходится с «конец+1».
MPD_АУДИО_ФОРМА_Б = b"""<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"
     mediaPresentationDuration="PT1H22M49.364S">
  <Period>
    <AdaptationSet mimeType="audio/mp4">
      <Representation id="a1" codecs="mp4a.40.2" bandwidth="179728" audioSamplingRate="44100">
        <BaseURL>https://edge-ams-1.kinescopecdn.net/x/assets/y/audio_0.mp4</BaseURL>
        <SegmentList timescale="1000" duration="4002">
          <Initialization sourceURL="" range="32-659"/>
          <SegmentURL mediaRange="660-75751"/>
          <SegmentURL mediaRange="75752-155116"/>
          <SegmentURL mediaRange="30908095-30913504"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""

# Оба варианта в одном манифесте — реальный случай: видео формой А, аудио формой Б.
MPD_СМЕШАННЫЙ = b"""<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"
     mediaPresentationDuration="PT1H22M49.364S">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <Representation id="v720" codecs="avc1.4D401F" width="1280" height="720" bandwidth="425480">
        <BaseURL>https://edge.example.net/assets/v720/</BaseURL>
        <SegmentList timescale="1000" duration="4001">
          <SegmentURL media="0/264296127/720p.mp4?kcd=A" mediaRange="0-764489"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <Representation id="a1" codecs="mp4a.40.2" bandwidth="179728">
        <BaseURL>https://edge-ams-1.kinescopecdn.net/x/assets/y/audio_0.mp4</BaseURL>
        <SegmentList timescale="1000" duration="4002">
          <SegmentURL mediaRange="0-30913504"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""


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


def test_форма_б_ссылка_это_baseurl_а_размер_конец_плюс_один():
    """Аудио размечено иначе видео: без `media`, только `mediaRange`.

    `BaseURL` здесь указывает прямо на файл — дописывать `0/<размер>/` не нужно и
    нельзя, получилась бы несуществующая ссылка. Размер — «конец плюс один»:
    mediaRange включает обе границы, и последний диапазон 30908095-30913504 покрывает
    байт номер 30913504 включительно, то есть файл весит 30913505 байт. До фикса эта
    дорожка пропадала: `media` был пуст у всех `SegmentURL`, `total` не набирался, и
    срабатывал `if not total: continue`.
    """
    tracks, _ = ks.parse(MPD_АУДИО_ФОРМА_Б)
    assert len(tracks) == 1
    a = tracks[0]
    assert a.kind == "audio"
    assert a.url == "https://edge-ams-1.kinescopecdn.net/x/assets/y/audio_0.mp4"
    assert a.size == 30913505


def test_форма_а_не_сломана_добавлением_формы_б():
    """Обратная сторона: старый (видео) манифест разбирается ровно как раньше."""
    tracks, duration = ks.parse(MPD)
    v720 = next(t for t in tracks if t.height == 720)
    assert v720.url == "https://edge.example.net/assets/v720/0/264296127/720p.mp4?kcd=A"
    assert v720.size == 264296127
    assert duration == pytest.approx(4969.364)


def test_обе_формы_в_одном_манифесте_видят_и_видео_и_звук():
    """Реальный случай: в одном ролике видео формой А, аудио формой Б.

    `pick_video` и `pick_audio` обязаны найти обе дорожки — до фикса аудио-форма Б
    выбрасывалась молча, и `pick_audio` возвращал `None`.
    """
    tracks, _ = ks.parse(MPD_СМЕШАННЫЙ)
    video = ks.pick_video(tracks)
    audio = ks.pick_audio(tracks)
    assert video.kind == "video" and video.height == 720
    assert audio is not None
    assert audio.kind == "audio"
    assert audio.url == "https://edge-ams-1.kinescopecdn.net/x/assets/y/audio_0.mp4"
    assert audio.size == 30913505


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


# Срез живого HLS-мастера: строка субтитров вперемешку с обычными EXT-X-MEDIA (аудио)
# и вариантами качества — в модуле их нужно отфильтровать по TYPE=SUBTITLES.
HLS_МАСТЕР = (
    b'#EXTM3U\n'
    b'#EXT-X-VERSION:6\n'
    b'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="default",DEFAULT=YES,URI="audio.m3u8"\n'
    b'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="\xd0\xa0\xd1\x83\xd1\x81\xd1\x81\xd0'
    b'\xba\xd0\xb8\xd0\xb9 (\xd0\x90\xd0\xb2\xd1\x82\xd0\xbe\xd0\xbc\xd0\xb0\xd1\x82\xd0\xb8'
    b'\xd1\x87\xd0\xb5\xd1\x81\xd0\xba\xd0\xb8\xd0\xb5)",DEFAULT=YES,AUTOSELECT=YES,'
    b'LANGUAGE="ru",FORCED="NO",URI="media.m3u8?id=5942ccb4-abc&type=subtitle&sign=xyz'
    b'&expires=1"\n'
    b'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",DEFAULT=NO,'
    b'AUTOSELECT=YES,LANGUAGE="en",FORCED="NO",URI="en.m3u8?sign=xyz"\n'
    b'#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1280x720\n'
    b'video.m3u8\n'
)
#: Та же строка кириллицей — читаемее в тексте теста, чем экранированные байты выше.
ИМЯ_РУССКОЙ_ДОРОЖКИ = "Русский (Автоматические)"


def test_строка_ext_x_media_subtitles_разбирается_в_дорожку():
    """Язык, имя, автоматичность и склейка относительного URI в абсолютный."""
    subs = ks.parse_subtitles(HLS_МАСТЕР, "ae28ead6-vid")
    # Аудио-строка (TYPE=AUDIO) отфильтрована — осталось только TYPE=SUBTITLES.
    assert len(subs) == 2

    ru = next(t for t in subs if t.lang == "ru")
    assert ru.name == ИМЯ_РУССКОЙ_ДОРОЖКИ
    assert ru.auto is True
    # URI в мастере относительный — склеен с https://kinescope.io/<video_id>/.
    assert ru.url == (
        "https://kinescope.io/ae28ead6-vid/media.m3u8"
        "?id=5942ccb4-abc&type=subtitle&sign=xyz&expires=1"
    )

    en = next(t for t in subs if t.lang == "en")
    assert en.name == "English"
    # «English» не содержит «авто»/«auto» — дорожка НЕ автоматическая.
    assert en.auto is False
    assert en.url == "https://kinescope.io/ae28ead6-vid/en.m3u8?sign=xyz"


def test_признак_автоматических_по_английскому_имени_тоже_работает():
    """Английский вариант — по подстроке auto/automatic, без учёта регистра."""
    линия = (
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English (Automatic)",'
        'LANGUAGE="en",URI="en.m3u8"\n'
    )
    subs = ks.parse_subtitles(линия.encode(), "vid")
    assert subs[0].auto is True


def test_нераспознанный_признак_не_роняет_разбор():
    """Имя без «авто»/«auto» — дорожка просто считается ручной, не ошибкой."""
    линия = (
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Deutsch",'
        'LANGUAGE="de",URI="de.m3u8"\n'
    )
    subs = ks.parse_subtitles(линия.encode(), "vid")
    assert subs[0].auto is False


def test_выбор_дорожки_по_языку_когда_их_несколько():
    """Русская приоритетнее английской — порядок из аргумента `langs`."""
    subs = ks.parse_subtitles(HLS_МАСТЕР, "ae28ead6-vid")
    assert ks.pick_subtitle(subs, ("ru", "en")).lang == "ru"
    assert ks.pick_subtitle(subs, ("en", "ru")).lang == "en"
    # Языка, которого нет среди дорожек, в списке приоритетов нет — ничего не выбрано.
    assert ks.pick_subtitle(subs, ("de",)) is None


def test_отсутствие_субтитров_в_hls_не_ошибка():
    """Часть роликов субтитров не имеет вовсе — пустой список, не исключение."""
    без_субтитров = (
        b'#EXTM3U\n'
        b'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="default",URI="audio.m3u8"\n'
        b'#EXT-X-STREAM-INF:BANDWIDTH=1000\n'
        b'video.m3u8\n'
    )
    assert ks.parse_subtitles(без_субтитров, "vid") == []
    assert ks.pick_subtitle([]) is None
