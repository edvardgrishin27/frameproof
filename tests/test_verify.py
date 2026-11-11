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


def test_опечатка_в_метке_роняет_только_своё_утверждение(index):
    """`[1:75 / f0001]` — секунд больше 59. Раньше это роняло аудит целиком.

    parse_tc честно отказывается такое читать, но ошибка вылетала из `verify`
    наружу: человек видел «ошибка: не понял тайм-код», и остальные утверждения
    не проверялись вовсе. Опечатка — это провал одной ссылки, а не всего разбора.
    """
    from frameproof.verify import render

    answer = (
        "- Команда установки `npm install hermes` [1:00 / f0001]\n"
        "- Что-то с опечаткой во времени [1:75 / f0002]\n"
        "- И без кадра, тоже с опечаткой [9:99]\n"
    )
    claims = audit(answer, index)

    assert len(claims) == 3
    assert only(claims, 1).severity in (OK, WARN)
    for n in (2, 3):
        c = only(claims, n)
        assert c.severity == FAIL
        assert any(f.code == "BAD_TIMECODE" for f in c.findings)
    text = render(claims, index_dir=index)
    assert "1:75" in text and "BAD_TIMECODE" in text


def test_у_нечитаемой_метки_нет_ложных_проверок(index):
    """Время не прочитано — сверять цитату с речью «рядом с 0:00» нельзя."""
    claims = audit("- цитата `совсем другое` [1:75 / f0001]\n", index)
    codes = [f.code for f in claims[0].findings]

    assert "BAD_TIMECODE" in codes
    assert not any(c.startswith("QUOTE_") for c in codes)


def test_json_не_выдаёт_нечитаемое_время_за_ноль(index, tmp_path, capsys):
    from frameproof.__main__ import main

    answer = tmp_path / "a.md"
    answer.write_text("- опечатка [1:75 / f0001]\n", encoding="utf-8")
    main(["verify", str(answer), "--out", index, "--json"])
    row = json.loads(capsys.readouterr().out)[0]

    assert row["t"] is None and row["tc_raw"] == "1:75"


# --- Строже к выдумке, мягче к опечаткам OCR ---------------------------------------

def test_выдуманная_цитата_из_одного_слова_ловится(index):
    """Раньше цитата короче двух слов «не судилась» и проходила как подтверждённая."""
    claims = audit("- На экране команда `pip` [1:00 / f0001]", index)
    assert "QUOTE_NOT_ON_SCREEN" in codes(claims[0])


def test_цитата_из_одного_слова_с_экрана_проходит(index):
    claims = audit("- На экране команда `npm` [1:00 / f0001]", index)
    assert not any(f.code.startswith("QUOTE") for f in claims[0].findings)


def test_подменённое_имя_в_коде_ловится(index):
    """`class Dragon(Reptile)` при `class Snake(Reptile)`: два слова из трёх совпали.

    Порог по доле слов это пропускал. Длинное слово, которого нет нигде, — выдумка.
    """
    claims = audit("- На экране `npm install dragon` [1:00 / f0001]", index)
    assert "QUOTE_NOT_ON_SCREEN" in codes(claims[0])


def test_опечатка_ocr_в_длинном_слове_не_приговор(index):
    """Одна буква мимо в длинном слове — обычная ошибка OCR, а не выдумка."""
    claims = audit("- Команда `npm install hermas` [1:00 / f0001]", index)
    assert not any(f.code.startswith("QUOTE") for f in claims[0].findings), codes(claims[0])


def test_цитату_нечем_проверить_это_не_зелёный(index):
    """У кадра нет OCR и рядом нет речи: раньше вердикт был ✓."""
    claims = audit("- На экране заставка «SMPTE bars» [8:00 / f0003]", index)
    assert "QUOTE_UNCHECKED" in codes(claims[0])
    assert claims[0].severity == WARN


def test_апостроф_не_делает_цитату(index):
    claims = extract_claims("- It's the config, don't touch it [1:00 / f0001]")
    assert claims[0].quotes == []


