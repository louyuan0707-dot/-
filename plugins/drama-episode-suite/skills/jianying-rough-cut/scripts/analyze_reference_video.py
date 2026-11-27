#!/usr/bin/env python3
"""Extract reusable cut-rhythm evidence from a user-provided reference edit."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--scene-threshold", type=float, default=0.28)
    parser.add_argument("--sample-interval", type=float, default=10.0)
    parser.add_argument("--boundary-window", type=float, default=0.45)
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"Reference video does not exist: {args.video}")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"Refusing to overwrite non-empty output directory: {args.output}")
    if args.scene_threshold <= 0 or args.sample_interval <= 0 or args.boundary_window <= 0:
        parser.error("Threshold, sample interval, and boundary window must be positive")
    args.output.mkdir(parents=True, exist_ok=True)

    probe = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", str(args.video.resolve()),
    ])
    if probe.returncode != 0:
        parser.error(f"ffprobe failed: {probe.stderr.strip()}")
    metadata = json.loads(probe.stdout)

    scene = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(args.video.resolve()),
        "-vf", f"select='gt(scene,{args.scene_threshold})',showinfo",
        "-an", "-f", "null", os.devnull,
    ])
    if scene.returncode != 0:
        parser.error(f"ffmpeg scene analysis failed: {scene.stderr.strip()}")
    cut_times = sorted({round(float(match.group(1)), 6) for match in PTS_RE.finditer(scene.stderr)})

    contact_sheet = args.output / "contact_sheet.jpg"
    contact = run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(args.video.resolve()),
        "-vf",
        (
            f"fps=1/{args.sample_interval},"
            "scale=270:480:force_original_aspect_ratio=decrease,"
            "pad=270:480:(ow-iw)/2:(oh-ih)/2:black,tile=4x4"
        ),
        "-frames:v", "1", str(contact_sheet),
    ])
    if contact.returncode != 0:
        parser.error(f"ffmpeg contact sheet failed: {contact.stderr.strip()}")

    boundary_matches: list[dict] = []
    if args.transcript is not None:
        transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
        boundaries: list[tuple[float, str]] = []
        for segment in transcript.get("segments", []):
            text = str(segment.get("text", "")).strip()
            boundaries.extend(((float(segment["start"]), text), (float(segment["end"]), text)))
        for cut_time in cut_times:
            nearest_time, nearest_text = min(
                boundaries, key=lambda item: abs(cut_time - item[0])
            )
            boundary_matches.append({
                "cut_time": cut_time,
                "nearest_boundary": round(nearest_time, 6),
                "distance": round(abs(cut_time - nearest_time), 6),
                "within_window": abs(cut_time - nearest_time) <= args.boundary_window,
                "narration_text": nearest_text,
            })

    near_count = sum(item["within_window"] for item in boundary_matches)
    profile = {
        "reference_video": str(args.video.resolve()),
        "metadata": metadata,
        "scene_threshold": args.scene_threshold,
        "cut_times": cut_times,
        "cut_count": len(cut_times),
        "boundary_window": args.boundary_window,
        "cuts_near_narration_boundary": near_count if boundary_matches else None,
        "cuts_with_boundary_evidence": len(boundary_matches),
        "boundary_matches": boundary_matches,
        "contact_sheet": str(contact_sheet.resolve()),
    }
    (args.output / "reference_profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    percentage = (
        f"{near_count / len(boundary_matches) * 100:.1f}%"
        if boundary_matches else "not measured"
    )
    report = [
        "# Reference edit profile", "",
        f"- Video: `{args.video.resolve()}`",
        f"- Detected hard cuts: {len(cut_times)}",
        f"- Cuts within {args.boundary_window:.3f}s of a narration boundary: {percentage}",
        f"- Contact sheet: `{contact_sheet.resolve()}`", "",
        "Use the contact sheet and transcript to describe semantic matches; do not copy the reference footage.",
    ]
    (args.output / "reference_profile.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote reference-edit profile to {args.output}; {len(cut_times)} cut(s), {percentage} near narration boundaries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
