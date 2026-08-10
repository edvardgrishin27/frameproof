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

Качаем при этом не одним запросом, а кусками по 32 МБ с докачкой: на 121 МБ одиночный
запрос проходил, на 337 МБ сервер рвёт соединение на середине.

Ходим обычным urllib: тащить `requests` в зависимости ради двух GET незачем.

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

BASE = "https://kinescope.io"
MASTER = BASE + "/{video_id}/master.mpd"
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
            media = [s.get("media", "") for s in rep.findall(".//m:SegmentURL", NS)]
            if not base or not media:
                continue
            # Путь сегмента — «<начало>/<конец>/<файл>». Самый большой «конец» и есть
            # размер всей дорожки, а «0/<размер>/<файл>» сервер отдаёт одним куском.
            total, name, query = 0, "", ""
            for m in media:
                head, _, tail = m.partition("?")
                parts = head.split("/")
                if len(parts) < 3 or not parts[1].isdigit():
                    continue
                if int(parts[1]) > total:
                    total, name, query = int(parts[1]), parts[-1], tail
            if not total:
                continue
            url = f"{base}0/{total}/{name}" + (f"?{query}" if query else "")
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
