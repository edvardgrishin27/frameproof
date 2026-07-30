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


def run_frameproof(video: str, duration: float) -> tuple[dict, float]:
    from frameproof.analyze import analyze
    from frameproof.probe import probe
    from frameproof.select import select_frames

    t0 = time.time()
    info = probe(video)
    sig = analyze(info)
    sel = select_frames(sig, info.duration)
    elapsed = time.time() - t0
    stats = gap_stats(sel.times, duration)
    stats["coverage_complete"] = not sel.gaps
    stats["caught_changes"] = len(sel.picks) - sel.safety_count
    stats["safety_fills"] = sel.safety_count
    return stats, elapsed


def run_claude_video(video: str, duration: float, repo_dir: str) -> tuple[dict, float]:
    from pathlib import Path

    scripts = os.path.join(repo_dir, "skills", "watch", "scripts")
    if not os.path.exists(scripts):
        raise SystemExit(f"не нашёл {scripts} — склонируйте {CLAUDE_VIDEO_REPO}")
    sys.path.insert(0, scripts)
    import frames as CV  # type: ignore

    out = tempfile.mkdtemp(prefix="cv-bench-")
    t0 = time.time()
    fps, target = CV.auto_fps(duration, max_frames=100)   # режим balanced, их дефолт
    picked, meta = CV.extract_scene_or_uniform(
        Path(video), Path(out), fps=fps, target_frames=target,
        resolution=1024, max_frames=100,
    )
    elapsed = time.time() - t0
    stats = gap_stats([p["timestamp_seconds"] for p in picked], duration)
    stats["engine"] = meta.get("engine")
    stats["fallback"] = meta.get("fallback")
    stats["candidates"] = meta.get("candidate_count")
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

    cv, cv_time = run_claude_video(video, duration, args.repo)
    fp, fp_time = run_frameproof(video, duration)

    rows = [
        ("кадров", str(cv["frames"]), str(fp["frames"])),
        ("максимальный разрыв", mmss(cv["max_gap"]), f"{fp['max_gap']:.0f} с"),
        ("в зонах >30 с без кадра",
         f"{cv['blind_seconds']:.0f} с ({cv['blind_ratio'] * 100:.0f} %)",
         f"{fp['blind_seconds']:.0f} с ({fp['blind_ratio'] * 100:.0f} %)"),
        ("время работы", f"{cv_time:.0f} с", f"{fp_time:.0f} с"),
    ]
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len("claude-video"), max(len(r[1]) for r in rows))
    print(f"{'':<{w0}}  {'claude-video':<{w1}}  frameproof")
    print(f"{'':─<{w0}}  {'':─<{w1}}  {'':─<10}")
    for a, b, c in rows:
        print(f"{a:<{w0}}  {b:<{w1}}  {c}")

    print()
    print(f"claude-video: движок «{cv['engine']}», фолбэк {cv['fallback']}, "
          f"кандидатов {cv['candidates']}")
    print(f"  крупнейшая дыра: {mmss(cv['biggest'][0])} → {mmss(cv['biggest'][1])}")
    print(f"frameproof: переходов {fp['caught_changes']}, страховки {fp['safety_fills']}, "
          f"покрытие полное: {fp['coverage_complete']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"duration": duration, "claude_video": cv, "frameproof": fp,
                       "seconds": {"claude_video": cv_time, "frameproof": fp_time}},
                      fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
