#!/usr/bin/env python3
"""Build a reviewable narration-led timeline, SRT, and report from JSON inputs."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

MIN_SPEED, MAX_SPEED = 0.85, 1.15


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def parse_scene_notes(path: Path | None) -> list[dict]:
    if not path or not path.is_file():
        return []
    scenes = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        scene_id, note = (parts + [""])[:2]
        match = re.search(r"(?:^|\s)file\s*=\s*([^|]+)", line, flags=re.I)
        scenes.append({"scene_id": scene_id or f"{number:02d}", "note": note, "file_hint": match.group(1).strip() if match else None})
    return scenes


def choose_asset(assets: list[dict], scene: dict | None, index: int) -> tuple[dict | None, str]:
    if not assets:
        return None, "no usable video assets"
    hint = scene.get("file_hint") if scene else None
    if hint:
        asset = next((item for item in assets if Path(item["path"]).name == hint or item["path"].endswith(hint)), None)
        return asset, "scene_notes file hint" if asset else f"file hint not found: {hint}"
    scene_id = scene.get("scene_id") if scene else ""
    if scene_id:
        asset = next((item for item in assets if Path(item["path"]).stem.startswith(scene_id)), None)
        if asset:
            return asset, "scene id matched filename prefix"
    return assets[index % len(assets)], "deterministic file-order fallback; semantic match required"


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_part, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d},{milliseconds:03d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--scene-notes", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New preview_vNN directory")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"Refusing to overwrite non-empty output directory: {args.output}")

    assets = load_json(args.assets).get("assets", [])
    transcript = load_json(args.transcript)
    voice_segments = transcript.get("segments", [])
    if not voice_segments:
        parser.error("Transcript has no segments")
    scenes = parse_scene_notes(args.scene_notes)
    cursors: dict[str, float] = defaultdict(float)
    timeline = []
    warnings = []
    for index, voice in enumerate(voice_segments):
        start, end = float(voice["start"]), float(voice["end"])
        duration = end - start
        if duration <= 0:
            warnings.append(f"Skipped invalid transcript segment {voice.get('id', index + 1)}")
            continue
        scene = scenes[index] if index < len(scenes) else None
        asset, selection_reason = choose_asset(assets, scene, index)
        scene_id = scene["scene_id"] if scene else f"{index + 1:02d}"
        status, source_start, source_end, speed = "READY", None, None, None
        reason = selection_reason
        confidence = 0.45 if "fallback" in selection_reason else 0.75
        if not asset:
            status, reason, confidence = "NEED_MORE_FOOTAGE", selection_reason, 0.0
        else:
            available = float(asset["duration"]) - cursors[asset["path"]]
            required_min = duration * MIN_SPEED
            if available + 1e-6 < required_min:
                status = "NEED_MORE_FOOTAGE"
                reason += f"; only {max(available, 0):.2f}s unused but need at least {required_min:.2f}s"
                confidence = min(confidence, 0.2)
            else:
                source_duration = min(duration, available)
                if source_duration < required_min:
                    source_duration = available
                speed = round(source_duration / duration, 4)
                if not MIN_SPEED <= speed <= MAX_SPEED:
                    status, reason, confidence = "NEED_MORE_FOOTAGE", reason + "; speed outside allowed range", min(confidence, 0.2)
                else:
                    source_start = round(cursors[asset["path"]], 3)
                    source_end = round(source_start + source_duration, 3)
                    cursors[asset["path"]] = source_end
        timeline.append({
            "id": voice.get("id", f"seg-{index + 1:03d}"), "scene_id": scene_id,
            "video_file": asset["path"] if asset else None, "timeline_start": round(start, 3), "timeline_end": round(end, 3),
            "source_start": source_start, "source_end": source_end, "speed": speed,
            "matched_text": str(voice.get("text", "")).strip(), "reason": reason,
            "confidence": round(confidence, 2), "status": status,
        })
    args.output.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "source_assets": str(args.assets.resolve()), "source_transcript": str(args.transcript.resolve()), "segments": timeline}
    (args.output / "timeline.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output / "subtitle.srt").open("w", encoding="utf-8") as handle:
        for number, segment in enumerate(timeline, start=1):
            handle.write(f"{number}\n{srt_time(segment['timeline_start'])} --> {srt_time(segment['timeline_end'])}\n{segment['matched_text']}\n\n")
    ready_count = sum(segment["status"] == "READY" for segment in timeline)
    preview = ["# Timeline preview", "", f"Ready: {ready_count}/{len(timeline)}. Review before creating a Jianying draft.", "", "| # | Voice timing | Scene | Footage | Status |", "| --- | --- | --- | --- | --- |"]
    for number, segment in enumerate(timeline, start=1):
        footage = Path(segment["video_file"]).name if segment["video_file"] else "—"
        preview.append(f"| {number} | {segment['timeline_start']:.2f}–{segment['timeline_end']:.2f}s | {segment['scene_id']} | {footage} | {segment['status']} |")
    (args.output / "timeline_preview.md").write_text("\n".join(preview) + "\n", encoding="utf-8")
    report = ["# Edit report", "", f"Segments: {len(timeline)}", f"Ready: {ready_count}", f"Needs footage/review: {len(timeline) - ready_count}", "", "## Review items"]
    report.extend([f"- `{item['id']}`: {item['reason']}" for item in timeline if item["status"] != "READY"] or ["- None"])
    if warnings:
        report.extend(["", "## Input warnings", *[f"- {warning}" for warning in warnings]])
    (args.output / "edit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Created {args.output}; {ready_count}/{len(timeline)} segment(s) ready for review.")
    return 0 if ready_count == len(timeline) else 3


if __name__ == "__main__":
    raise SystemExit(main())
