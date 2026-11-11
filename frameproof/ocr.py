"""OCR кадров через Apple Vision — офлайн, без установки, с русским языком.

Роль OCR здесь узкая и осознанная: он решает, КАКИЕ кадры показать модели, и делает
экран грепаемым. ЧТО именно написано — читает глазами сама модель.

Почему так, а не «OCR точнее для команд». На проверке Apple Vision прочитал три строки
терминала из четырёх байт-в-байт, включая смешанную русско-английскую, но `[main 7f3a9c1]`
превратил в `Imain 7f3a9c1]` — спутал квадратную скобку с заглавной I. В коде пунктуация
несёт смысл, поэтому дословно снимать команды с OCR нельзя.

Зато два других выигрыша реальные: вопрос «в какой момент появилась команда npm install»
решается грепом без единой картинки, и кадры без текста можно вообще не предлагать модели.

Требуется macOS со swiftc (идёт с Xcode Command Line Tools). На других системах молча
возвращаем None — инструмент работает и без OCR.

Чужой распознаватель подключается через `--ocr-command`: программа получает пути к
картинкам аргументами и отвечает строками `путь<TAB>текст`. Это тот же договор, по
которому работает свифтовый бинарник внутри, — не новый интерфейс, а вынесенный наружу
существующий. Так закрывается Windows с её встроенным офлайновым Windows.Media.Ocr,
которому не нужны ни ключи, ни установка, и любой другой движок под рукой.
"""

from __future__ import annotations

import locale
import os
import shlex
import shutil
import hashlib
import subprocess
import sys
import tempfile

_SWIFT_SRC = r"""
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments.dropFirst()
for path in args {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("\(path)\t")
        continue
    }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    // Критично для кода: иначе языковая модель «исправит» команды и
    // идентификаторы в обычные слова.
    req.usesLanguageCorrection = false
    req.recognitionLanguages = ["ru-RU", "en-US"]
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    try? handler.perform([req])
    let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    let text = lines.joined(separator: " ⏎ ").replacingOccurrences(of: "\t", with: " ")
    print("\(path)\t\(text)")
}
"""

_BIN_NAME = "frameproof_ocr"

#: Сколько ждать одну партию распознавания, секунды. Без потолка зависший
#: распознаватель вешал весь index навсегда.
OCR_TIMEOUT = 600


def binary_cache_dir() -> str:
    """Общая папка для собранного распознавателя — одна на все индексы.

    Раньше бинарь собирался заново в каждой папке индекса: лишние 10–20 секунд
    swiftc на каждом новом ролике.
    """
    base = os.environ.get("FRAMEPROOF_HOME") or os.path.join(os.path.expanduser("~"), ".frameproof")
    return os.path.join(base, ".cache")


def available(command: str | None = None) -> bool:
    if command:
        parts = split_command(command)
        return bool(parts) and shutil.which(parts[0]) is not None
    return shutil.which("swiftc") is not None


