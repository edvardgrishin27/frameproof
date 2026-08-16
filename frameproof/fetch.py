"""Загрузка видео и субтитров через yt-dlp.

Два решения, которые здесь важнее, чем кажутся.

1. СНАЧАЛА СУБТИТРЫ. Если у ролика есть готовая дорожка — она бесплатна, уже с
   тайм-кодами и обычно точнее ASR. Расшифровка запускается только когда их нет.

2. VIDEO-ONLY ПОТОК В ВЫСОКОМ РАЗРЕШЕНИИ. Прогрессивный формат у YouTube часто
   ограничен 360p — на нём мелкий текст в терминале и код нечитаемы, а именно они
   и нужны. Звук в этом файле не нужен: он уже взят субтитрами или отдельной дорожкой.

3. ПЕРЕБОР ФОРМАТОВ ПРИ 403. 403 у YouTube привязан не к видео, а к конкретному
   формату: на одном ролике, в одну минуту, одним клиентом `bv*[height<=1080]`
   скачивался (30 МБ), а `bv*[height<=720]` падал с 403 — при этом
   `b[height<=720]` (прогрессивный, тот же ролик и клиент) скачался нормально
   (23 МБ). yt-dlp выбирает формат по цепочке `a/b/c` один раз, по тому, что
   ЕСТЬ в метаданных, а не по тому, скачается ли он — запасные варианты цепочки
   без нашего вмешательства не пробуются. Решение — разложить цепочку в список
   и перебирать самим, отступая на следующий формат только при 403
   (`_это_запрет`); 429 по-прежнему идёт через `_с_повторами` без изменений,
   паузы и смена клиента тут ни при чём. Версия yt-dlp тоже влияет: 2026.03.17
   давала 403 на всех форматах этого ролика, 2026.07.04 — только на части; при
   старте загрузки версия сверяется с текущей датой, и если сильно устарела —
   печатается предупреждение (`_проверить_версию_ytdlp`). За сеть для сверки с
   реальной последней версией и за автообновление инструмент не отвечает —
   решение остаётся за человеком.

4. COOKIES ПРИ АУТЕНТИФИКАЦИОННОМ 403. Пункт 3 не спасает, если 403 держится
   не на конкретном формате, а на КАЖДОМ переспробованном варианте сразу —
   это уже не ограничение формата, а требование авторизации. `cookiesfrombrowser`
   у yt-dlp читает cookies живой сессии из установленного браузера и включается
   только явным флагом (`--cookies-from-browser`), никогда по умолчанию: это
   доступ к учётной записи YouTube, а не анонимная загрузка. YouTube известны
   случаи блокировки аккаунтов за автоматические загрузки с cookies — поэтому
   разумнее отдельный аккаунт, а не основной.

yt-dlp дёргается как БИБЛИОТЕКА, а не через CLI: в песочнице у CLI бывает
`curl: (6) Could not resolve host`, тогда как библиотечный путь работает.

Kinescope идёт мимо yt-dlp целиком — экстрактора там нет, а через манифест он качает
не то. Своя загрузка в `kinescope.py`: видео и звук берутся из DASH, а субтитры — из
HLS того же ролика (тот же ID и подпись, `master.m3u8` вместо `master.mpd`). Принцип
«сначала субтитры» из пункта 1 действует и здесь: DASH субтитров не содержит, но если
готовая дорожка нашлась в HLS, звук не качаем вовсе — расшифровывать по нему нечего.
Измерения и ссылки на заявки — там же, в `kinescope.py`.
"""

from __future__ import annotations

import datetime
import os
import sys
import time
import urllib.error
from dataclasses import dataclass, field

SUB_LANGS = ("ru", "en")

#: Браузеры, из которых yt-dlp умеет забирать cookies (`cookiesfrombrowser`).
#: Список — как в самом yt-dlp; держим отдельной константой, а не хардкодим
#: строкой в help CLI, чтобы `__main__.py` и текст ошибки на опечатке видели
#: один и тот же список.
БРАУЗЕРЫ_С_COOKIES = (
    "brave", "chrome", "chromium", "edge", "firefox",
    "opera", "safari", "vivaldi", "whale",
)