def test_метка_с_трёхзначными_минутами(index):
    claims = extract_claims("- Под конец лекции [125:30 / f0001]")
    assert len(claims) == 1 and claims[0].t == 125 * 60 + 30


# --- Утверждения про экран без метки ----------------------------------------------

def test_строка_про_экран_без_метки_помечается(index):
    answer = ("- Команда установки `npm install hermes` [1:00 / f0001]\n"
              "- Потом на экране открывается таблица с ценами.\n"
              "- Просто мысль автора без экрана.\n")
    claims = audit(answer, index)
    untagged = [c for c in claims if "UNTAGGED" in codes(c)]

    assert len(untagged) == 1
    assert "таблица с ценами" in untagged[0].text
    assert untagged[0].severity == WARN
    assert untagged[0].frame_id is None


def test_разбор_про_экран_без_единой_метки_это_провал_выхода(index, tmp_path, capsys):
    from frameproof.__main__ import main

    answer = tmp_path / "a.md"
    answer.write_text("На экране терминал, он вводит docker run.\n", encoding="utf-8")
    assert main(["verify", str(answer), "--out", index]) == 1
    assert "без метки" in capsys.readouterr().out


# --- План второго взгляда ----------------------------------------------------------

def test_план_сначала_берёт_сомнительные(index):
    answer = ("- Верное. [1:00 / f0001]\n"
              "- Не открывал. [2:00 / f0002]\n")
    tasks = plan_second_look(audit(answer, index), index, limit=1)
    assert [t["frame_id"] for t in tasks] == ["f0002"]


def test_план_отдаёт_абсолютные_пути(index, monkeypatch):
    monkeypatch.chdir(os.path.dirname(index))
    rel = os.path.basename(index)
    tasks = plan_second_look(audit("- Тут. [1:00 / f0001]", rel), rel)
    assert os.path.isabs(tasks[0]["frame_path"])


def test_план_говорит_сколько_пропущено(index, tmp_path, capsys):
    from frameproof.__main__ import main

    answer = tmp_path / "a.md"
    answer.write_text("\n".join(f"- У{i}. [1:00 / f0001]" for i in range(5)), encoding="utf-8")
    main(["verify", str(answer), "--out", index, "--plan", "--limit", "2"])
    assert "пропущено 3" in capsys.readouterr().err


# --- По ревью: цифры, окно, миниатюры, проза --------------------------------------

@pytest.mark.parametrize("цитата, на_экране", [
    ("Cursor 2.1.154", "Cursor 2.1.153"),
    ("$29.3B", "$29.8B"),
    ("timeout 3000ms", "timeout 3600ms"),
    ("Claude-3.5 Sonnet", "Claude-3.7 Sonnet"),
    ("npm install foo", "npm install bar"),
])
def test_подмена_цифр_и_коротких_слов_ловится(цитата, на_экране):
    """Нечёткое сравнение пропускало подмену версий и цифр — главный риск фактчека."""
    from frameproof.verify import _quote_supported

    assert not _quote_supported(цитата, на_экране)


def test_нечитаемая_метка_не_получает_ложного_вне_окна(index):
    path = os.path.join(index, "index.json")
    data = json.load(open(path, encoding="utf-8"))
    data["window"] = {"from": 50.0, "to": 200.0}
    json.dump(data, open(path, "w", encoding="utf-8"))
    claims = audit("- опечатка [1:75 / f0001]", index)
    assert "OUTSIDE_WINDOW" not in codes(claims[0])


def test_кадр_виденный_только_миниатюрой_не_считается_открытым(index):
    with open(os.path.join(index, "served.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "f0002", "t": 120.0, "at": 2.0, "via": "sheet"}) + "\n")
    claims = audit("- Тут таблица. [2:00 / f0002]", index)
    finding = next(f for f in claims[0].findings if f.code == "NEVER_OPENED")
    assert "миниатюр" in finding.detail


@pytest.mark.parametrize("проза", [
    "Открытый код — это хорошо.",
    "Как видно из цифр, рост замедлился.",
    "The show shows growth.",
])
def test_обычная_проза_не_untagged(проза):
    assert extract_claims(проза) == []
