"""Kinescope — своя загрузка, потому что через yt-dlp она сегодня не работает.

Kinescope это российский видеохостинг, на нём лежат курсы, вебинары и корпоративные
записи, то есть ровно тот материал, ради которого инструмент и писался. Экстрактора
в yt-dlp нет: заявка `yt-dlp#3391` открыта с 2022 года, страница отдаёт «Unsupported
URL». Обойти это подсовыванием манифеста тоже не выходит — форматы yt-dlp перечислит,
но скачает не то, см. ниже.

Как устроена отдача. Ключей и авторизации не нужно:

    https://kinescope.io/<video_id>/master.mpd

DASH-манифест, `SegmentList` с байтовыми диапазонами. Все «сегменты» указывают на один
и тот же файл: у 82-минутной лекции 1243 записи `SegmentURL` и от двух до семи
уникальных URL. Путь внутри — `<начало>/<конец>/720p.mp4`, то есть буквально границы
куска в байтах. Отсюда открытый баг `yt-dlp#12687`: загрузчик берёт `media`, не берёт
`mediaRange` и качает файл целиком на каждый из 1243 «сегментов». Живая проверка на
том же ролике: yt-dlp насчитал 96 ГБ там, где видео весит 121 МБ.

Из устройства путей следует простое решение. Сервер честно отдаёт любой диапазон,
который у него попросят, поэтому весь файл берётся ОДНИМ запросом по `0/<размер>/`.
Проверено: `Content-Length` совпадает с размером из пути, длительность скачанного
совпадает с заявленной в манифесте, кадр с 40-й минуты достаётся за 0.09 с.

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
_ID_IN_PAGE = re.compile(r'id:\s*"([0-9a-f-]{36}|\d{6,})"', re.I)


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


def video_id(url: str) -> str:
    m = _ID.search(url)
    if m:
        return m.group(1)
    # Ссылка-обёртка: идентификатор лежит в разметке плеера.
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
    return m.group(1)


def _get(url: str, *, referer: str = BASE, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"Referer": referer, "User-Agent": "frameproof"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def manifest(vid: str) -> bytes:
    try:
        return _get(MASTER.format(video_id=vid))
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


def download(track: Track, dest: str, *, progress=None) -> str:
    """Скачивает дорожку целиком. Готовый файл того же размера не перекачивается."""
    if os.path.exists(dest) and os.path.getsize(dest) == track.size:
        return dest
    tmp = dest + ".part"
    req = urllib.request.Request(track.url, headers={"Referer": BASE, "User-Agent": "frameproof"})
    done = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, track.size)
    os.replace(tmp, dest)
    return dest