@dataclass
class Fetched:
    video_path: str
    title: str
    duration: float
    source_url: str
    subtitle_path: str | None = None
    subtitle_lang: str | None = None
    subtitle_auto: bool = False
    audio_path: str | None = None
    extra: dict = field(default_factory=dict)


def _ydl():
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "нужен yt-dlp: pip install yt-dlp  (или brew install yt-dlp)"
        ) from exc
    return yt_dlp


#: Паузы перед повторной попыткой, секунды. Короткие намеренно.
#:
#: Соблазн поставить долгое ожидание велик, но данными он не подтверждается: при
#: разборе трёх видео подряд YouTube отдал 429, паузы в 4 и 15 минут не дали
#: ничего, а смена клиента взяла файл с первой попытки. Значит лимит привязан не
#: ко времени, а к клиенту — ждать дольше бессмысленно, дешевле сменить клиента.
_ПАУЗЫ = (5, 15, 45)
#: Клиенты YouTube по порядку. `None` — как ходит yt-dlp по умолчанию.
#:
#: Своё, а не встроенные `retries`/`extractor_retries`/`sleep_interval` yt-dlp:
#: те умеют только ждать, а менять `player_client` между попытками — нет. То есть
#: дают ровно то, что здесь не сработало.
_КЛИЕНТЫ = (None, "android", "ios", "web_safari")


