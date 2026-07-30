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
"""

from __future__ import annotations

import os
import shutil
import subprocess
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


def available() -> bool:
    return shutil.which("swiftc") is not None


def _build(cache_dir: str) -> str | None:
    if not available():
        return None
    os.makedirs(cache_dir, exist_ok=True)
    binary = os.path.join(cache_dir, _BIN_NAME)
    if os.path.exists(binary):
        return binary
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "ocr.swift")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(_SWIFT_SRC)
        proc = subprocess.run(
            ["swiftc", "-O", "-o", binary, src],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not os.path.exists(binary):
            return None
    return binary


def recognize(paths: list[str], *, cache_dir: str) -> dict[str, str]:
    """Путь к кадру -> распознанный текст. Пустой словарь, если OCR недоступен."""
    if not paths:
        return {}
    binary = _build(cache_dir)
    if not binary:
        return {}

    out: dict[str, str] = {}
    # Партиями, чтобы не упереться в лимит длины командной строки.
    for i in range(0, len(paths), 60):
        chunk = paths[i : i + 60]
        proc = subprocess.run([binary, *chunk], capture_output=True, text=True)
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            path, _, text = line.partition("\t")
            if path:
                out[path] = text.strip()
    return out


def annotate_index(out_dir: str) -> int:
    """Дописывает OCR-слой в frames.jsonl и пересобирает поиск. Возвращает число кадров с текстом."""
    import json

    from .index import build_search

    frames_file = os.path.join(out_dir, "frames.jsonl")
    if not os.path.exists(frames_file):
        return 0

    rows = [json.loads(line) for line in open(frames_file, encoding="utf-8")]
    paths = [os.path.join(out_dir, r["path"]) for r in rows]
    texts = recognize(paths, cache_dir=os.path.join(out_dir, ".cache"))
    if not texts:
        return 0

    hits = 0
    for r, p in zip(rows, paths):
        text = texts.get(p, "")
        r["ocr"] = text or None
        if text:
            hits += 1
    with open(frames_file, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    build_search(out_dir)
    return hits
