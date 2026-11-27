#!/usr/bin/env python3
"""Build a full-source, order-preserving retime-only timeline with reviewed audio roles."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from subtitle_rules import display_units, normalize_cues


EPSILON = 0.003
MUTE = "MUTE_USE_MASTER_VO"
PRESERVE = "PRESERVE_SOURCE_AUDIO"
DIALOGUE = "DIALOGUE_USE_VIDEO_AUDIO"
NARRATION = "NARRATION_USE_MASTER_VO"
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？…])\s+")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def write_srt(path: Path, cues: list[dict]) -> None:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{srt_time(float(cue['start']))} --> {srt_time(float(cue['end']))}\n{cue['text']}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")


def split_short_semantic_cues(cue: dict, max_units: int = 28) -> list[dict]:
    """Split long Latin-script captions into reference-like phrase-sized timed cues."""
    text = str(cue.get("text", "")).strip()
    if not text or display_units(text) <= max_units:
        return [cue]
    chunks = []
    for sentence in [item.strip() for item in SENTENCE_SPLIT_RE.split(text) if item.strip()]:
        words = sentence.split()
        group_count = max(1, math.ceil(display_units(sentence) / max_units))
        target = display_units(sentence) / group_count
        # Dynamic programming avoids one-word flashes such as "yo." or "pequeña."
        costs = {(0, 0): (0.0, [])}
        for group in range(group_count):
            for (used_groups, start), (cost, parts) in list(costs.items()):
                if used_groups != group:
                    continue
                remaining_groups = group_count - group - 1
                for end in range(start + 1, len(words) + 1):
                    if len(words) - end < remaining_groups:
                        break
                    part = " ".join(words[start:end])
                    units = display_units(part)
                    if units > max_units:
                        break
                    key = (group + 1, end)
                    candidate = (cost + (units - target) ** 2, parts + [part])
                    if key not in costs or candidate[0] < costs[key][0]:
                        costs[key] = candidate
        solution = costs.get((group_count, len(words)))
        if solution:
            chunks.extend(solution[1])
        else:
            chunks.append(sentence)
    if len(chunks) <= 1:
        return [cue]
    start, end = float(cue["start"]), float(cue["end"])
    weights = [max(1, display_units(chunk)) for chunk in chunks]
    total = sum(weights)
    result = []
    cursor = start
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        chunk_end = end if index == len(chunks) - 1 else cursor + (end - start) * weight / total
        result.append({**cue, "start": cursor, "end": chunk_end, "text": chunk})
        cursor = chunk_end
    return result


def retained_voice_ranges(anchors: list[dict], narration: dict) -> list[dict]:
    """Return spoken narration groups, excluding matched dialogue and outer silent handles."""
    slots: list[list[dict]] = [[] for _ in range(len(anchors) + 1)]
    for segment in narration.get("segments", []):
        midpoint = (float(segment["start"]) + float(segment["end"])) / 2
        if any(
            float(anchor.get("target_speech_start", anchor["target_start"])) - EPSILON
            <= midpoint
            <= float(anchor.get("target_speech_end", anchor["target_end"])) + EPSILON
            for anchor in anchors
        ):
            continue
        slot = sum(
            midpoint > float(anchor.get("target_speech_end", anchor["target_end"]))
            for anchor in anchors
        )
        slots[slot].append(segment)
    result = []
    for index, segments in enumerate(slots, start=1):
        if not segments:
            raise ValueError(f"No retained spoken narration remains in picture gap {index}")
        result.append({
            "source_start": float(segments[0]["start"]),
            "source_end": float(segments[-1]["end"]),
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--dialogues", type=Path, required=True)
    parser.add_argument("--shot-map", type=Path, required=True)
    parser.add_argument("--audio-role-map", type=Path, required=True)
    parser.add_argument("--retime-plan", type=Path, required=True)
    parser.add_argument("--subtitle-language", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-speed", type=float, default=0.95)
    parser.add_argument("--max-speed", type=float, default=1.20)
    parser.add_argument("--max-shot-duration", type=float, default=30.0)
    args = parser.parse_args()

    required_paths = [
        args.assets, args.narration, args.dialogues, args.shot_map,
        args.audio_role_map, args.retime_plan,
    ]
    for path in required_paths:
        if not path.is_file():
            parser.error(f"Input does not exist: {path}")
    if args.output.exists():
        parser.error(f"Refusing to overwrite existing output directory: {args.output}")

    assets = load_json(args.assets).get("assets", [])
    narration = load_json(args.narration)
    dialogue_data = load_json(args.dialogues)
    shots = load_json(args.shot_map).get("shots", [])
    role_data = load_json(args.audio_role_map)
    plan = load_json(args.retime_plan)
    if not assets or not shots:
        parser.error("Assets and shot map must be non-empty")
    if role_data.get("all_assets_reviewed") is not True:
        parser.error("Audio-role map is not fully reviewed")
    if plan.get("status") != "READY":
        parser.error("Retime plan must be explicitly reviewed and marked READY")

    role_by_asset = {}
    for item in role_data.get("roles", []):
        index = int(item["asset_index"])
        if item.get("status") != "READY" or item.get("role") not in {MUTE, PRESERVE}:
            parser.error(f"Asset {index} has an unresolved audio role")
        role_by_asset[index] = item
    asset_by_index = {}
    asset_offsets = {}
    source_cursor = 0.0
    for index, asset in enumerate(assets):
        asset_index = int(asset.get("source_index", index))
        if asset_index not in role_by_asset:
            parser.error(f"Asset {asset_index} is missing from the audio-role map")
        asset_by_index[asset_index] = asset
        asset_offsets[asset_index] = source_cursor
        source_cursor += float(asset["duration"])

    annotations = dialogue_data.get("dialogue_annotations") or {}
    annotations_complete = annotations.get("dialogue_lines_complete") is True
    if annotations and (
        annotations.get("status") != "READY" or not annotations_complete
    ):
        parser.error("Dialogue annotations are present but not complete and READY")
    active = []
    ignored = []
    narration_overrides = []
    for anchor in dialogue_data.get("anchors", []):
        if anchor.get("status") != "READY":
            parser.error(f"Dialogue anchor is unresolved: {anchor.get('id', 'unknown')}")
        asset_index = int(anchor["source_asset_index"])
        role = role_by_asset[asset_index]
        annotation_role = anchor.get("annotation_role")
        if annotations_complete and annotation_role not in {DIALOGUE, NARRATION}:
            parser.error(
                f"Dialogue anchor lacks a valid editor annotation: {anchor.get('id', 'unknown')}"
            )
        if annotation_role == DIALOGUE:
            active.append(anchor)
        elif annotation_role == NARRATION:
            narration_overrides.append(anchor)
            ignored.append({
                "id": anchor.get("id"),
                "reason": "editor annotation requires master voice-over for this source interval",
            })
        elif role["role"] == MUTE:
            ignored.append({
                "id": anchor.get("id"),
                "reason": "source asset is reviewed as narration and uses master voice-over",
            })
        else:
            active.append(anchor)
    active.sort(key=lambda item: float(item["source_start"]))
    if any(float(active[i]["source_end"]) > float(active[i + 1]["source_start"]) + EPSILON
           for i in range(len(active) - 1)):
        parser.error("Active source dialogue anchors overlap")
    if any(float(active[i]["target_end"]) > float(active[i + 1]["target_start"]) + EPSILON
           for i in range(len(active) - 1)):
        parser.error("Active narration dialogue ranges overlap")

    plan_by_shot = {item["shot_id"]: item for item in plan.get("shots", [])}
    missing_plan = [shot["id"] for shot in shots if shot["id"] not in plan_by_shot]
    if missing_plan:
        parser.error("Retime plan is missing shots: " + ", ".join(missing_plan))

    pieces = []
    for shot in shots:
        shot_start = float(shot["global_start"])
        shot_end = float(shot["global_end"])
        boundaries = {shot_start, shot_end}
        for anchor in active + narration_overrides:
            for key in ("source_start", "source_speech_start", "source_speech_end", "source_end"):
                value = float(anchor[key])
                if shot_start + EPSILON < value < shot_end - EPSILON:
                    boundaries.add(value)
        points = sorted(boundaries)
        planned_speed = float(plan_by_shot[shot["id"]]["speed"])
        for start, end in zip(points, points[1:]):
            if end <= start + EPSILON:
                continue
            midpoint = (start + end) / 2
            anchor = next((item for item in active
                           if float(item["source_start"]) - EPSILON <= midpoint
                           <= float(item["source_end"]) + EPSILON), None)
            speech_anchor = next((item for item in active
                                  if float(item.get("source_speech_start", item["source_start"]))
                                  - EPSILON <= midpoint
                                  <= float(item.get("source_speech_end", item["source_end"]))
                                   + EPSILON), None)
            narration_override = next((item for item in narration_overrides
                                        if float(item["source_start"]) - EPSILON <= midpoint
                                        <= float(item["source_end"]) + EPSILON), None)
            source_duration = end - start
            target_duration = source_duration if anchor else source_duration / planned_speed
            pieces.append({
                "shot_id": shot["id"],
                "asset_index": int(shot["asset_index"]),
                "global_source_start": start,
                "global_source_end": end,
                "source_duration": source_duration,
                "target_duration": target_duration,
                "kind": "dialogue" if anchor else "picture",
                "dialogue_id": anchor.get("id") if anchor else None,
                "dialogue_speech_id": speech_anchor.get("id") if speech_anchor else None,
                "source_audio_override": MUTE if narration_override else (
                    PRESERVE if anchor and anchor.get("annotation_role") == DIALOGUE else None
                ),
                "audio_override_annotation_id": (
                    narration_override.get("annotation_id") if narration_override else (
                        anchor.get("annotation_id") if anchor else None
                    )
                ),
            })

    voice_duration = float(narration["duration"])
    try:
        voice_source_ranges = retained_voice_ranges(active, narration)
    except ValueError as error:
        parser.error(str(error))
    if len(voice_source_ranges) != len(active) + 1:
        parser.error(
            "Each active dialogue must be surrounded by retained narration; "
            "zero-length narration gaps need explicit manual handling"
        )

    gap_groups = []
    cursor = 0
    for anchor in active:
        indices = [index for index, piece in enumerate(pieces)
                   if piece.get("dialogue_speech_id") == anchor.get("id")]
        if not indices:
            parser.error(f"Dialogue anchor falls outside the shot map: {anchor.get('id')}")
        first, last = min(indices), max(indices)
        gap_groups.append(list(range(cursor, first)))
        cursor = last + 1
    gap_groups.append(list(range(cursor, len(pieces))))

    stretch_events = []
    for group_index, (indices, voice_range) in enumerate(zip(gap_groups, voice_source_ranges), start=1):
        required = float(voice_range["source_end"]) - float(voice_range["source_start"])
        baseline = sum(float(pieces[index]["target_duration"]) for index in indices)
        if baseline + EPSILON < required:
            if baseline <= EPSILON:
                parser.error(f"Picture gap {group_index} has no footage for {required:.3f}s narration")
            stretchable = [index for index in indices if pieces[index]["kind"] != "dialogue"]
            stretchable_duration = sum(float(pieces[index]["target_duration"]) for index in stretchable)
            if stretchable_duration <= EPSILON:
                parser.error(f"Picture gap {group_index} has no non-dialogue footage to retime")
            extra = required - baseline
            factor = (stretchable_duration + extra) / stretchable_duration
            for index in stretchable:
                pieces[index]["target_duration"] *= factor
                speed = pieces[index]["source_duration"] / pieces[index]["target_duration"]
                if speed < args.min_speed - EPSILON:
                    parser.error(
                        f"Picture gap {group_index} cannot fit narration without slowing below "
                        f"{args.min_speed:.2f}x (baseline={baseline:.3f}s, voice={required:.3f}s, "
                        f"required segment speed={speed:.3f}x)"
                    )
            stretch_events.append({
                "gap_index": group_index,
                "baseline_duration": baseline,
                "required_voice_duration": required,
                "non_dialogue_stretch_factor": factor,
            })

    timeline_cursor = 0.0
    segments = []
    for index, piece in enumerate(pieces, start=1):
        asset_index = int(piece["asset_index"])
        role = role_by_asset[asset_index]
        target_duration = float(piece["target_duration"])
        speed = float(piece["source_duration"]) / target_duration
        if not args.min_speed - EPSILON <= speed <= args.max_speed + EPSILON:
            parser.error(f"Segment {index} speed is outside policy: {speed:.6f}x")
        if piece["kind"] != "dialogue" and target_duration > args.max_shot_duration + EPSILON:
            parser.error(f"Segment {index} exceeds maximum shot duration: {target_duration:.3f}s")
        local_start = float(piece["global_source_start"]) - asset_offsets[asset_index]
        local_end = float(piece["global_source_end"]) - asset_offsets[asset_index]
        effective_audio_role = piece.get("source_audio_override") or role["role"]
        segment = {
            "id": f"segment-{index:03d}",
            "status": "READY",
            "kind": piece["kind"],
            "shot_id": piece["shot_id"],
            "dialogue_id": piece.get("dialogue_id"),
            "asset_index": asset_index,
            "video_file": str(Path(asset_by_index[asset_index]["path"]).resolve()),
            "source_start": local_start,
            "source_end": local_end,
            "global_source_start": float(piece["global_source_start"]),
            "global_source_end": float(piece["global_source_end"]),
            "timeline_start": timeline_cursor,
            "timeline_end": timeline_cursor + target_duration,
            "speed": speed,
            "source_audio_volume": 0.0 if effective_audio_role == MUTE else 1.0,
            "audio_role": effective_audio_role,
            "asset_audio_role": role["role"],
            "audio_override_annotation_id": piece.get("audio_override_annotation_id"),
            "reuses_source": False,
        }
        segments.append(segment)
        timeline_cursor += target_duration

    def source_to_timeline(source_position: float) -> float:
        for segment in segments:
            start = float(segment["global_source_start"])
            end = float(segment["global_source_end"])
            if start - EPSILON <= source_position <= end + EPSILON:
                clamped = min(end, max(start, source_position))
                return float(segment["timeline_start"]) + (clamped - start) / float(segment["speed"])
        raise ValueError(f"Source position is outside timeline: {source_position:.3f}")

    dialogue_timeline_ranges = {}
    for anchor in active:
        matching = [item for item in segments if item.get("dialogue_id") == anchor.get("id")]
        dialogue_timeline_ranges[anchor["id"]] = {
            "timeline_start": min(float(item["timeline_start"]) for item in matching),
            "timeline_end": max(float(item["timeline_end"]) for item in matching),
        }

    voice_regions = []
    gap_timeline_ranges = []
    for group_index, (indices, source_range) in enumerate(zip(gap_groups, voice_source_ranges), start=1):
        if indices:
            gap_start = float(segments[indices[0]]["timeline_start"])
            gap_end = float(segments[indices[-1]]["timeline_end"])
        else:
            gap_start = gap_end = 0.0
        source_start = float(source_range["source_start"])
        source_end = float(source_range["source_end"])
        duration = source_end - source_start
        if gap_end - gap_start + EPSILON < duration:
            parser.error(f"Internal error: picture gap {group_index} is shorter than retained narration")
        voice_regions.append({
            "id": f"voice-region-{group_index:03d}",
            "status": "READY",
            "source_start": source_start,
            "source_end": source_end,
            "timeline_start": gap_start,
            "timeline_end": gap_start + duration,
            "speed": 1.0,
        })
        gap_timeline_ranges.append({
            "gap_index": group_index,
            "timeline_start": gap_start,
            "timeline_end": gap_end,
            "voice_end": gap_start + duration,
            "natural_moving_room": (gap_end - gap_start) - duration,
        })

    cues = []
    for region in voice_regions:
        source_start = float(region["source_start"])
        source_end = float(region["source_end"])
        for narration_segment in narration.get("segments", []):
            start = max(source_start, float(narration_segment["start"]))
            end = min(source_end, float(narration_segment["end"]))
            if end <= start + 0.05:
                continue
            cues.append({
                "start": float(region["timeline_start"]) + start - source_start,
                "end": float(region["timeline_start"]) + end - source_start,
                "text": str(narration_segment.get("text", "")).strip(),
                "speaker": "narration",
            })
    for anchor in active:
        speech_start = float(anchor.get("source_speech_start", anchor["source_start"]))
        speech_end = float(anchor.get("source_speech_end", anchor["source_end"]))
        cues.append({
            "start": source_to_timeline(speech_start),
            "end": source_to_timeline(speech_end),
            "text": str(anchor.get("target_text") or anchor.get("source_text") or "").strip(),
            "speaker": "dialogue",
        })
    cues = [cue for cue in cues if cue["text"]]
    cues = [part for cue in cues for part in split_short_semantic_cues(cue)]
    try:
        cues, subtitle_profile = normalize_cues(cues, args.subtitle_language)
    except ValueError as error:
        parser.error(str(error))

    natural_tail = gap_timeline_ranges[-1]["natural_moving_room"] if gap_timeline_ranges else 0.0
    timeline = {
        "version": 5,
        "mode": "full_source_retime_only",
        "canvas": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "source_assets": str(args.assets.resolve()),
        "source_shot_map": str(args.shot_map.resolve()),
        "audio_role_map": str(args.audio_role_map.resolve()),
        "retime_plan": str(args.retime_plan.resolve()),
        "voice_source_duration": voice_duration,
        "timeline_duration": timeline_cursor,
        "subtitle_profile": {"language": args.subtitle_language, **subtitle_profile},
        "editing_policy": {
            "min_non_dialogue_speed": args.min_speed,
            "max_non_dialogue_speed": args.max_speed,
            "max_non_dialogue_shot_duration": args.max_shot_duration,
            "allow_trim": False,
            "allow_reuse": False,
            "allow_freeze": False,
        },
        "preservation": {
            "full_video": True,
            "source_order_preserved": True,
            "dialogue_video_speed_one": True,
            "voice_dialogue_deleted": True,
            "remaining_voice_ranges_preserved": True,
            "dialogue_boundaries_reviewed": True,
            "audio_roles_reviewed": True,
            "dialogue_lines_annotated": annotations_complete,
            "no_source_trim": True,
            "no_source_reuse": True,
            "no_hold_segments": True,
            "natural_moving_tail": natural_tail >= -EPSILON,
        },
        "ignored_dialogue_candidates": ignored,
        "dialogue_annotations": annotations,
        "active_dialogues": [
            {**anchor, **dialogue_timeline_ranges[anchor["id"]]} for anchor in active
        ],
        "voice_regions": voice_regions,
        "segments": segments,
        "natural_room": gap_timeline_ranges,
    }
    audit = {
        "status": "PASS",
        "mode": timeline["mode"],
        "source_duration": source_cursor,
        "timeline_duration": timeline_cursor,
        "source_trimmed_duration": 0.0,
        "source_reused_duration": 0.0,
        "muted_master_vo_assets": sorted(
            index for index, role in role_by_asset.items() if role["role"] == MUTE
        ),
        "preserved_source_audio_assets": sorted(
            index for index, role in role_by_asset.items() if role["role"] == PRESERVE
        ),
        "dialogue_lines_annotated": annotations_complete,
        "annotated_dialogue_count": sum(
            item.get("annotation_role") == DIALOGUE for item in active
        ),
        "annotated_master_vo_interval_count": len(narration_overrides),
        "interval_audio_override_segment_count": sum(
            item.get("audio_override_annotation_id") is not None for item in segments
        ),
        "ignored_false_dialogue_anchors": ignored,
        "active_dialogue_count": len(active),
        "all_active_dialogue_speed_one": all(
            abs(float(item["speed"]) - 1.0) <= EPSILON
            for item in segments if item["kind"] == "dialogue"
        ),
        "stretch_events": stretch_events,
        "natural_tail_duration": natural_tail,
        "subtitle_cue_count": len(cues),
    }
    args.output.mkdir(parents=True)
    (args.output / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_srt(args.output / "subtitle.srt", cues)
    print(
        f"Created v5 full-source timeline: {args.output} "
        f"({timeline_cursor:.3f}s; {len(active)} dialogue anchors; "
        f"natural tail {natural_tail:.3f}s; zero trim/reuse)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
