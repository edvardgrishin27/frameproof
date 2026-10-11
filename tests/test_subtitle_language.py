# -*- coding: utf-8 -*-
"""Субтитры на языке самого ролика, а не машинный перевод.

Живой прогон 11.10.2026: у английского ролика `language` = `en-US`, ручных
субтитров нет, автосубтитров 177 дорожек, и пометка `-orig` стоит у двадцати
с лишним языков сразу. Порядок `("ru", "en")` выбирал `ru` — это машинный
перевод YouTube, а не речь автора: дословные цитаты по нему врут.
"""

from frameproof import fetch as F


def _дорожка(ключ):
    return [{"ext": "json3", "url": f"http://sub/{ключ}"}]


def _авто(*ключи):
    return {к: _дорожка(к) for к in ключи}


def test_английский_ролик_берёт_английский_оригинал():
    info = {"language": "en-US", "subtitles": {},
            "automatic_captions": _авто("ru", "ru-orig", "en", "en-orig", "de-orig")}
    url, язык, авто = F._pick_subtitle(info)
    assert язык == "en-orig" and авто, (url, язык)


def test_русский_ролик_берёт_русский():
    info = {"language": "ru", "subtitles": {},
            "automatic_captions": _авто("ru", "ru-orig", "en")}
    assert F._pick_subtitle(info)[1] == "ru-orig"


def test_без_языка_ролика_порядок_прежний():
    info = {"subtitles": {}, "automatic_captions": _авто("ru", "en")}
    assert F._pick_subtitle(info)[1] == "ru"


def test_ручные_на_языке_ролика_важнее_автоматических():
    info = {"language": "en", "subtitles": {"en": _дорожка("en")},
            "automatic_captions": _авто("ru", "en-orig")}
    url, язык, авто = F._pick_subtitle(info)
    assert (язык, авто) == ("en", False)


def test_язык_ролика_не_из_списка_тоже_берётся():
    """Испанский ролик: оригинал полезнее перевода на русский или английский."""
    info = {"language": "es", "subtitles": {},
            "automatic_captions": _авто("ru", "en", "es-orig")}
    assert F._pick_subtitle(info)[1] == "es-orig"
