#!/usr/bin/env python3
"""Scan local video assets with ffprobe and write a portable JSON manifest."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}


def probe(path: Path) -> dict:
    command = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,duration,width,height,r_frame_rate,channels,sample_rate",
        "-of", "json", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"), {})
    return {
        "path": str(path.resolve()),
        "duration": round(float(video.get("duration") or data["format"]["duration"]), 6),
        "container_duration": round(float(data["format"]["duration"]), 6),
        "codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": video.get("r_frame_rate"),
        "has_audio": bool(audio),
        "audio_codec": audio.get("codec_name"),
        "audio_sample_rate": audio.get("sample_rate"),
        "audio_channels": audio.get("channels"),
    }


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def ordered_files(input_path: Path, order_file: Path | None) -> list[Path]:
    if input_path.is_file():
        if order_file:
            raise SystemExit("--order-file can only be used when input is a directory")
        return [input_path]
    discovered = sorted(
        (path.resolve() for path in input_path.rglob("*") if path.suffix.lower() in VIDEO_EXTENSIONS),
        key=natural_key,
    )
    if not order_file:
        return discovered
    if not order_file.is_file():
        raise SystemExit(f"Order file does not exist: {order_file}")
    listed = []
    for raw in order_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line)
        if not candidate.is_absolute():
            candidate = input_path / candidate
        candidate = candidate.resolve()
        if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
            raise SystemExit(f"Invalid video in order file: {line}")
        listed.append(candidate)
    if len(set(listed)) != len(listed):
        raise SystemExit("Order file contains duplicate video paths")
    missing = [path for path in discovered if path not in set(listed)]
    extra = [path for path in listed if path not in set(discovered)]
    if missing or extra:
        details = []
        if missing:
            details.append("unlisted: " + ", ".join(path.name for path in missing))
        if extra:
            details.append("outside input: " + ", ".join(path.name for path in extra))
        raise SystemExit("Order file must list every discovered video exactly once (" + "; ".join(details) + ")")
    return listed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Video file or directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--order-file", type=Path, help="UTF-8 file containing one relative video path per line")
    args = parser.parse_args()
    if not shutil.which("ffprobe"):
        parser.error("ffprobe was not found on PATH")
    if not args.input.exists():
        parser.error(f"Input does not exist: {args.input}")
    files = ordered_files(args.input.resolve(), args.order_file)
    assets, errors = [], []
    for file in files:
        try:
            item = probe(file)
            item["source_index"] = len(assets)
            assets.append(item)
        except (subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError) as error:
            errors.append({"path": str(file.resolve()), "error": str(error)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"version": 1, "assets": assets, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Scanned {len(assets)} asset(s); {len(errors)} failed. Manifest: {args.output}")
    return 0 if assets else 2


if __name__ == "__main__":
    raise SystemExit(main())
