#!/usr/bin/env python3
"""Convert a reviewed reference cut profile into per-shot target durations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shot-map", type=Path, required=True)
    parser.add_argument("--reference-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mark-ready", action="store_true")
    parser.add_argument("--min-speed", type=float, default=0.45)
    parser.add_argument("--max-speed", type=float, default=1.20)
    args = parser.parse_args()

    if args.output.exists():
        parser.error(f"Refusing to overwrite existing output: {args.output}")
    shots = load_json(args.shot_map).get("shots", [])
    profile = load_json(args.reference_profile)
    if not shots:
        parser.error("Shot map contains no shots")
    reference_duration = float(profile.get("metadata", {}).get("format", {}).get("duration", 0.0))
    cuts = sorted(float(value) for value in profile.get("cut_times", []))
    required = len(shots) - 1
    if reference_duration <= 0 or len(cuts) < required:
        parser.error(
            f"Reference profile needs a duration and at least {required} reviewed cuts; found {len(cuts)}"
        )
    used = cuts[:required]
    points = [0.0, *used, reference_duration]
    entries = []
    out_of_bounds = []
    for index, shot in enumerate(shots):
        target_duration = points[index + 1] - points[index]
        source_duration = float(shot["duration"])
        speed = source_duration / target_duration
        if not args.min_speed <= speed <= args.max_speed:
            out_of_bounds.append(str(shot["id"]))
        entries.append({
            "shot_id": shot["id"],
            "asset_index": int(shot["asset_index"]),
            "source_start": float(shot["global_start"]),
            "source_end": float(shot["global_end"]),
            "source_duration": source_duration,
            "target_start": points[index],
            "target_end": points[index + 1],
            "target_duration": target_duration,
            "speed": speed,
        })
    if out_of_bounds:
        parser.error(
            "Reference-derived speeds exceed the allowed range for shots: " + ", ".join(out_of_bounds)
        )
    payload = {
        "version": 1,
        "mode": "reference_shot_retime",
        "status": "READY" if args.mark_ready else "REVIEW",
        "shot_map": str(args.shot_map.resolve()),
        "reference_profile": str(args.reference_profile.resolve()),
        "reference_video": profile.get("reference_video"),
        "reference_duration": reference_duration,
        "used_cut_times": used,
        "ignored_cut_times": cuts[required:],
        "min_speed": args.min_speed,
        "max_speed": args.max_speed,
        "shots": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Created {payload['status']} retime plan: {args.output} "
        f"({len(entries)} shots; {len(payload['ignored_cut_times'])} extra reference cuts ignored)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
