"""Тихие сбои: всё, что раньше проглатывалось или вводило в заблуждение.

Каждый тест здесь — случай, где инструмент молчал о поломке или называл её
не своим именем.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")


def _clip(path, sec=3, size="320x180", *extra):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=s={size}:r=25:d={sec}", "-pix_fmt", "yuv420p", *extra, str(path)],
                   check=True)
    return str(path)


# --- OCR -----------------------------------------------------------------------

@needs_ffmpeg
def test_сбой_распознавателя_называется_вслух(tmp_path, capsys):
    """Раньше партия с ненулевым кодом пропускалась без единого слова."""
    from frameproof.__main__ import main

    bad = tmp_path / "bad_ocr.py"
    bad.write_text("import sys; sys.stderr.write('движок упал'); sys.exit(3)\n",
                   encoding="utf-8")
    clip = _clip(tmp_path / "v.mp4")
    main(["index", clip, "--out", str(tmp_path / "i"), "--no-transcribe", "--ocr",
          "--ocr-command", f'"{sys.executable}" "{bad}"'])
    err = capsys.readouterr().err
    assert "распознавание не отработало" in err
    assert "движок упал" in err


def test_бинарь_ocr_общий_на_все_индексы(tmp_path, monkeypatch):
    """Свифтовый распознаватель собирался заново в каждой папке индекса."""
    from frameproof import ocr

    monkeypatch.setenv("FRAMEPROOF_HOME", str(tmp_path / "home"))
    assert ocr.binary_cache_dir() == str(tmp_path / "home" / ".cache")


# --- Расшифровка ---------------------------------------------------------------

def test_падение_движка_расшифровки_не_выдаётся_за_его_отсутствие(monkeypatch):
    from frameproof import transcribe as T

    def boom(audio, lang):
        raise MemoryError("не хватило памяти под модель")

    monkeypatch.setattr(T, "_mlx_whisper", boom)
    monkeypatch.setattr(T, "_openai_whisper", lambda a, l: None)
    with pytest.raises(RuntimeError) as e:
        T.transcribe_audio("x.wav")
    assert "не хватило памяти" in str(e.value)
    assert "не нашёл локального движка" not in str(e.value)


# --- Анализ ----------------------------------------------------------------------

@needs_ffmpeg
def test_оборванный_декод_называется_вслух(tmp_path, capsys):
    """Обрезанный файл анализировался до места обрыва, дальше — тишина."""
    from frameproof.__main__ import main

    # faststart: оглавление в начале файла, и первая половина остаётся читаемой.
    whole = _clip(tmp_path / "whole.mp4", 60, "320x180", "-movflags", "+faststart")
    cut = tmp_path / "cut.mp4"
    data = open(whole, "rb").read()
    cut.write_bytes(data[: len(data) // 2])
    main(["index", str(cut), "--out", str(tmp_path / "i"), "--no-transcribe"])
    err = capsys.readouterr().err
    assert "декод" in err and "оборвал" in err


def test_быстрый_режим_не_смешивает_тайм_коды():
    """Меток pts меньше, чем кадров: хвост раньше получал idx/fps — не время."""
    from frameproof.analyze import FastModeUnreliable, _keyframe_times

    with pytest.raises(FastModeUnreliable):
        _keyframe_times(stamps=[0.0, 2.0], decoded=5)
    assert _keyframe_times(stamps=[0.0, 2.0, 4.0], decoded=3) == [0.0, 2.0, 4.0]


# --- Потолок кадров на длинных роликах ---------------------------------------------

def test_потолок_по_умолчанию_не_спорит_с_гарантией():
    """3 часа при гарантии 15 с — это минимум 720 кадров, а потолок был 600.

    Отчёт всегда писал «потолок не выдержан», хотя ничего не просили.
    """
    import numpy as np

    from frameproof.analyze import Signal
    from frameproof.select import select_frames

    n = 10
    sig = Signal(times=np.linspace(0, 3 * 3600, n), cells=np.zeros((n, 3, 3)),
                 change_global=np.zeros(n), thumbs=np.zeros((n, 36, 64), dtype=np.uint8),
                 width=320, height=180, fps=1.0)
    sel = select_frames(sig, 3 * 3600.0)
    assert not sel.cap_exceeded
    assert not sel.gaps


# --- Сообщения -------------------------------------------------------------------

def test_report_без_индекса_подсказывает(tmp_path, capsys):
    from frameproof.__main__ import main

    assert main(["report", "--out", str(tmp_path / "нет")]) == 2
    assert "нет индекса" in capsys.readouterr().err


def test_doctor_проверяет_то_что_вызывает(monkeypatch, capsys):
    """Код зовёт бинарь whisper, а doctor проверял модуль."""
    from frameproof.__main__ import main

    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda b: None if b == "whisper" else real(b))
    main(["doctor"])
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "whisper " in ln and "mlx" not in ln)
    assert "✓" not in line


def test_подсказка_установки_по_системе(monkeypatch):
    from frameproof import util

    monkeypatch.setattr(util.sys, "platform", "win32")
    with pytest.raises(util.ToolMissing) as e:
        monkeypatch.setattr(util.shutil, "which", lambda t: None)
        util.which("ffmpeg")
    assert "winget" in str(e.value) and "brew" not in str(e.value)


@needs_ffmpeg
def test_файл_с_минусом_в_начале_имени(tmp_path, monkeypatch):
    """`-clip.mp4` без пути ffprobe читал как ключ командной строки."""
    from frameproof.probe import probe

    _clip(tmp_path / "-clip.mp4")
    monkeypatch.chdir(tmp_path)
    assert probe("-clip.mp4").duration > 0


def test_gitignore_без_комментариев_в_строке_шаблона():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as fh:
        for line in fh:
            body = line.strip()
            assert not (body and not body.startswith("#") and " #" in body), (
                f"git не понимает комментарий в строке шаблона: {body!r}")


@needs_ffmpeg
def test_нечитаемый_файл_называется_по_человечески(tmp_path, capsys):
    """Обрезанный mp4 без оглавления: раньше печатался сырой дамп команды ffprobe."""
    from frameproof.__main__ import main

    whole = _clip(tmp_path / "whole.mp4", 10)
    cut = tmp_path / "cut.mp4"
    data = open(whole, "rb").read()
    cut.write_bytes(data[: len(data) // 2])
    assert main(["index", str(cut), "--out", str(tmp_path / "i"), "--no-transcribe"]) == 1
    err = capsys.readouterr().err
    assert "не смог прочитать" in err and "Command" not in err