def _это_лимит(exc: Exception) -> bool:
    """Похоже ли на 429. На других ошибках повторять нечего — они не пройдут."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return True
    текст = str(exc)
    return "429" in текст or "Too Many Requests" in текст


def _это_запрет(exc: Exception) -> bool:
    """Похоже ли на 403. Это не лимит: соседний формат того же видео, в ту же
    минуту, тем же клиентом — скачивается нормально (см. шапку модуля, факт
    про 137 и 136). Паузы и смена клиента тут бессмысленны — помогает только
    смена формата, см. `_с_перебором_форматов`.
    """
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
        return True
    текст = str(exc)
    return "403" in текст or "Forbidden" in текст


def _с_повторами(сделать, что: str, *, спать=time.sleep):
    """Выполнить `сделать(доп_опции)`, повторяя при 429 и меняя клиента.

    Один механизм на все четыре точки загрузки — метаданные, субтитры, аудио и
    видео. Раньше повторов не было нигде: `ydl.download([url])` вызывался голым,
    и любая сетевая ошибка роняла команду целиком. Причём обёрнуто в `try` было
    только аудио, и то молча — при 429 расшифровка просто исчезала, а человек не
    узнавал почему.

    Возвращает `(результат, клиент)`: имя клиента нужно записать в лог, иначе
    потом не понять, откуда взялось другое качество картинки.
    """
    неудачи = []
    for клиент in _КЛИЕНТЫ:
        доп = ({} if клиент is None
               else {"extractor_args": {"youtube": {"player_client": [клиент]}}})
        for пауза in (0,) + _ПАУЗЫ:
            if пауза:
                спать(пауза)
            try:
                return сделать(доп), клиент
            except Exception as exc:
                if not _это_лимит(exc):
                    raise
                неудачи.append((клиент or "по умолчанию", пауза))
    потрачено = len(_КЛИЕНТЫ) * sum(_ПАУЗЫ)
    raise RuntimeError(
        f"{что}: YouTube отвечает 429 (слишком много запросов). "
        f"Попыток: {len(неудачи)}, клиенты: "
        f"{', '.join(dict.fromkeys(k for k, _ in неудачи))}. "
        f"Ждали суммарно {потрачено} с.\n"
        "  Что делать: подождите 10–20 минут и повторите, либо скачайте файл "
        "сами (`yt-dlp -f 'bv*[height<=1080]' <ссылка>`) и подайте путь к файлу "
        "вместо ссылки — frameproof принимает и то, и другое."
    )


#: Рунги понижения по высоте — последний шанс, когда 403 отдали и video-only,
#: и прогрессивный вариант на исходной высоте. Все — прогрессивные (`b`, не
#: `bv*`): живая проверка показала, что именно прогрессивный формат иногда
#: проходит там, где video-only на той же высоте падает с 403. 360p (itag 18
#: у YouTube) в конце — он существует почти всегда, это последний шанс.
_РУНГИ_ПОНИЖЕНИЯ = (720, 480, 360)


def _форматы_видео(max_height: int) -> list[str]:
    """Раскладывает прежнюю цепочку `a/b/c` в список попыток, лучший — первым.

    yt-dlp выбирает формат по цепочке `bv*.../bv*.../b...` один раз, по тому,
    что ЕСТЬ в метаданных, — а не по тому, скачается ли он. 403 прилетает
    позже, на этапе передачи байт, когда цепочка внутри yt-dlp уже
    отработала и откатиться на следующий вариант не может (см. шапку модуля,
    пункт 3). Поэтому раскладываем на отдельные попытки и перебираем сами —
    см. `_с_перебором_форматов`.

    В конец добавлены рунги понижения (`_РУНГИ_ПОНИЖЕНИЯ`) как последний
    шанс: на живой проверке `bv*[height<=720]` падал с 403, а
    `b[height<=720]` (прогрессивный, тот же ролик и клиент) — нет.
    """
    форматы = [
        f"bv*[height<={max_height}][ext=mp4]",
        f"bv*[height<={max_height}]",
        f"b[height<={max_height}]",
    ]
    форматы_ниже = [f"b[height<={h}]" for h in _РУНГИ_ПОНИЖЕНИЯ if h < max_height]
    return форматы + форматы_ниже


def _форматы_аудио() -> list[str]:
    """Раскладывает аудио-цепочку `ba/bestaudio/best` в список попыток.

    Тот же приём, что и в `_форматы_видео`, но экзотика тут не нужна: три
    исходных звена как отдельные попытки, порядок не меняется.
    """
    return ["ba", "bestaudio", "best"]


def _с_перебором_форматов(собрать, форматы, что: str, *, спать=time.sleep,
                           cookies_used: bool = False):
    """Перебирает `форматы` по очереди, уходя на следующий при 403.

    `собрать(формат)` должна вернуть функцию `сделать(доп)` — то же самое,
    что обычно передают в `_с_повторами`. Каждый формат по-прежнему проходит
    через `_с_повторами` целиком: 429 на конкретном формате всё так же
    лечится паузой и сменой клиента, это поведение не меняется и не
    трогается. Меняется то, что происходит, когда `_с_повторами` пробрасывает
    исключение дальше — если это 403 (`_это_запрет`), берём следующий формат
    из списка вместо того, чтобы ронять всё скачивание. Любая другая ошибка
    (включая исчерпание 429) падает сразу, как и раньше.

    `cookies_used` влияет только на текст ошибки при полном исчерпании: если
    cookies ещё не пробовали, в сообщении появляется подсказка про
    `--cookies-from-browser` — часть 403 держится не на формате, а на
    требовании авторизации (см. пункт 4 шапки модуля), и перебор форматов
    такое в принципе не лечит. Если cookies уже использовались, повторный
    совет — просто шум.

    Возвращает `(результат, клиент, формат)` — формат нужен вызывающему коду
    для лога, если сработал не первый (лучший) вариант.
    """
    неудачи = []
    for формат in форматы:
        try:
            результат, клиент = _с_повторами(собрать(формат), что, спать=спать)
            return результат, клиент, формат
        except Exception as exc:
            if not _это_запрет(exc):
                raise
            неудачи.append(формат)
    совет_cookies = "" if cookies_used else (
        " Ещё не пробовали: --cookies-from-browser <браузер> — часть 403 "
        "держится не на формате, а на требовании авторизации, перебор "
        "форматов это не лечит."
    )
    raise RuntimeError(
        f"{что}: YouTube отвечает 403 (Forbidden) на каждом из перепробованных "
        f"форматов. Форматов: {len(неудачи)}: {', '.join(неудачи)}.\n"
        "  Что делать: сначала обновите yt-dlp (`pip install -U yt-dlp`) — на "
        "живой проверке версия 2026.03.17 давала 403 на всех форматах этого "
        "ролика, а 2026.07.04 — только на части. Не помогло — скачайте файл "
        "сами (`yt-dlp -f 'b[height<=720]' <ссылка>`) и подайте путь к файлу "
        "вместо ссылки — frameproof принимает и то, и другое."
        f"{совет_cookies}"
    )


_COOKIES_ПОКАЗАНО = False


def _предупредить_о_cookies(browser: str) -> None:
    """Разовое предупреждение о приватности — один раз за процесс.

    cookies передаются на всех четырёх точках загрузки (метаданные, субтитры,
    аудио, видео) через один и тот же `_опции_cookies`, но предупреждать на
    каждой из них — значит показать человеку одно и то же до четырёх раз
    подряд на одну загрузку. Печатаем один раз, при первом использовании.

    cookies — это доступ к учётной записи YouTube, а не анонимная загрузка, и
    у этого есть отдельная цена: YouTube известны случаи блокировки аккаунтов
    за автоматические загрузки с cookies. Поэтому сразу совет про отдельный
    аккаунт, а не про основной — не расписываем подробнее, это предупреждение,
    а не инструкция.
    """
    global _COOKIES_ПОКАЗАНО
    if _COOKIES_ПОКАЗАНО:
        return
    _COOKIES_ПОКАЗАНО = True
    print(
        f"⚠ cookies браузера {browser}: используется доступ к вашей учётной "
        "записи YouTube, это не анонимная загрузка.",
        file=sys.stderr,
    )
    print(
        "  YouTube известны случаи блокировки аккаунтов за автоматические "
        "загрузки с cookies — используйте отдельный аккаунт, не основной.",
        file=sys.stderr,
    )


def _опции_cookies(browser: str | None) -> dict:
    """Опция yt-dlp для cookies из установленного браузера — или пустой dict.

    Пустой dict, когда `browser` не задан (в частности `None` по умолчанию):
    cookies не включаются НИ ПРИ КАКИХ условиях сами по себе, только по
    явному имени браузера. Значение валидируем сами, а не отдаём на откуп
    yt-dlp: его собственная ошибка на опечатке в имени — трейсбек про keyring
    и профили браузера, а не понятный человеку список поддерживаемых имён.
    """
    if not browser:
        return {}
    if browser not in БРАУЗЕРЫ_С_COOKIES:
        raise RuntimeError(
            f"неизвестный браузер для cookies: {browser!r}. "
            f"Поддерживаются: {', '.join(БРАУЗЕРЫ_С_COOKIES)}."
        )
    _предупредить_о_cookies(browser)
    return {"cookiesfrombrowser": (browser,)}


#: Порог устаревания yt-dlp, дней, после которого печатается предупреждение.
#:
#: yt-dlp выпускается часто — обычно раз в несколько недель, потому что
#: YouTube меняет обфускацию и старые версии перестают её понимать. Живой
#: пример из этого же модуля (см. шапку, пункт 3): версия 2026.03.17 давала
#: 403 на ВСЕХ форматах ролика, а 2026.07.04 — на том же ролике, 109 дней
#: спустя, — только на части. 120 дней (четыре месяца) — небольшой запас
#: сверх этого разрыва: предупреждать раньше жалко, два-три месяца
#: отставания обычно ещё терпимы, а к четырём версия уже в зоне риска,
#: которую живой случай показал наглядно.
_МАКС_ВОЗРАСТ_YTDLP_ДНЕЙ = 120

_ВЕРСИЯ_ПРОВЕРЕНА = False


def _проверить_версию_ytdlp(yt_dlp) -> None:
    """Предупреждает в stderr, если yt-dlp давно не обновлялся. Раз за процесс,
    не на каждую из четырёх точек загрузки — иначе то же предупреждение
    повторилось бы до четырёх раз на одну загрузку.

    Сверяем только дату из версии (`ГГГГ.ММ.ДД`) с текущей датой локально —
    в сеть за номером актуальной версии не ходим и обновлять сами не пытаемся,
    решение остаётся за человеком. Нестандартная строка версии (сборка из
    исходников, отсутствующий атрибут, лишний суффикс) не должна ронять
    разбор — на любой непонятной версии молча выходим.
    """
    global _ВЕРСИЯ_ПРОВЕРЕНА
    if _ВЕРСИЯ_ПРОВЕРЕНА:
        return
    _ВЕРСИЯ_ПРОВЕРЕНА = True
    try:
        версия = yt_dlp.version.__version__
        год, месяц, день = (int(x) for x in версия.split(".")[:3])
        возраст_дней = (datetime.date.today() - datetime.date(год, месяц, день)).days
    except (AttributeError, ValueError, TypeError):
        return
    if возраст_дней > _МАКС_ВОЗРАСТ_YTDLP_ДНЕЙ:
        print(
            f"⚠ yt-dlp {версия} не обновлялся {возраст_дней} дн. YouTube "
            "регулярно ломает старые версии (см. шапку модуля про 403 на "
            "всех форматах у версии 2026.03.17) — обновите: "
            "pip install -U yt-dlp",
            file=sys.stderr,
        )


def probe_remote(url: str, *, extra_opts: dict | None = None) -> dict:
    """`extra_opts` — статичные опции yt-dlp, домешиваются в каждую попытку.

    Сюда приходят cookies из `fetch()` (см. `_опции_cookies`): `probe_remote`
    сам про cookies ничего не знает и знать не должен — это просто ещё один
    словарь опций, как и везде в модуле.
    """
    yt_dlp = _ydl()
    статичные = extra_opts or {}

    def взять(доп):
        with yt_dlp.YoutubeDL({
            "quiet": True, "skip_download": True, **статичные, **доп,
        }) as ydl:
            return ydl.extract_info(url, download=False)

    info, _ = _с_повторами(взять, "метаданные")
    return info


def _pick_subtitle(info: dict, langs=SUB_LANGS) -> tuple[str | None, str | None, bool]:
    """Возвращает (url, язык, авто?). Ручные субтитры приоритетнее автоматических."""
    for auto in (False, True):
        table = info.get("automatic_captions" if auto else "subtitles") or {}
        for lang in langs:
            for key in (lang, f"{lang}-orig"):
                tracks = table.get(key)
                if not tracks:
                    continue
                # json3 предпочтительнее vtt: у авто-субтитров YouTube в vtt
                # строки «катятся» и дублируются.
                for ext in ("json3", "vtt", "srv3", "ttml"):
                    for tr in tracks:
                        if tr.get("ext") == ext and tr.get("url"):
                            return tr["url"], key, auto
                if tracks and tracks[0].get("url"):
                    return tracks[0]["url"], key, auto
    return None, None, False


def fetch(url: str, work_dir: str, *, max_height: int = 1080,
          langs=SUB_LANGS, want_video: bool = True, want_audio: bool = True,
          cookies_from_browser: str | None = None) -> Fetched:
    """Скачивает метаданные, субтитры (если есть), аудио и/или видео.

    `cookies_from_browser` — имя установленного браузера (`chrome`, `firefox`,
    ... — полный список см. в `БРАУЗЕРЫ_С_COOKIES`) для `cookiesfrombrowser`
    у yt-dlp: НЕ путь к файлу с cookies, а прямое чтение живой сессии из
    браузера. Это доступ к вашей учётной записи YouTube, а не анонимная
    загрузка — по умолчанию `None` (выключено) и стоит включать только когда
    403 держится на каждом переспробованном формате (см.
    `_с_перебором_форматов`), то есть похоже на требование авторизации, а не
    ограничение конкретного формата (см. шапку модуля, пункты 3 и 4).
    """
    os.makedirs(work_dir, exist_ok=True)

    from .kinescope import is_kinescope

    if is_kinescope(url):
        return _fetch_kinescope(
            url, work_dir, max_height=max_height, want_video=want_video,
            want_audio=want_audio, langs=langs,
        )

    yt_dlp = _ydl()
    _проверить_версию_ytdlp(yt_dlp)
    cookies_opts = _опции_cookies(cookies_from_browser)
    info = probe_remote(url, extra_opts=cookies_opts)

    sub_path = None
    sub_url, sub_lang, sub_auto = _pick_subtitle(info, langs)
    if sub_url:
        def взять_субтитры(доп):
            with yt_dlp.YoutubeDL({"quiet": True, **cookies_opts, **доп}) as ydl:
                return ydl.urlopen(sub_url).read()

        raw, _ = _с_повторами(взять_субтитры, "субтитры")
        ext = "json3" if b'"events"' in raw[:400] else "vtt"
        sub_path = os.path.join(work_dir, f"subs.{sub_lang}.{ext}")
        with open(sub_path, "wb") as fh:
            fh.write(raw)

    # Видео тянем БЕЗ звука — так поток чётче при том же весе. Но если субтитров
    # не нашлось, расшифровывать будет нечего, поэтому аудиодорожку берём отдельно.
    audio_path = None
    if not sub_path:
        audio_path = os.path.join(work_dir, "audio.m4a")
        if not os.path.exists(audio_path):
            def собрать_аудио(формат):
                def взять(доп):
                    with yt_dlp.YoutubeDL({
                        "quiet": True, "no_warnings": True,
                        "outtmpl": audio_path, "format": формат,
                        **cookies_opts, **доп,
                    }) as ydl:
                        return ydl.download([url])
                return взять

            try:
                _, клиент, _формат = _с_перебором_форматов(
                    собрать_аудио, _форматы_аудио(), "аудиодорожка",
                    cookies_used=bool(cookies_from_browser),
                )
                if клиент:
                    print(f"аудио взято клиентом {клиент}", file=sys.stderr)
            except Exception as exc:
                # Раньше ошибка глоталась молча, и расшифровка просто исчезала —
                # человек видел индекс без транскрипта и не знал почему.
                print(f"не удалось скачать аудио: {exc}", file=sys.stderr)
                print("  индекс соберётся, но без расшифровки речи", file=sys.stderr)
                audio_path = None
        if audio_path and not os.path.exists(audio_path):
            audio_path = next(
                (os.path.join(work_dir, n) for n in os.listdir(work_dir)
                 if n.startswith("audio.") and not n.endswith((".part", ".ytdl"))),
                None,
            )

    video_path = ""
    if want_video:
        video_path = os.path.join(work_dir, "video.mp4")
        if not os.path.exists(video_path):
            opts = {
                "quiet": True,
                "no_warnings": True,
                "outtmpl": video_path,
                **cookies_opts,
            }
            def собрать_видео(формат):
                def взять(доп):
                    # Только видеодорожка: звук здесь не нужен, а качество
                    # картинки нужно (кроме нижних рунгов — там уже неважно,
                    # лишь бы прошло, см. _форматы_видео).
                    with yt_dlp.YoutubeDL({**opts, "format": формат, **доп}) as ydl:
                        return ydl.download([url])
                return взять

            форматы = _форматы_видео(max_height)
            _, клиент, формат = _с_перебором_форматов(
                собрать_видео, форматы, "видео", cookies_used=bool(cookies_from_browser)
            )
            if клиент:
                # Клиенты отдают разные наборы форматов: без этой строки потом не
                # понять, почему картинка вышла хуже запрошенной.
                print(f"видео взято клиентом {клиент}", file=sys.stderr)
            if формат != форматы[0]:
                # Лучший формат отдал 403, и в ход пошёл запасной — без этой
                # строки потом не понять, почему картинка вышла хуже запрошенной.
                print(f"видео: лучший формат отдал 403, взят запасной {формат!r}",
                      file=sys.stderr)
        if not os.path.exists(video_path):
            for name in os.listdir(work_dir):
                if name.startswith("video.") and not name.endswith((".part", ".ytdl")):
                    video_path = os.path.join(work_dir, name)
                    break

    return Fetched(
        video_path=video_path,
        title=str(info.get("title") or "video"),
        duration=float(info.get("duration") or 0.0),
        source_url=url,
        subtitle_path=sub_path,
        subtitle_lang=sub_lang,
        subtitle_auto=sub_auto,
        audio_path=audio_path,
        extra={
            "id": info.get("id"),
            "channel": info.get("channel"),
            "upload_date": info.get("upload_date"),
        },
    )


def _fetch_kinescope(url: str, work_dir: str, *, max_height: int,
                     want_video: bool, want_audio: bool = True,
                     langs=SUB_LANGS) -> Fetched:
    """Kinescope мимо yt-dlp: экстрактора там нет, а через манифест он качает не то.

    Подробности устройства и ссылки на заявки — в `kinescope.py`. Субтитров в
    DASH-манифесте нет вовсе, но тот же ролик отдаётся и по HLS, и там для части
    видео есть готовая дорожка — берём её оттуда тем же порядком, что и на
    YouTube-ветке выше: сначала субтитры, и если нашлись — звук не качаем вовсе,
    расшифровывать по нему было бы нечего.
    """
    import sys

    from . import kinescope as ks

    vid, sign = ks.video_id(url)
    tracks, duration = ks.parse(ks.manifest(vid, sign))

    def show(label: str, size: int):
        def cb(done: int, total: int):
            if total and (done % (16 << 20) < (1 << 20) or done >= total):
                print(f"\r{label}: {done * 100 // total} % из {total >> 20} МБ",
                      end="", file=sys.stderr, flush=True)
        return cb

    video_path = ""
    if want_video:
        track = ks.pick_video(tracks, max_height)
        print(f"kinescope: {track.height}p, {track.size >> 20} МБ", file=sys.stderr)
        video_path = ks.download(
            track, os.path.join(work_dir, "video.mp4"), progress=show("видео", track.size)
        )
        print("", file=sys.stderr)

    # СНАЧАЛА СУБТИТРЫ, тем же принципом, что и в YouTube-ветке этого модуля. DASH
    # их не отдаёт вовсе, поэтому идём в HLS того же ролика (тот же ID и подпись).
    # Субтитры — бонус, а не обязательная часть разбора: любая ошибка здесь (нет
    # HLS, 403, кривой плейлист, оборвалась загрузка) не должна ронять видео —
    # раньше на YouTube-аудио такая ошибка глоталась молча, и это было прямой
    # ошибкой прошлой редакции, повторять её здесь незачем.
    sub_path = sub_lang = None
    sub_auto = False
    try:
        sub_tracks = ks.parse_subtitles(ks.hls_master(vid, sign), vid)
        chosen = ks.pick_subtitle(sub_tracks, langs)
        if chosen:
            sub_path = ks.download_subtitle(
                chosen, os.path.join(work_dir, f"subs.{chosen.lang}.vtt")
            )
            sub_lang, sub_auto = chosen.lang, chosen.auto
    except Exception as exc:
        print(f"не удалось получить субтитры: {exc}", file=sys.stderr)
        print("  расшифровка пойдёт по звуку, если он нужен", file=sys.stderr)

    # Звук весит как половина видео. Не качаем его, если явно попросили не качать
    # (want_audio=False — на это решение субтитры не влияют) или если субтитры уже
    # нашлись (тогда расшифровывать по звуку незачем — ровно как на YouTube).
    audio_path = None
    audio = ks.pick_audio(tracks) if (want_audio and not sub_path) else None
    if audio:
        audio_path = ks.download(
            audio, os.path.join(work_dir, "audio.m4a"), progress=show("звук", audio.size)
        )
        print("", file=sys.stderr)

    return Fetched(
        video_path=video_path,
        title=f"kinescope {vid}",
        duration=duration,
        source_url=url,
        subtitle_path=sub_path,
        subtitle_lang=sub_lang,
        subtitle_auto=sub_auto,
        audio_path=audio_path,
        extra={"id": vid, "host": "kinescope"},
    )
