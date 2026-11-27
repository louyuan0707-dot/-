#!/usr/bin/env python3
"""Apply a complete editor-supplied dialogue-line manifest to a reviewed dialogue map."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


DIALOGUE = "DIALOGUE_USE_VIDEO_AUDIO"
NARRATION = "NARRATION_USE_MASTER_VO"
ALLOWED_ROLES = {DIALOGUE, NARRATION}
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", flags=re.UNICODE)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def tokens(text: str) -> tuple[str, ...]:
    value = unicodedata.normalize("NFKD", str(text).casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return tuple(WORD_RE.findall(value))


def validate_notes(notes: dict) -> list[dict]:
    if notes.get("dialogue_lines_complete") is not True:
        raise ValueError(
            "dialogue_lines_complete must be true; partial notes cannot be a hard dialogue gate"
        )
    lines = notes.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("notes.lines must contain at least one annotated line")
    seen_ids: set[str] = set()
    output = []
    for index, item in enumerate(lines, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"notes.lines[{index}] must be an object")
        line_id = str(item.get("id") or f"note-{index:03d}")
        role = str(item.get("role") or DIALOGUE)
        text = str(item.get("text") or "").strip()
        if line_id in seen_ids:
            raise ValueError(f"duplicate note id: {line_id}")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"{line_id}: unsupported role {role}")
        if not tokens(text):
            raise ValueError(f"{line_id}: text is empty")
        seen_ids.add(line_id)
        output.append({**item, "id": line_id, "role": role, "text": text})
    return output


def choose_anchor(note: dict, anchors: list[dict], used: set[int]) -> int:
    note_tokens = tokens(note["text"])
    candidates = [
        index for index, anchor in enumerate(anchors)
        if index not in used and tokens(anchor.get("target_text", "")) == note_tokens
    ]
    file_hint = str(note.get("video_file_hint") or "").casefold().strip()
    if file_hint:
        candidates = [
            index for index in candidates
            if file_hint in str(anchors[index].get("video_file", "")).casefold()
        ]
    time_hint = note.get("source_start_hint")
    if time_hint is not None and candidates:
        best_distance = min(
            abs(float(anchors[index].get("source_speech_start", 0.0)) - float(time_hint))
            for index in candidates
        )
        candidates = [
            index for index in candidates
            if abs(float(anchors[index].get("source_speech_start", 0.0)) - float(time_hint))
            <= best_distance + 0.001
        ]
    if not candidates:
        raise ValueError(f"{note['id']}: no dialogue anchor matches {note['text']!r}")
    if len(candidates) > 1:
        raise ValueError(
            f"{note['id']}: {len(candidates)} anchors match; add video_file_hint or source_start_hint"
        )
    return candidates[0]


def apply_notes(dialogue_map: dict, notes: dict) -> dict:
    anchors = [dict(item) for item in dialogue_map.get("anchors", [])]
    if not anchors:
        raise ValueError("dialogue map has no anchors")
    annotated_lines = validate_notes(notes)
    used: set[int] = set()
    for note in annotated_lines:
        anchor_index = choose_anchor(note, anchors, used)
        used.add(anchor_index)
        anchor = anchors[anchor_index]
        anchor["annotation_id"] = note["id"]
        anchor["annotation_role"] = note["role"]
        anchor["annotation_text"] = note["text"]
        anchor["annotation_speaker"] = note.get("speaker")
        anchor["annotation_video_file_hint"] = note.get("video_file_hint")
        anchor["annotation_source_start_hint"] = note.get("source_start_hint")
    for index, anchor in enumerate(anchors):
        if index not in used:
            anchor["annotation_role"] = NARRATION
            anchor["annotation_reason"] = (
                "Not listed as dialogue in a complete editor-supplied dialogue manifest"
            )
    dialogue_count = sum(item.get("annotation_role") == DIALOGUE for item in anchors)
    narration_count = sum(item.get("annotation_role") == NARRATION for item in anchors)
    output = dict(dialogue_map)
    output["anchors"] = anchors
    output["dialogue_annotations"] = {
        "version": 1,
        "source_notes": notes.get("source_notes"),
        "dialogue_lines_complete": True,
        "status": "READY",
        "dialogue_anchor_count": dialogue_count,
        "narration_anchor_count": narration_count,
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dialogues", type=Path, required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.dialogues, args.notes):
        if not path.is_file():
            parser.error(f"Input does not exist: {path}")
    if args.output.exists():
        parser.error(f"Refusing to overwrite existing output: {args.output}")
    try:
        output = apply_notes(load_json(args.dialogues), load_json(args.notes))
    except ValueError as error:
        parser.error(str(error))
    output["dialogue_annotations"]["source_notes"] = str(args.notes.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = output["dialogue_annotations"]
    print(
        f"Applied complete dialogue notes: {summary['dialogue_anchor_count']} dialogue, "
        f"{summary['narration_anchor_count']} master-VO source speech candidate(s): {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
