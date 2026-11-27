#!/usr/bin/env python3
"""Reject dialogue deletions that clip matched speech or cross adjacent words."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EPSILON = 0.003
DIALOGUE = "DIALOGUE_USE_VIDEO_AUDIO"
NARRATION = "NARRATION_USE_MASTER_VO"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def boundary_errors(anchor: dict) -> list[str]:
    errors: list[str] = []
    anchor_id = str(anchor.get("id") or "unknown")
    required = (
        "source_speech_start", "source_speech_end",
        "target_speech_start", "target_speech_end",
    )
    missing = [key for key in required if anchor.get(key) is None]
    if missing:
        return [f"{anchor_id}: missing evidence fields: {', '.join(missing)}"]
    for prefix in ("source", "target"):
        cut_start = float(anchor[f"{prefix}_start"])
        cut_end = float(anchor[f"{prefix}_end"])
        speech_start = float(anchor[f"{prefix}_speech_start"])
        speech_end = float(anchor[f"{prefix}_speech_end"])
        previous_end = anchor.get(f"{prefix}_previous_end")
        next_start = anchor.get(f"{prefix}_next_start")
        if cut_start > speech_start + EPSILON or cut_end < speech_end - EPSILON:
            errors.append(f"{anchor_id}: {prefix} boundary clips matched speech")
        if previous_end is not None and cut_start < float(previous_end) - EPSILON:
            errors.append(f"{anchor_id}: {prefix} boundary crosses previous spoken word")
        if next_start is not None and cut_end > float(next_start) + EPSILON:
            errors.append(f"{anchor_id}: {prefix} boundary crosses next spoken word")
    if anchor.get("boundary_safe") is not True:
        errors.append(f"{anchor_id}: boundary_safe is not true")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dialogues", type=Path, required=True)
    args = parser.parse_args()
    data = load_json(args.dialogues)
    anchors = data.get("anchors", [])
    if not anchors:
        parser.error("Dialogue map has no anchors")
    errors: list[str] = []
    annotations = data.get("dialogue_annotations") or {}
    annotations_complete = annotations.get("dialogue_lines_complete") is True
    if annotations and (annotations.get("status") != "READY" or not annotations_complete):
        errors.append("dialogue annotations are present but not complete and READY")
    for anchor in anchors:
        if anchor.get("status") != "READY":
            errors.append(f"{anchor.get('id', 'unknown')}: status is not READY")
        errors.extend(boundary_errors(anchor))
        if annotations_complete and anchor.get("annotation_role") not in {DIALOGUE, NARRATION}:
            errors.append(
                f"{anchor.get('id', 'unknown')}: missing valid editor dialogue annotation"
            )
    if errors:
        raise SystemExit("Dialogue boundary audit failed: " + "; ".join(errors))
    print(f"Dialogue boundary audit PASS: {len(anchors)} clean anchor(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