def split_command(command: str) -> list[str]:
    """Разбор командной строки, не съедающий пути Windows.

    `shlex.split` по умолчанию работает в posix-режиме, где обратная косая — это
    экранирование. Поэтому `-File D:\\tools\\ocr.ps1` молча превращается в
    `D:toolsocr.ps1`: ошибки нет, просто файл не находится. На Windows берём
    posix=False, который косые сохраняет, и снимаем кавычки сами — в этом режиме
    shlex оставляет их приклеенными к токену.
    """
    if os.name != "nt":
        return shlex.split(command)
    out = []
    for tok in shlex.split(command, posix=False):
        if len(tok) > 1 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def _decode(raw: bytes) -> str:
    """UTF-8, а при неудаче — системная кодировка.

    `subprocess` с `text=True` на Windows декодирует системной ANSI, поэтому
    распознаватель, отвечающий в UTF-8, молча превращался в кашу. Договор теперь
    UTF-8, но ANSI-ответ тоже принимается: ломать тех, кто уже подстроился, незачем.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def _build(cache_dir: str, errors: list[str] | None = None) -> str | None:
    if not available():
        return None
    os.makedirs(cache_dir, exist_ok=True)
    # Имя с отпечатком исходника: новая версия распознавателя не возьмёт старый бинарь.
    tag = hashlib.sha256(_SWIFT_SRC.encode("utf-8")).hexdigest()[:8]
    binary = os.path.join(cache_dir, f"{_BIN_NAME}-{tag}")
    if os.path.exists(binary):
        return binary
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "ocr.swift")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(_SWIFT_SRC)
        # Собираем во временный файл и переносим целиком: бинарь общий на все
        # индексы, и параллельный index не должен запустить недособранный файл.
        tmp_bin = os.path.join(tmp, _BIN_NAME)
        proc = subprocess.run(
            ["swiftc", "-O", "-o", tmp_bin, src],
            capture_output=True, text=True,
        )
        if proc.returncode == 0 and os.path.exists(tmp_bin):
            shutil.copy2(tmp_bin, binary + ".part")
            os.replace(binary + ".part", binary)
        if proc.returncode != 0 or not os.path.exists(binary):
            if errors is not None:
                errors.append("swiftc не собрал распознаватель: "
                              + (proc.stderr or "").strip()[-300:])
            return None
    return binary


def recognize(paths: list[str], *, cache_dir: str, command: str | None = None,
              errors: list[str] | None = None) -> dict[str, str]:
    """Путь к кадру -> распознанный текст. Пустой словарь, если OCR недоступен."""
    if not paths:
        return {}
    if command:
        argv = split_command(command)
        if not argv:
            return {}
    else:
        binary = _build(cache_dir, errors)
        if not binary:
            return {}
        argv = [binary]

    out: dict[str, str] = {}
    # Партиями, чтобы не упереться в лимит длины командной строки.
    for i in range(0, len(paths), 60):
        chunk = paths[i : i + 60]
        # Без text=True: декодируем сами, иначе на Windows ответ читается системной
        # ANSI и UTF-8 молча превращается в кашу.
        try:
            proc = subprocess.run([*argv, *chunk], capture_output=True, timeout=OCR_TIMEOUT)
        except subprocess.TimeoutExpired:
            if errors is not None:
                errors.append(f"партия из {len(chunk)} кадров не уложилась в {OCR_TIMEOUT} с")
            continue
        except OSError as exc:
            if errors is not None:
                errors.append(f"не запустился: {exc}")
            continue
        if proc.returncode != 0:
            # Раньше партия молча пропускалась, и 60 кадров оставались без текста.
            if errors is not None:
                tail = _decode(proc.stderr or b"").strip()[-300:]
                errors.append(f"партия из {len(chunk)} кадров: код {proc.returncode}"
                              + (f", {tail}" if tail else ""))
            continue
        for line in _decode(proc.stdout).splitlines():
            path, _, text = line.partition("\t")
            if path:
                out[path] = text.strip()
    return out


def annotate_index(
    out_dir: str, *, images: list[str] | None = None, command: str | None = None
) -> int:
    """Дописывает OCR-слой в frames.jsonl и пересобирает поиск. Возвращает число кадров с текстом.

    `images` — на чём распознавать, по одному пути на строку индекса. По умолчанию это
    сами кадры индекса, но кадры индекса ужаты до ширины показа (1280 — это про цену
    токенов), и мелкий интерфейс на них не читается. Замер по отзыву с Windows: один и
    тот же кадр со страницей GitHub при 1280 дал одно слово, при 2560 — имена файлов и
    строки коммитов. Поэтому распознавать правильно по отдельной, более крупной копии,
    а показывать по-прежнему лёгкую.
    """
    import json

    from .index import build_search

    frames_file = os.path.join(out_dir, "frames.jsonl")
    if not os.path.exists(frames_file):
        return 0

    rows = [json.loads(line) for line in open(frames_file, encoding="utf-8")]
    targets = images or [os.path.join(out_dir, r["path"]) for r in rows]
    if len(targets) != len(rows):
        targets = [os.path.join(out_dir, r["path"]) for r in rows]
    errors: list[str] = []
    texts = recognize(targets, cache_dir=binary_cache_dir(), command=command, errors=errors)
    if errors:
        print("⚠ распознавание не отработало: " + "; ".join(errors[:3])
              + (f" и ещё {len(errors) - 3}" if len(errors) > 3 else ""), file=sys.stderr)
    if not texts:
        return 0

    hits = 0
    for r, p in zip(rows, targets):
        text = texts.get(p, "")
        r["ocr"] = text or None
        if text:
            hits += 1
    with open(frames_file, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    build_search(out_dir)
    return hits
