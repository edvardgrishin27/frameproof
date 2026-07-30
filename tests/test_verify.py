"""Тесты механического аудита утверждений.

Здесь важны обе стороны. Пропустить подлог — плохо. Но зарубить верное утверждение —
ровно так же плохо: инструмент, который кричит на правду, перестают слушать, и он
становится хуже отсутствия проверки.
"""

from __future__ import annotations

import json
import os

import pytest

from frameproof.verify import FAIL, OK, WARN, audit, extract_claims, plan_second_look


@pytest.fixture
def index(tmp_path):
    """Маленький индекс: три кадра, транскрипт, дыра покрытия 5:00-5:40."""
    d = tmp_path / "idx"
    frames_dir = d / "frames"
    frames_dir.mkdir(parents=True)
    for name in ("f0001.jpg", "f0002.jpg", "f0003.jpg"):
        (frames_dir / name).write_bytes(b"\xff\xd8\xff")

    (d / "index.json").write_text(json.dumps({
        "schema_version": "1",
        "video": {"title": "t", "duration_sec": 600.0},
        "transcript": {"segment_count": 2},
        "frames": {"count": 3},
        "coverage": {
            "complete": False,
            "gaps": [{"from": 300.0, "to": 340.0, "tc": "5:00–5:40"}],
        },
    }, ensure_ascii=False), encoding="utf-8")

    rows = [
        {"id": "f0001", "t": 60.0, "path": "frames/f0001.jpg", "w": 1280, "h": 720,
         "est_tokens": 1196, "ocr": "npm install hermes ⏎ Готово"},
        {"id": "f0002", "t": 120.0, "path": "frames/f0002.jpg", "w": 1280, "h": 720,
         "est_tokens": 1196, "ocr": "Куда какую модель ⏎ MAIN ⏎ AUXILIARY"},
        {"id": "f0003", "t": 480.0, "path": "frames/f0003.jpg", "w": 1280, "h": 720,
         "est_tokens": 1196, "ocr": None},
    ]
    (d / "frames.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    (d / "segments.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in [
        {"i": 0, "t0": 55.0, "t1": 70.0, "text": "ставим Hermes одной командой"},
        {"i": 1, "t0": 115.0, "t1": 130.0, "text": "дальше про маршрутизацию моделей"},
    ]), encoding="utf-8")
    # f0001 выдавался агенту, остальные — нет
    (d / "served.jsonl").write_text(
        json.dumps({"id": "f0001", "t": 60.0, "at": 1.0}) + "\n", encoding="utf-8")
    return str(d)


def only(claims, n):
    return next(c for c in claims if c.n == n)


def codes(claim):
    return {f.code for f in claim.findings}


def test_верное_утверждение_проходит_чисто(index):
    claims = audit("- На экране команда `npm install hermes`. [1:00 / f0001]", index)
    assert len(claims) == 1
    assert claims[0].severity == OK, codes(claims[0])


def test_выдуманный_кадр_ловится(index):
    claims = audit("- Тут схема памяти. [2:00 / f9999]", index)
    assert claims[0].severity == FAIL
    assert "FRAME_NOT_FOUND" in codes(claims[0])


def test_расхождение_таймкода_с_кадром(index):
    claims = audit("- Открывает настройки. [8:00 / f0001]", index)
    assert claims[0].severity == FAIL
    assert "TIME_MISMATCH" in codes(claims[0])


def test_утверждение_в_дыре_покрытия(index):
    """Момент, откуда кадров не извлекалось: агенту там просто нечего было видеть."""
    claims = audit("- В этот момент он показывает терминал. [5:10]", index)
    assert claims[0].severity == FAIL
    assert "IN_COVERAGE_GAP" in codes(claims[0])


def test_кадр_существует_но_не_запрашивался(index):
    claims = audit("- Тут таблица моделей. [2:00 / f0002]", index)
    assert claims[0].severity == WARN
    assert "NEVER_OPENED" in codes(claims[0])


def test_выдуманная_цитата_с_экрана(index):
    claims = audit('- На слайде написано «квантовая телепортация». [1:00 / f0001]', index)
    assert "QUOTE_NOT_ON_SCREEN" in codes(claims[0])


def test_цитата_из_речи_засчитывается_без_кадра(index):
    """Процитировано то, что действительно сказано рядом по времени."""
    claims = audit('- Он говорит «ставим Hermes одной командой». [1:00]', index)
    assert not any(f.code.startswith("QUOTE") for f in claims[0].findings)


def test_ocr_путает_пунктуацию_и_это_не_приговор(index):
    """Распознавание системно ошибается на скобках и отдельных буквах.

    Сверка нарочно снисходительная: цитата, отличающаяся мелочью, не должна
    объявляться выдумкой — иначе инструмент завалит ложными тревогами.
    """
    claims = audit('- Показана команда «npm install Hermes.». [1:00 / f0001]', index)
    assert not any(f.code.startswith("QUOTE") for f in claims[0].findings), codes(claims[0])


def test_перенесённая_строка_склеивается(index):
    """Метка часто оказывается на второй строке markdown-переноса."""
    answer = (
        "- На экране показана команда установки, которую он вводит\n"
        "  в терминале прямо во время объяснения. [1:00 / f0001]\n"
    )
    claims = extract_claims(answer)
    assert len(claims) == 1
    assert "показана команда установки" in claims[0].text
    assert "в терминале" in claims[0].text


def test_метка_без_кадра_не_считается_дважды(index):
    claims = extract_claims("- Тут вот так. [1:00 / f0001]")
    assert len(claims) == 1
    assert claims[0].frame_id == "f0001"


def test_разбор_без_меток_даёт_пусто():
    assert extract_claims("Просто текст без единой ссылки на момент.") == []


def test_план_второго_взгляда_не_содержит_провалов(index):
    answer = (
        "- Верное. [1:00 / f0001]\n"
        "- Выдумка. [2:00 / f9999]\n"
    )
    claims = audit(answer, index)
    tasks = plan_second_look(claims, index)
    assert [t["claim_id"] for t in tasks] == [1]
    assert os.path.exists(tasks[0]["frame_path"])


def test_план_не_протекает_контекстом(index):
    """Проверяющий должен получить только утверждение и кадр — больше ничего."""
    claims = audit("- Тут таблица. [2:00 / f0002]", index)
    task = plan_second_look(claims, index)[0]
    assert set(task) == {"claim_id", "claim_text", "frame_path", "frame_id"}


def test_потолок_второго_взгляда_соблюдается(index):
    answer = "\n".join(f"- Утверждение {i}. [1:00 / f0001]" for i in range(20))
    claims = audit(answer, index)
    assert len(plan_second_look(claims, index, limit=3)) == 3
