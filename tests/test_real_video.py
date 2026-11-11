"""Сквозные проверки на настоящем ffmpeg и синтетических роликах.

Юнит-тесты отбора кадров не видят того, что ломается на живом файле: длительность
контейнера против длительности дорожки, сбой одного кадра, поворот с телефона.
Ролики собираются тут же из `lavfi`, сеть не нужна. Нет ffmpeg — тесты пропускаются.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="нужны ffmpeg и ffprobe",
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _clip(path, *, video_sec: float, audio_sec: float | None = None,
          size: str = "320x180", src: str = "testsrc") -> str:
    """Ролик с картинкой video_sec и, если задано, звуком audio_sec."""
    args = ["-f", "lavfi", "-i", f"{src}=s={size}:r=25:d={video_sec}"]
    if audio_sec:
        args += ["-f", "lavfi", "-i", f"sine=f=440:d={audio_sec}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_sec:
        args += ["-c:a", "aac"]
    _ffmpeg(*args, str(path))
    return str(path)


def _index(target: str, out: str, *extra: str) -> int:
    from frameproof.__main__ import main

    return main(["index", target, "--out", out, "--no-transcribe", *extra])


# --- Длительность: по видеодорожке, а не по контейнеру --------------------------

def test_длительность_берётся_по_видеодорожке(tmp_path):
    """Звук на 30 с, картинка на 20 с. Кадров после 20 с не бывает.

    Так выглядит почти любая запись экрана или созвона, где звук пишут дольше
    картинки. Длительность контейнера здесь 30 с, и кадры, поставленные после
    конца видео, роняли ffmpeg с кодом 234 — а с ним весь индекс.
    """
    from frameproof.probe import probe

    path = _clip(tmp_path / "v.mp4", video_sec=20, audio_sec=30)
    assert abs(probe(path).duration - 20.0) < 0.5


def test_звук_длиннее_картинки_не_роняет_индекс(tmp_path):
    path = _clip(tmp_path / "v.mp4", video_sec=200, audio_sec=300)
    out = str(tmp_path / "idx")

    assert _index(path, out) == 0
    with open(os.path.join(out, "index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)
    assert idx["video"]["duration_sec"] < 201
    assert idx["frames"]["count"] > 0


# --- Покрытие: по снятым кадрам, а не по плану -----------------------------------

def test_неснятый_кадр_не_роняет_извлечение(tmp_path):
    """Кадр, который ffmpeg снять не смог, пропускается, а не бросает исключение."""
    from frameproof.extract import extract
    from frameproof.probe import probe
    from frameproof.select import Pick

    info = probe(_clip(tmp_path / "v.mp4", video_sec=5))
    picks = [Pick(t=1.0, reason="change", change=0.1),
             Pick(t=50.0, reason="grid", change=0.0),     # далеко за концом ролика
             Pick(t=3.0, reason="change", change=0.1)]
    frames = extract(info, picks, str(tmp_path / "frames"))

    assert [f.t for f in frames] == [1.0, 3.0]


def test_покрытие_считается_по_снятым_кадрам():
    """План закрыт сеткой без разрывов. Если кадр не сняли — разрыв настоящий.

    Раньше покрытие писалось из плана, и `gaps` был пуст по построению: отчёт
    гордо писал 100 % даже там, где кадров не получилось.
    """
    from frameproof.report import coverage_lines
    from frameproof.select import Pick, Selection, keep_extracted

    picks = [Pick(t=float(t), reason="grid", change=0.0) for t in range(0, 100, 10)]
    plan = Selection(picks=picks, duration=100.0, max_gap_target=15.0, gaps=[],
                     dropped_duplicates=0, dropped_budget=0)
    assert plan.gaps == []

    got = keep_extracted(plan, [p.t for p in picks if p.t not in (40.0, 50.0)])

    assert got.missing == 2
    assert got.gaps == [(30.0, 60.0)]
    text = "\n".join(coverage_lines(got))
    assert "БЕЗ КАДРА" in text and "0:30" in text
    assert "не удалось снять 2 кадра" in text


def test_ни_одного_кадра_это_ошибка(tmp_path):
    """Если не снялось ничего, это не «покрытие 0 %», а поломка: сказать прямо."""
    from frameproof.extract import extract
    from frameproof.probe import VideoInfo
    from frameproof.select import Pick

    fake = VideoInfo(path=str(tmp_path / "нет-такого.mp4"), duration=10.0, width=320,
                     height=180, fps=25.0, vfr=False, codec="h264")
    with pytest.raises(RuntimeError, match="ни одного кадра"):
        extract(fake, [Pick(t=1.0, reason="grid", change=0.0)], str(tmp_path / "f"))


# --- Папка индекса и звук: чужое не переиспользуется -----------------------------

def test_одноимённые_файлы_из_разных_папок_получают_разные_индексы(tmp_path, monkeypatch):
    """`recording.mp4` из двух разных созвонов писались в одну папку.

    Первый индекс молча перезаписывался, а звук второго брался от первого.
    """
    from frameproof.__main__ import _work_dir

    monkeypatch.setenv("FRAMEPROOF_HOME", str(tmp_path / "home"))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _clip(tmp_path / "a" / "recording.mp4", video_sec=2)
    b = _clip(tmp_path / "b" / "recording.mp4", video_sec=3)

    assert _work_dir(a, None) != _work_dir(b, None)
    assert _work_dir(a, None) == _work_dir(a, None), "тот же файл — та же папка"
    assert os.path.basename(_work_dir(a, None)).startswith("recording-")
    assert _work_dir(a, str(tmp_path / "явно")) == str(tmp_path / "явно")


def test_звук_извлекается_заново_для_другого_источника(tmp_path):
    """В папке уже лежит audio16k.wav от другого ролика — его нельзя брать."""
    from frameproof.__main__ import _extract_audio
    from frameproof.probe import _ratio
    from frameproof.util import run

    out = str(tmp_path / "idx")
    os.makedirs(out)
    a = _clip(tmp_path / "a.mp4", video_sec=3, audio_sec=3)
    b = _clip(tmp_path / "b.mp4", video_sec=8, audio_sec=8)

    def seconds(path: str) -> float:
        proc = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "csv=p=0", path])
        return _ratio(proc.stdout.strip())

    assert abs(seconds(_extract_audio(a, out)) - 3.0) < 0.3
    assert abs(seconds(_extract_audio(b, out)) - 8.0) < 0.3


def test_источник_без_звука_не_наследует_чужой(tmp_path):
    from frameproof.__main__ import _extract_audio

    out = str(tmp_path / "idx")
    os.makedirs(out)
    assert _extract_audio(_clip(tmp_path / "a.mp4", video_sec=3, audio_sec=3), out)
    assert _extract_audio(_clip(tmp_path / "немой.mp4", video_sec=3), out) is None


# --- Субтитры: VTT без часов и пустой файл ---------------------------------------

def test_vtt_без_часов_читается():
    """WebVTT разрешает `ММ:СС.ммм`. Такие файлы отдаёт Loom и пишут руками.

    Раньше разбор требовал часы и терял все реплики молча: «0 реплик».
    """
    from frameproof.transcribe import parse_vtt

    raw = ("WEBVTT\n\n00:01.000 --> 00:04.500\nhello &amp; world\n\n"
           "01:02.250 --> 01:05.000\nsecond\n\n1:00:00.000 --> 1:00:02.000\nthird\n")
    segs = parse_vtt(raw)

    assert [(s.t0, s.t1, s.text) for s in segs] == [
        (1.0, 4.5, "hello & world"), (62.25, 65.0, "second"), (3600.0, 3602.0, "third")]


def test_пустые_субтитры_не_выдаются_за_транскрипт(tmp_path, capsys):
    """0 реплик — это «субтитров нет», а не «транскрипт есть, пустой».

    Иначе расшифровка не запускается, а агенту искать по речи нечему, и никто
    об этом не говорит.
    """
    subs = tmp_path / "пусто.vtt"
    subs.write_text("WEBVTT\n\n", encoding="utf-8")
    clip = _clip(tmp_path / "v.mp4", video_sec=3)
    out = str(tmp_path / "idx")

    assert _index(clip, out, "--subs", str(subs)) == 0

    err = capsys.readouterr().err
    assert "ни одной реплики" in err
    with open(os.path.join(out, "index.json"), encoding="utf-8") as fh:
        assert json.load(fh)["transcript"]["segment_count"] == 0


# --- Поворот: видео с телефона ---------------------------------------------------

def test_повёрнутое_видео_снимается_без_искажения(tmp_path):
    """Телефон пишет 640x360 и метку «повернуть на 90°». ffmpeg поворачивает сам.

    Раньше размер кадра брался из дорожки как есть, `scale=640:360` сплющивал
    уже повёрнутую картинку 360x640, и кадр выходил растянутым боком.
    """
    from frameproof.extract import extract
    from frameproof.probe import probe
    from frameproof.select import Pick
    from frameproof.util import run

    plain = _clip(tmp_path / "plain.mp4", video_sec=3, size="640x360")
    rotated = str(tmp_path / "rot.mp4")
    _ffmpeg("-display_rotation", "90", "-i", plain, "-c", "copy", rotated)

    info = probe(rotated)
    assert (info.width, info.height) == (360, 640)

    frame = extract(info, [Pick(t=1.0, reason="grid", change=0.0)], str(tmp_path / "f"))[0]
    real = run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                "-of", "csv=p=0", frame.path]).stdout.strip()
    assert real == f"{frame.width},{frame.height}"
    assert frame.width < frame.height


def test_vtt_минуты_из_одной_цифры():
    from frameproof.transcribe import parse_vtt

    segs = parse_vtt("WEBVTT\n\n1:05.000 --> 1:07.000\nloom style\n")
    assert [(s.t0, s.t1) for s in segs] == [(65.0, 67.0)]


def test_оборванное_извлечение_звука_не_становится_кэшем(tmp_path, monkeypatch):
    """ffmpeg упал на середине, обрезок остался на диске — брать его нельзя."""
    import frameproof.util as util
    from frameproof import __main__ as cli

    out = str(tmp_path / "idx")
    os.makedirs(out)
    src = _clip(tmp_path / "a.mp4", video_sec=3, audio_sec=3)
    real_run = util.run

    def broken(cmd, **kw):
        proc = real_run(cmd, **kw)
        return subprocess.CompletedProcess(proc.args, 1, proc.stdout, proc.stderr)

    monkeypatch.setattr(util, "run", broken)
    assert cli._extract_audio(src, out) is None
    assert not os.path.exists(os.path.join(out, "audio16k.wav.source"))

    monkeypatch.setattr(util, "run", real_run)
    assert cli._extract_audio(src, out) is not None


# --- frames: точный кадр, обзорный лист, заметка ----------------------------------

@pytest.fixture
def built(tmp_path):
    clip = _clip(tmp_path / "v.mp4", video_sec=240, src="testsrc2")
    out = str(tmp_path / "idx")
    assert _index(clip, out) == 0
    return out


def _rows(out):
    with open(os.path.join(out, "frames.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_точный_кадр_снимается_в_названный_момент(built, capsys):
    """Ближайший готовый кадр может стоять в 8–15 с от нужного момента."""
    from frameproof.__main__ import main

    before = {r["id"] for r in _rows(built)}
    capsys.readouterr()
    assert main(["frames", "--at", "1:47.5", "--exact", "--out", built]) == 0
    out = capsys.readouterr().out

    new = [r for r in _rows(built) if r["id"] not in before]
    assert len(new) == 1 and abs(new[0]["t"] - 107.5) < 0.01
    assert new[0]["reason"] == "exact"
    assert os.path.exists(os.path.join(built, new[0]["path"]))
    assert new[0]["id"] in out
    # старые номера не сдвинулись — разборы по ним остаются верными
    assert before <= {r["id"] for r in _rows(built)}


def test_точный_кадр_рядом_с_готовым_не_дублируется(built):
    from frameproof.__main__ import main

    t = _rows(built)[3]["t"]
    n = len(_rows(built))
    main(["frames", "--at", str(round(t, 2)), "--exact", "--out", built])
    assert len(_rows(built)) == n


def test_обзорный_лист_одной_картинкой(built, capsys):
    from frameproof.__main__ import main

    capsys.readouterr()
    assert main(["frames", "--sheet", "--from", "1:00", "--to", "3:00", "--out", built]) == 0
    out = capsys.readouterr().out
    sheet = next(w for w in out.split() if w.endswith(".jpg"))
    assert os.path.exists(sheet)
    assert "1196 токенов" in out
    # легенда: какие кадры в ячейках
    assert out.count("/ f0") >= 4


def test_заметка_к_кадру_ищется_и_видна(built, capsys):
    from frameproof.__main__ import main

    fid = _rows(built)[2]["id"]
    assert main(["caption", fid, "таблица тарифов на слайде", "--out", built]) == 0
    capsys.readouterr()
    main(["search", "тарифов", "--out", built])
    found = capsys.readouterr().out
    assert fid in found and "заметка" in found
    main(["frames", "--ids", fid, "--out", built])
    assert "таблица тарифов" in capsys.readouterr().out
