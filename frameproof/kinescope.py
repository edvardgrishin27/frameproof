"""Kinescope — своя загрузка, потому что через yt-dlp она сегодня не работает.

Kinescope это российский видеохостинг, на нём лежат курсы, вебинары и корпоративные
записи, то есть ровно тот материал, ради которого инструмент и писался. Экстрактора
в yt-dlp нет: заявка `yt-dlp#3391` открыта с 2022 года, страница отдаёт «Unsupported
URL». Обойти это подсовыванием манифеста тоже не выходит — форматы yt-dlp перечислит,
но скачает не то, см. ниже.

Как устроена отдача:

    https://kinescope.io/<video_id>/master.mpd

Ключей и авторизации не нужно, но часть роликов закрыта ПОДПИСЬЮ ссылки: без
`expires` и `sign` манифест отвечает 403, одинаково и в DASH, и в HLS. Параметры
берём из самой ссылки, а если её дали без них — со страницы плеера. Пустая подпись
открытому видео не мешает.

DASH-манифест, `SegmentList` с байтовыми диапазонами. Все «сегменты» указывают на один
и тот же файл: у 82-минутной лекции 1243 записи `SegmentURL` и от двух до семи
уникальных URL. Путь внутри — `<начало>/<конец>/720p.mp4`, то есть буквально границы
куска в байтах. Отсюда открытый баг `yt-dlp#12687`: загрузчик берёт `media`, не берёт
`mediaRange` и качает файл целиком на каждый из 1243 «сегментов». Живая проверка на
том же ролике: yt-dlp насчитал 96 ГБ там, где видео весит 121 МБ.

Из устройства путей следует простое решение. Сервер честно отдаёт любой диапазон,
который у него попросят, поэтому весь файл адресуется как `0/<размер>/`. Проверено:
`Content-Length` совпадает с размером из пути, длительность скачанного совпадает с
заявленной в манифесте, кадр с 40-й минуты достаётся за 0.09 с.

Аудио в том же манифесте размечено ДРУГОЙ формой `SegmentList`. У `SegmentURL` там
нет атрибута `media` вовсе — есть только `mediaRange`, а `BaseURL` указывает прямо
на файл (`audio_0.mp4`), не на каталог с путём внутри. `mediaRange` включает обе
границы, поэтому размер файла — не последний «конец» сам по себе, а «конец плюс
один»: у дорожки с последним диапазоном `30908095-30913504` файл весит 30913505
байт, и сервер это подтверждает — на запрос с `Range` отвечает `206` с
`Content-Range: bytes 0-1048575/30913505`. Первая редакция эту форму не различала:
цикл собирал `media` по всем `SegmentURL`, получал список пустых строк (атрибута
там нет), не набирал `total` ни на одном элементе и молча выбрасывал дорожку целиком
— аудио пропадало из разбора, расшифровывать было нечего.

Качаем при этом не одним запросом, а кусками по 32 МБ с докачкой: на 121 МБ одиночный
запрос проходил, на 337 МБ сервер рвёт соединение на середине.

Ходим обычным urllib: тащить `requests` в зависимости ради двух GET незачем.

Субтитров DASH-манифест не содержит вовсе — в нём только два `AdaptationSet`,
`video/mp4` и `audio/mp4`. Но тот же ролик отдаётся и по HLS, тем же ID и той же
подписью (`master.m3u8` вместо `master.mpd`), и там для части видео есть готовая
дорожка — строкой `EXT-X-MEDIA:TYPE=SUBTITLES`, ссылающейся на плейлист с одним
готовым `.vtt`. HLS здесь используется ТОЛЬКО ради субтитров: видео и звук как
качались через DASH, так и качаются, дублировать этот путь через HLS незачем.

Чего здесь НЕТ. Часть видео на Kinescope зашифрована ClearKey — у таких в манифесте
стоит `ContentProtection`, а ключ выдаётся по запросу на `license.kinescope.io`.
Расшифровка требует `mp4decrypt` из Bento4, отдельного бинарника, которого у нас в
зависимостях нет и который не ставится через pip. Такое видео мы не тянем и говорим
об этом прямо, вместо того чтобы отдать битый файл.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin

BASE = "https://kinescope.io"
MASTER = BASE + "/{video_id}/master.mpd"
HLS_MASTER = BASE + "/{video_id}/master.m3u8"
NS = {"m": "urn:mpeg:dash:schema:mpd:2011"}

#: UUID видео или старый числовой идентификатор.
_ID = re.compile(
    r"(?:kinescope\.io|kinescopecdn\.net)/(?:embed/)?"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{6,})",
    re.I,
)
#: Идентификатор, зашитый в страницу плеера, — запасной путь для ссылок-обёрток.
#: Форм две и обе живые: `id: "…"` в конфиге плеера и `"id":"…"` в JSON-состоянии.
#: Первая редакция знала только про первую, и на ссылках вида kinescope.io/<slug>/<slug>
#: разбор падал с «не нашёл идентификатор», хотя идентификатор на странице был.
_ID_IN_PAGE = re.compile(
    r'"?\bid"?\s*:\s*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{6,})"',
    re.I,
)
#: Подпись ссылки. У части видео манифест закрыт: без них и master.mpd, и master.m3u8
#: отвечают 403. Параметры берём из самой ссылки или со страницы плеера.
_SIGN = re.compile(r"\b(expires|sign|token)=([^&\"'\s<>]+)", re.I)
#: На странице плеера адрес лежит в JS/JSON-виде: амперсанд записан там
#: escape-последовательностью `\u0026`, а не самим символом. `_SIGN` обрывает
#: значение только по настоящему амперсанду, поэтому хвост утекал внутрь `expires`, а
#: `sign` потом находился ещё раз и дописывался повторно —
#: `expires=…&sign=X&sign=X`. С такой строкой `master.mpd` отвечает 410. Раскрываем
#: `\uXXXX` в общем виде, а не только амперсанд: `=` тоже иногда приходит
#: экранированным, и тогда `_SIGN` вовсе не находит параметр.
_ESCAPED_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescape(text: str) -> str:
    """Раскрывает `\\uXXXX` перед поиском подписи.

    Раскрываем только коды, дающие печатный ASCII (0x20–0x7E), — этого достаточно для
    амперсанда и знака равенства и безопасно для всего остального: непечатное или
    не-ASCII оставляем как есть, не гадая, что имелось в виду.

    HTML-мнемоника `&amp;` тут НЕ обрабатывается, и это не упущение: в ней амперсанд
    стоит настоящим символом, так что `_SIGN` обрывает значение на нём сам. Замена
    была написана «на всякий случай», мутационная проверка показала, что она не
    защищает ничего, — а код, который ничего не делает, врёт про свою нужность.
    Случай `&amp;` при этом закреплён тестом.
    """
    def _sub(m: re.Match[str]) -> str:
        code = int(m.group(1), 16)
        return chr(code) if 0x20 <= code <= 0x7E else m.group(0)

    return _ESCAPED_UNICODE.sub(_sub, text)


class KinescopeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Track:
    kind: str                # 'video' | 'audio'
    url: str                 # прямая ссылка на файл целиком
    size: int
    height: int = 0
    codec: str = ""
    bandwidth: int = 0


def is_kinescope(url: str) -> bool:
    return "kinescope.io" in url.lower()


def signature(*sources: str) -> str:
    """Параметры подписи из ссылки или из страницы плеера. Пусто — если их там нет.

    Закрытому видео манифест без `expires`/`sign` отдаёт 403, причём одинаково и в
    DASH, и в HLS. Открытому они не нужны и не мешают.
    """
    found: dict[str, str] = {}
    for src in sources:
        for key, value in _SIGN.findall(_unescape(src or "")):
            found.setdefault(key.lower(), value)
    return "&".join(f"{k}={v}" for k, v in found.items())


def video_id(url: str) -> tuple[str, str]:
    """Идентификатор и параметры подписи. Страница читается только если надо."""
    sign = signature(url)
    m = _ID.search(url)
    if m:
        return m.group(1), sign

    # Ссылка-обёртка (в том числе kinescope.io/<slug>/<slug>): идентификатор и подпись
    # лежат в разметке плеера.
    try:
        page = _get(url, referer=url).decode("utf-8", "replace")
    except Exception as exc:
        raise KinescopeError(f"не открылась страница {url}: {exc}") from exc
    m = _ID_IN_PAGE.search(page)
    if not m:
        raise KinescopeError(
            "не нашёл идентификатор видео на странице. Если видео встроено на чужом "
            "сайте, откройте плеер и возьмите ссылку вида kinescope.io/embed/<id>"
        )
    return m.group(1), sign or signature(page)


def _get(url: str, *, referer: str = BASE, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"Referer": referer, "User-Agent": "frameproof"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def manifest(vid: str, sign: str = "") -> bytes:
    url = MASTER.format(video_id=vid) + (f"?{sign}" if sign else "")
    try:
        return _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and not sign:
            raise KinescopeError(
                f"манифест закрыт подписью ({vid}): 403. Откройте плеер и возьмите "
                "ссылку целиком, вместе с параметрами expires и sign, — они нужны "
                "и DASH, и HLS одинаково"
            ) from exc
        raise KinescopeError(f"манифест не отдался ({vid}): {exc}") from exc
    except Exception as exc:
        raise KinescopeError(f"манифест не отдался ({vid}): {exc}") from exc


def parse(mpd: bytes) -> tuple[list[Track], float]:
    """Дорожки и длительность из манифеста. Ссылка у каждой — на весь файл сразу."""
    root = ET.fromstring(mpd)
    if root.findall(".//m:ContentProtection", NS):
        raise KinescopeError(
            "видео зашифровано (ClearKey). Для расшифровки нужен mp4decrypt из Bento4 — "
            "его здесь нет, и битый файл отдавать не будем"
        )

    tracks: list[Track] = []
    for aset in root.findall(".//m:AdaptationSet", NS):
        kind = "audio" if "audio" in (aset.get("mimeType") or "") else "video"
        for rep in aset.findall("m:Representation", NS):
            base = (rep.findtext("m:BaseURL", default="", namespaces=NS) or "").strip()
            segs = rep.findall(".//m:SegmentURL", NS)
            if not base or not segs:
                continue

            # Форма А (видео) и форма Б (аудио) различаются наличием `media`: у формы
            # А он есть на каждом `SegmentURL`, у формы Б атрибута нет вовсе — там
            # только `mediaRange`. Смешивать формы внутри одной Representation
            # незачем, живой манифест так не делает.
            media = [s.get("media") for s in segs]
            if any(media):
                # Путь сегмента — «<начало>/<конец>/<файл>». Самый большой «конец» и
                # есть размер всей дорожки, а «0/<размер>/<файл>» сервер отдаёт одним
                # куском.
                total, name, query = 0, "", ""
                for m in media:
                    if not m:
                        continue
                    head, _, tail = m.partition("?")
                    parts = head.split("/")
                    if len(parts) < 3 or not parts[1].isdigit():
                        continue
                    if int(parts[1]) > total:
                        total, name, query = int(parts[1]), parts[-1], tail
                if not total:
                    continue
                url = f"{base}0/{total}/{name}" + (f"?{query}" if query else "")
            else:
                # `BaseURL` здесь — уже прямая ссылка на файл, дописывать нечего.
                # Размер — максимальный «конец» среди `mediaRange`, плюс один: границы
                # в mediaRange включают обе стороны, и «30908095-30913504» покрывает
                # байт номер 30913504 включительно, то есть файл весит 30913505.
                # Проверено живьём: сервер на Range отвечает 206 с
                # `Content-Range: bytes 0-1048575/30913505` — сходится с расчётом.
                ends = []
                for s in segs:
                    _, _, end = s.get("mediaRange", "").partition("-")
                    if end.isdigit():
                        ends.append(int(end))
                if not ends:
                    continue
                total = max(ends) + 1
                url = base
            tracks.append(Track(
                kind=kind,
                url=url,
                size=total,
                height=int(rep.get("height") or 0),
                codec=rep.get("codecs") or "",
                bandwidth=int(rep.get("bandwidth") or 0),
            ))

    if not tracks:
        raise KinescopeError("в манифесте не нашлось ни одной дорожки")
    return tracks, _duration(root.get("mediaPresentationDuration") or "")


def _duration(value: str) -> float:
    m = re.match(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", value)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def pick_video(tracks: list[Track], max_height: int = 1080) -> Track:
    vids = [t for t in tracks if t.kind == "video"]
    if not vids:
        raise KinescopeError("в манифесте нет видеодорожки")
    fit = [t for t in vids if t.height <= max_height]
    return max(fit or vids, key=lambda t: (t.height, t.bandwidth))


def pick_audio(tracks: list[Track]) -> Track | None:
    auds = [t for t in tracks if t.kind == "audio"]
    return max(auds, key=lambda t: t.bandwidth) if auds else None


@dataclass(frozen=True)
class SubtitleTrack:
    lang: str            # код языка из LANGUAGE, как есть, в нижнем регистре
    name: str             # человекочитаемое имя из NAME
    url: str              # ссылка на плейлист дорожки, уже абсолютная
    auto: bool = False    # автоматические (ASR) или ручные


#: Атрибуты строки `#EXT-X-MEDIA:…`: `КЛЮЧ="значение в кавычках"` или `КЛЮЧ=значение`
#: без них (в HLS так пишут перечислимые значения вроде `YES`/`SUBTITLES`).
_HLS_ATTR = re.compile(r'([A-Za-z0-9-]+)=(?:"([^"]*)"|([^,]*))')
#: Признак автоматической дорожки в имени. По фактам с двух виденных роликов имя
#: содержит либо «(Автоматические)», либо `Automatic` — нижняя граница совпадения по
#: подстроке «авто»/«auto» хватает на оба варианта. Не распознали — считаем дорожку
#: ручной, это не ошибка: ложноотрицательный `auto` не мешает ни выбору, ни скачиванию.
_AUTO_HINT = re.compile(r"авто|auto", re.I)


def _hls_attrs(line: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in _HLS_ATTR.finditer(line):
        key, quoted, bare = m.group(1), m.group(2), m.group(3)
        attrs[key] = quoted if quoted is not None else bare
    return attrs


def hls_master(vid: str, sign: str = "") -> bytes:
    """HLS-мастер того же ролика — нужен только ради субтитров, DASH их не содержит.

    ID и подпись те же, что и у DASH: `master.m3u8` вместо `master.mpd`. Видео и звук
    отсюда не берём — это осталось за `manifest()`/`parse()`, дублировать незачем.
    """
    url = HLS_MASTER.format(video_id=vid) + (f"?{sign}" if sign else "")
    try:
        return _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and not sign:
            raise KinescopeError(
                f"HLS-мастер закрыт подписью ({vid}): 403. Нужны те же expires и "
                "sign, что и для DASH"
            ) from exc
        raise KinescopeError(f"HLS-мастер не отдался ({vid}): {exc}") from exc
    except Exception as exc:
        raise KinescopeError(f"HLS-мастер не отдался ({vid}): {exc}") from exc


def parse_subtitles(m3u8: bytes, vid: str) -> list[SubtitleTrack]:
    """Дорожки субтитров из строк `#EXT-X-MEDIA:TYPE=SUBTITLES` HLS-мастера.

    Пустой список — не ошибка: часть роликов субтитров не имеет вовсе, и это
    штатный случай, а не повод падать.

    `URI` в мастере относительный (`media.m3u8?id=…`), и склеивать его нужно с
    `https://kinescope.io/<video_id>/` — это подтверждено на живом мастере, где
    полный путь получался именно так.
    """
    base = f"{BASE}/{vid}/"
    tracks: list[SubtitleTrack] = []
    for line in m3u8.decode("utf-8", "replace").splitlines():
        if not line.startswith("#EXT-X-MEDIA:"):
            continue
        attrs = _hls_attrs(line)
        if (attrs.get("TYPE") or "").upper() != "SUBTITLES":
            continue
        uri = attrs.get("URI")
        if not uri:
            continue
        name = attrs.get("NAME", "")
        tracks.append(SubtitleTrack(
            lang=(attrs.get("LANGUAGE") or "").lower(),
            name=name,
            url=urljoin(base, uri),
            auto=bool(_AUTO_HINT.search(name)),
        ))
    return tracks


def pick_subtitle(tracks: list[SubtitleTrack], langs: tuple[str, ...] = ("ru", "en")) -> SubtitleTrack | None:
    """Выбирает дорожку по приоритету языков; внутри языка — ручную раньше авто."""
    for lang in langs:
        matches = [t for t in tracks if t.lang == lang]
        if matches:
            return min(matches, key=lambda t: t.auto)
    return None


def download_subtitle(track: SubtitleTrack, dest: str) -> str:
    """Скачивает готовую дорожку целиком в `.vtt`.

    Плейлист дорожки на практике — одна строка-ссылка на цельный `.vtt` (весь файл
    одним «сегментом», не нарезкой на куски), но закладываться на это нельзя: берём
    все строки, не начинающиеся с `#`, и если их несколько — склеиваем содержимое по
    порядку. Ссылки внутри плейлиста бывают и абсолютными, и относительными к самому
    плейлисту.
    """
    try:
        playlist = _get(track.url).decode("utf-8", "replace")
    except Exception as exc:
        raise KinescopeError(f"плейлист субтитров не отдался: {exc}") from exc

    urls = [
        urljoin(track.url, line.strip())
        for line in playlist.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not urls:
        raise KinescopeError("в плейлисте субтитров нет ни одной ссылки на сегмент")

    chunks = []
    for u in urls:
        try:
            chunks.append(_get(u))
        except Exception as exc:
            raise KinescopeError(f"не скачался сегмент субтитров: {exc}") from exc

    with open(dest, "wb") as fh:
        fh.write(b"".join(chunks))
    return dest


#: Сколько тянуть одним запросом. Сервер обрывает длинную выдачу: на 121 МБ это не
#: проявлялось, на 337 МБ соединение падает на середине. Просить кусками надёжнее, а
#: Range эта раздача поддерживает — отвечает 206 на первый же пробный запрос.
CHUNK = 32 << 20
RETRIES = 4


def download(track: Track, dest: str, *, progress=None, chunk: int = CHUNK) -> str:
    """Скачивает дорожку кусками, с докачкой. Готовый файл не перекачивается.

    Раньше здесь был один запрос на весь файл. На большом видео это ломается: сервер
    закрывает соединение на середине, и повторять приходилось с нуля. Теперь берём
    диапазонами и продолжаем с того места, где остановились, — недокачанный `.part`
    переживает и обрыв, и повторный запуск.
    """
    if os.path.exists(dest) and os.path.getsize(dest) == track.size:
        return dest

    tmp = dest + ".part"
    done = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    if done > track.size:                     # чужой или битый остаток — начинаем заново
        done = 0
    if progress and done:
        progress(done, track.size)

    with open(tmp, "r+b" if done else "wb") as fh:
        fh.seek(done)
        while done < track.size:
            end = min(done + chunk, track.size) - 1
            for attempt in range(RETRIES):
                try:
                    part = _get_range(track.url, done, end)
                    break
                except Exception as exc:
                    if attempt == RETRIES - 1:
                        raise KinescopeError(
                            f"обрыв загрузки на {done >> 20} МБ из {track.size >> 20}: {exc}. "
                            f"Недокачанное лежит в {tmp} — повторный запуск продолжит с него"
                        ) from exc
            fh.write(part)
            done += len(part)
            if progress:
                progress(done, track.size)
            if not part:                      # сервер молчит — дальше не продвинемся
                raise KinescopeError(f"сервер вернул пустой кусок на {done >> 20} МБ")

    os.replace(tmp, dest)
    return dest


def _get_range(url: str, start: int, end: int, *, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={
        "Referer": BASE,
        "User-Agent": "frameproof",
        "Range": f"bytes={start}-{end}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    # 200 вместо 206 означает, что диапазон проигнорировали и прислали файл целиком.
    if resp.status == 200 and start:
        return data[start : end + 1]
    return data
