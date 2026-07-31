#!/usr/bin/env python3
"""Воспроизводимый замер: frameproof против claude-video на одном видео.

Честность замера обеспечивается тремя вещами:

1. Оба инструмента получают ОДИН И ТОТ ЖЕ локальный файл. Никто не качает своё.
2. claude-video запускается СВОИМ кодом со своими параметрами по умолчанию —
   не пересказом его алгоритма.
3. Меряется то, что можно проверить: число кадров, максимальный разрыв, доля
   хронометража в зонах без кадров. Не «качество разбора», которое неизмеримо.

Запуск:
    python3 bench/benchmark.py --video path/to/video.mp4
    python3 bench/benchmark.py --url "https://www.youtube.com/watch?v=..."
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

CLAUDE_VIDEO_REPO = "https://github.com/bradautomates/claude-video.git"


def mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def gap_stats(times: list[float], duration: float) -> dict:
    if not times:
        return {"frames": 0, "max_gap": duration, "blind_seconds": duration,
                "blind_ratio": 1.0, "biggest": (0.0, duration)}
    edges = [0.0] + sorted(times) + [duration]
    gaps = [(b - a, a, b) for a, b in zip(edges, edges[1:])]
    biggest = max(gaps)
    blind = sum(g for g, _, _ in gaps if g > 30.0)
    return {
        "frames": len(times),
        "max_gap": biggest[0],
        "blind_seconds": blind,
        "blind_ratio": blind / duration if duration else 0.0,
        "biggest": (biggest[1], biggest[2]),
    }


def run_frameproof(video: str, duration: float, *, extract_frames: bool = True) -> tuple[dict, float]:
    """Полный проход: детекция И извлечение.

    Извлечение включено намеренно. Прежняя версия бенча меряла у нас только детекцию,
    а у claude-video — детекцию вместе с извлечением, и «41 с против 55 с» было
    сравнением разных стадий. Честно они идут вровень.
    """
    import tempfile as _tf

    from frameproof.analyze import analyze
    from frameproof.extract import extract
    from frameproof.probe import probe
    from frameproof.select import select_frames

    t0 = time.time()
    info = probe(video)
    sig = analyze(info)
    sel = select_frames(sig, info.duration)
    detect = time.time() - t0
    if extract_frames:
        extract(info, sel.picks, _tf.mkdtemp(prefix="fp-bench-frames-"))
    elapsed = time.time() - t0

    stats = gap_stats(sel.times, duration)
    stats["coverage_complete"] = not sel.gaps
    stats["caught_changes"] = len(sel.picks) - sel.safety_count
    stats["safety_fills"] = sel.safety_count
    stats["detect_seconds"] = round(detect, 1)
    stats["mode"] = "по умолчанию"
    return stats, elapsed


def _load_cv(repo_dir: str):
    scripts = os.path.join(repo_dir, "skills", "watch", "scripts")
    if not os.path.exists(scripts):
        raise SystemExit(f"не нашёл {scripts} — склонируйте {CLAUDE_VIDEO_REPO}")
    sys.path.insert(0, scripts)
    import frames as CV  # type: ignore

    return CV


#: Все три режима конкурента, а не только слабейший.
#: Мерить только `balanced` нечестно: их же движок в `efficient` без капа даёт
#: покрытие ЛУЧШЕ нашего. Об этом надо говорить вслух, а не прятать.
CV_MODES = [
    ("balanced (по умолчанию)", dict(engine="scene", resolution=512, max_frames=100)),
    ("efficient (по умолчанию)", dict(engine="keyframe", resolution=512, max_frames=50, dedup=True)),
    ("efficient без капа и дедупа", dict(engine="keyframe", resolution=512, max_frames=None, dedup=False)),
]


def run_claude_video(video: str, duration: float, repo_dir: str, mode: dict) -> tuple[dict, float]:
    from pathlib import Path

    CV = _load_cv(repo_dir)
    out = Path(tempfile.mkdtemp(prefix="cv-bench-"))
    t0 = time.time()
    if mode["engine"] == "scene":
        fps, target = CV.auto_fps(duration, max_frames=mode["max_frames"])
        picked, meta = CV.extract_scene_or_uniform(
            Path(video), out, fps=fps, target_frames=target,
            resolution=mode["resolution"], max_frames=mode["max_frames"],
        )
    else:
        picked, meta = CV.extract_keyframes(
            Path(video), out, resolution=mode["resolution"],
            max_frames=mode["max_frames"], dedup=mode.get("dedup", True),
        )
    elapsed = time.time() - t0
    stats = gap_stats([p["timestamp_seconds"] for p in picked], duration)
    stats["engine"] = meta.get("engine")
    stats["fallback"] = meta.get("fallback")
    stats["candidates"] = meta.get("candidate_count")
    stats["resolution"] = mode["resolution"]
    return stats, elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", help="локальный файл")
    ap.add_argument("--url", help="ссылка (скачается один раз, оба инструмента получат её)")
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(__file__), "claude-video"),
                    help="папка с клоном claude-video")
    ap.add_argument("--json", help="куда сложить результат в JSON")
    args = ap.parse_args()

    video = args.video
    if not video:
        if not args.url:
            ap.error("нужен --video или --url")
        from frameproof.fetch import fetch
        work = tempfile.mkdtemp(prefix="fp-bench-")
        print(f"качаю {args.url} ...")
        video = fetch(args.url, work, max_height=1080).video_path

    if not os.path.exists(args.repo):
        print(f"клонирую claude-video в {args.repo} ...")
        subprocess.run(["git", "clone", "--depth", "1", CLAUDE_VIDEO_REPO, args.repo], check=True)

    from frameproof.probe import probe
    duration = probe(video).duration
    print(f"видео: {os.path.basename(video)}  {mmss(duration)}\n")

    runs = []
    for label, mode in CV_MODES:
        st, sec = run_claude_video(video, duration, args.repo, mode)
        runs.append((f"claude-video {label}", st, sec))
    fp, fp_time = run_frameproof(video, duration)
    runs.append(("frameproof", fp, fp_time))

    head = ("инструмент / режим", "кадров", "макс. разрыв", ">30 с без кадра", "время")
    table = [head] + [
        (name, str(st["frames"]), mmss(st["max_gap"]),
         f"{st['blind_ratio'] * 100:.0f} %", f"{sec:.0f} с")
        for name, st, sec in runs
    ]
    widths = [max(len(r[i]) for r in table) for i in range(len(head))]
    for n, row in enumerate(table):
        print("  ".join(v.ljust(widths[i]) for i, v in enumerate(row)))
        if n == 0:
            print("  ".join("─" * w for w in widths))

    print()
    print(f"frameproof: детекция {fp['detect_seconds']} с + извлечение "
          f"{fp['frames']} кадров = {fp_time:.0f} с (like-for-like: их время тоже с извлечением)")
    print(f"  переходов {fp['caught_changes']}, страховки {fp['safety_fills']}, "
          f"покрытие полное: {fp['coverage_complete']}")
    for name, st, _ in runs[:-1]:
        print(f"{name}: движок «{st['engine']}», кандидатов {st['candidates']}, "
              f"крупнейшая дыра {mmss(st['biggest'][0])} → {mmss(st['biggest'][1])}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"duration": duration,
                       "runs": [{"name": n, "stats": s, "seconds": sec} for n, s, sec in runs]},
                      fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
