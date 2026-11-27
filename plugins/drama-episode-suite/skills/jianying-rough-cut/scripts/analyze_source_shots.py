#!/usr/bin/env python3
"""Detect source-video hard cuts and build a template-free ordered shot map."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
MOTION_RE = re.compile(
    r"frame:[^\n]*pts_time:([0-9]+(?:\.[0-9]+)?)[\s\S]*?lavfi\.signalstats\.YAVG=([0-9]+(?:\.[0-9]+)?)"
)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def detect_cuts(path: Path, duration: float, threshold: float, min_shot: float) -> list[float]:
    expression = f"select='gt(scene,{threshold})',showinfo"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(path),
         "-vf", expression, "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg scene detection failed for {path}: {result.stderr[-800:]}")
    raw = sorted({float(match.group(1)) for match in PTS_RE.finditer(result.stderr)})
    cuts = [0.0]
    for value in raw:
        if min_shot <= value <= duration - min_shot and value - cuts[-1] >= min_shot:
            cuts.append(value)
    if duration - cuts[-1] < min_shot and len(cuts) > 1:
        cuts.pop()
    cuts.append(duration)
    return cuts


def sample_motion(path: Path) -> list[tuple[float, float]]:
    """Return low-rate frame-difference samples as normalized motion evidence."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(path),
         "-vf", "fps=2,scale=160:-2,format=gray,tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg motion sampling failed for {path}: {result.stderr[-800:]}")
    return [(float(match.group(1)), float(match.group(2)) / 255.0)
            for match in MOTION_RE.finditer(result.stderr)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene-threshold", type=float, default=0.28)
    parser.add_argument("--min-shot-duration", type=float, default=0.18)
    args = parser.parse_args()
    if not 0.05 <= args.scene_threshold <= 0.9:
        parser.error("--scene-threshold must be between 0.05 and 0.9")
    if args.min_shot_duration < 0.08:
        parser.error("--min-shot-duration must be at least 0.08 seconds")
    assets = load_json(args.assets).get("assets", [])
    if not assets:
        parser.error("Assets manifest has no videos")
    shots: list[dict] = []
    global_offset = 0.0
    for asset_position, asset in enumerate(assets):
        path = Path(asset["path"])
        duration = float(asset["duration"])
        try:
            cuts = detect_cuts(path, duration, args.scene_threshold, args.min_shot_duration)
            motion_samples = sample_motion(path)
        except RuntimeError as error:
            parser.error(str(error))
        for local_index, (start, end) in enumerate(zip(cuts, cuts[1:]), start=1):
            values = [score for timestamp, score in motion_samples if start <= timestamp < end]
            motion_score = sum(values) / len(values) if values else 0.0
            shots.append({
                "id": f"shot-{len(shots) + 1:03d}",
                "asset_index": int(asset.get("source_index", asset_position)),
                "asset_path": str(path),
                "local_shot_index": local_index,
                "local_start": round(start, 6), "local_end": round(end, 6),
                "global_start": round(global_offset + start, 6),
                "global_end": round(global_offset + end, 6),
                "duration": round(end - start, 6),
                "motion_score": round(motion_score, 6),
            })
        global_offset += duration
    output = {
        "version": 1, "mode": "ordered_source_hard_cuts",
        "source_assets": str(args.assets.resolve()),
        "scene_threshold": args.scene_threshold,
        "min_shot_duration": args.min_shot_duration,
        "shot_count": len(shots), "shots": shots,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detected {len(shots)} ordered source shot(s): {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
