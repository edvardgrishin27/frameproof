# -*- coding: utf-8 -*-
"""JS-runtime: без него YouTube не отдаёт форматы, и об этом никто не предупреждает.

Живой отзыв: «n challenge solving failed», «No supported JavaScript runtime could
be found. Only deno is enabled by default». В требованиях deno не упомянут,
в doctor не проверяется, а опции пробросить нечем.

Автор инструмента этого не видел ровно потому, что deno у него стоит.
"""

import shutil

import pytest

from frameproof import fetch as F


def test_deno_есть_берём_его(monkeypatch):
    monkeypatch.setattr(F.shutil, "which", lambda b: "/usr/bin/" + b if b == "deno" else None)
    assert F._опции_js_runtime(None) == {}, (
        "при доступном deno ничего подмешивать не надо: он и так по умолчанию")


def test_deno_нет_а_node_есть_подставляем_node(monkeypatch):
    """У большинства node стоит, и инструмент обязан заработать сам, без флагов."""
    monkeypatch.setattr(F.shutil, "which", lambda b: "/usr/bin/node" if b == "node" else None)
    opts = F._опции_js_runtime(None)
    assert "js_runtimes" in opts, "node не подхвачен: %s" % opts
    assert "node" in opts["js_runtimes"]


def test_ни_одного_рантайма_молчим(monkeypatch):
    """Выдумывать несуществующий бинарь нельзя: yt-dlp сам скажет понятнее."""
    monkeypatch.setattr(F.shutil, "which", lambda b: None)
    assert F._опции_js_runtime(None) == {}


def test_явный_флаг_побеждает(monkeypatch):
    monkeypatch.setattr(F.shutil, "which", lambda b: "/usr/bin/" + b)
    opts = F._опции_js_runtime("bun")
    assert "bun" in opts["js_runtimes"], opts


def test_fetch_принимает_js_runtime():
    import inspect
    assert "js_runtime" in inspect.signature(F.fetch).parameters


def test_doctor_проверяет_рантайм():
    """Человек должен узнать о нехватке из doctor, а не из ошибки посреди загрузки."""
    import inspect
    from frameproof import __main__ as M
    src = inspect.getsource(M)
    assert "deno" in src, "doctor не проверяет наличие JS-runtime"


def test_требование_названо_в_readme():
    import io, os
    КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    t = io.open(os.path.join(КОРЕНЬ, "README.md"), encoding="utf-8").read()
    assert "deno" in t.lower(), "требование JS-runtime не названо в README"
