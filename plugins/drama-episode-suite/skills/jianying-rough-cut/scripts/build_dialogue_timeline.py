#!/usr/bin/env python3
"""Build a ripple timeline with 1x source dialogue and deleted narration dialogue."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from subtitle_rules import normalize_cues

EPSILON = 0.002
MAX_ACCELERATION = 1.2


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_part, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d},{milliseconds:03d}"


def join_words(words: list[dict], language: str) -> str:
    primary = language.casefold().replace("_", "-").split("-", 1)[0]
    separator = "" if primary in {"ja", "zh", "th"} else " "
    text = separator.join(str(word.get("word", "")).strip() for word in words).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def add_interval(intervals: list[dict], *, kind: str, source_start: float, source_end: float,
                 timeline_start: float, timeline_end: float, anchor: dict | None = None,
                 reuses_source: bool = False, has_voice: bool = False,
                 beat_id: str | None = None) -> None:
    source_duration = source_end - source_start
    target_duration = timeline_end - timeline_start
    if source_duration <= EPSILON or target_duration <= EPSILON:
        raise ValueError(
            f"Invalid {kind} interval: source {source_start:.3f}-{source_end:.3f}, "
            f"timeline {timeline_start:.3f}-{timeline_end:.3f}"
        )
    speed = source_duration / target_duration
    if kind == "dialogue" and abs(speed - 1.0) > EPSILON:
        raise ValueError(f"Dialogue must stay at 1x, got {speed:.6f}x")
    intervals.append({
        "kind": kind,
        "anchor_id": anchor.get("id") if anchor else None,
        "text": anchor.get("target_text", anchor.get("source_text", "")) if anchor else "",
        "source_start": source_start,
        "source_end": source_end,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "speed": speed,
        "reuses_source": reuses_source,
        "has_voice": has_voice,
        "beat_id": beat_id,
    })


def asset_offsets(assets: list[dict]) -> tuple[list[tuple[float, float, dict]], float]:
    offsets: list[tuple[float, float, dict]] = []
    cursor = 0.0
    for asset in assets:
        end = cursor + float(asset["duration"])
        offsets.append((cursor, end, asset))
        cursor = end
    return offsets, cursor


def add_hold(intervals: list[dict], offsets: list[tuple[float, float, dict]], source_position: float,
             timeline_start: float, timeline_end: float, frame_duration: float,
             beat_id: str | None = None) -> None:
    total = offsets[-1][1]
    if source_position > EPSILON:
        source_end = min(total, source_position)
        source_start = max(0.0, source_end - frame_duration)
    else:
        source_start = 0.0
        source_end = min(total, frame_duration)
    if source_end - source_start <= EPSILON:
        raise ValueError("Cannot create a video hold because the source has no usable frame")
    add_interval(
        intervals, kind="hold", source_start=source_start, source_end=source_end,
        timeline_start=timeline_start, timeline_end=timeline_end,
        reuses_source=True, has_voice=True, beat_id=beat_id,
    )


def split_intervals(intervals: list[dict], offsets: list[tuple[float, float, dict]],
                    max_acceleration: float) -> list[dict]:
    output: list[dict] = []
    for interval in intervals:
        speed = float(interval["speed"])
        intersecting = [
            entry for entry in offsets
            if entry[1] > float(interval["source_start"]) + EPSILON
            and entry[0] < float(interval["source_end"]) - EPSILON
        ]
        if not intersecting:
            raise ValueError(f"No source asset intersects {interval['kind']} interval")
        for part_index, (asset_start, asset_end, asset) in enumerate(intersecting):
            source_start = max(float(interval["source_start"]), asset_start)
            source_end = min(float(interval["source_end"]), asset_end)
            timeline_start = float(interval["timeline_start"]) + (
                source_start - float(interval["source_start"])
            ) / speed
            timeline_end = float(interval["timeline_start"]) + (
                source_end - float(interval["source_start"])
            ) / speed
            if part_index == len(intersecting) - 1:
                timeline_end = float(interval["timeline_end"])
            warning = None
            if interval["kind"] == "dialogue" and abs(speed - 1.0) > EPSILON:
                warning = f"ERROR dialogue speed: {speed:.4f}x"
            elif interval["kind"] == "hold":
                warning = f"reused-frame hold: {timeline_end - timeline_start:.3f}s"
            elif speed > max_acceleration + EPSILON:
                raise ValueError(
                    f"Non-dialogue acceleration exceeds {max_acceleration:.3f}x: {speed:.6f}x"
                )
            output.append({
                "id": f"segment-{len(output) + 1:03d}",
                "kind": interval["kind"],
                "anchor_id": interval["anchor_id"],
                "beat_id": interval.get("beat_id"),
                "text": interval["text"],
                "video_file": asset["path"],
                "source_asset_index": int(asset.get("source_index", 0)),
                "source_start": round(source_start - asset_start, 6),
                "source_end": round(source_end - asset_start, 6),
                "source_global_start": round(source_start, 6),
                "source_global_end": round(source_end, 6),
                "timeline_start": round(timeline_start, 6),
                "timeline_end": round(timeline_end, 6),
                "speed": round(speed, 6),
                "reuses_source": bool(interval["reuses_source"]),
                "has_voice": bool(interval["has_voice"]),
                "status": "READY",
                "warning": warning,
            })
    return output


def build_voice_cues(narration: dict, voice_regions: list[dict], language: str) -> list[dict]:
    cues: list[dict] = []
    for region in voice_regions:
        source_start = float(region["source_start"])
        source_end = float(region["source_end"])
        timeline_start = float(region["timeline_start"])
        for segment in narration.get("segments", []):
            segment_start = float(segment["start"])
            segment_end = float(segment["end"])
            overlap_start = max(source_start, segment_start)
            overlap_end = min(source_end, segment_end)
            if overlap_end <= overlap_start + EPSILON:
                continue
            full_segment = overlap_start <= segment_start + EPSILON and overlap_end >= segment_end - EPSILON
            if full_segment:
                text = str(segment.get("text", "")).strip()
                cue_source_start, cue_source_end = segment_start, segment_end
            else:
                words = [
                    word for word in segment.get("words", [])
                    if overlap_start - EPSILON <= (float(word["start"]) + float(word["end"])) / 2 <= overlap_end + EPSILON
                ]
                if not words:
                    continue
                text = join_words(words, language)
                cue_source_start = max(overlap_start, float(words[0]["start"]))
                cue_source_end = min(overlap_end, float(words[-1]["end"]))
            if text:
                cues.append({
                    "kind": "narration",
                    "start": timeline_start + (cue_source_start - source_start),
                    "end": timeline_start + (cue_source_end - source_start),
                    "text": text,
                })
    return cues


def load_semantic_beats(path: Path | None, source_duration: float,
                        voice_duration: float) -> list[dict]:
    if path is None:
        return []
    data = load_json(path)
    if data.get("mode") != "semantic_narration_video_sync":
        raise ValueError("Beat map mode must be semantic_narration_video_sync")
    beats = sorted(data.get("anchors", []), key=lambda item: float(item["voice_time"]))
    previous_source = -1.0
    previous_voice = -1.0
    for beat in beats:
        beat_id = str(beat.get("id") or "unknown")
        if beat.get("status") != "READY":
            raise ValueError(f"Semantic beat {beat_id} has not been reviewed")
        source_time = float(beat["source_time"])
        voice_time = float(beat["voice_time"])
        if not EPSILON < source_time < source_duration - EPSILON:
            raise ValueError(f"Semantic beat {beat_id} source_time is outside the source")
        if not EPSILON < voice_time < voice_duration - EPSILON:
            raise ValueError(f"Semantic beat {beat_id} voice_time is outside the narration")
        if source_time <= previous_source + EPSILON or voice_time <= previous_voice + EPSILON:
            raise ValueError("Semantic beat anchors must be strictly monotonic in source and narration")
        if not str(beat.get("narration_text", "")).strip():
            raise ValueError(f"Semantic beat {beat_id} is missing narration_text")
        if not str(beat.get("visual_match", "")).strip():
            raise ValueError(f"Semantic beat {beat_id} is missing visual_match")
        previous_source, previous_voice = source_time, voice_time
    return beats


def audit(segments: list[dict], voice_regions: list[dict], source_duration: float,
          final_duration: float, max_acceleration: float) -> list[str]:
    errors: list[str] = []
    if not segments:
        return ["timeline has no video segments"]
    ordered = sorted(segments, key=lambda item: float(item["timeline_start"]))
    if abs(float(ordered[0]["timeline_start"])) > EPSILON:
        errors.append("timeline does not start at zero")
    for previous, current in zip(ordered, ordered[1:]):
        if abs(float(previous["timeline_end"]) - float(current["timeline_start"])) > EPSILON:
            errors.append(f"target gap/overlap between {previous['id']} and {current['id']}")
    if abs(float(ordered[-1]["timeline_end"]) - final_duration) > EPSILON:
        errors.append("video timeline does not reach final duration")

    source_segments = sorted(
        (item for item in segments if not item.get("reuses_source")),
        key=lambda item: float(item["source_global_start"]),
    )
    if not source_segments or abs(float(source_segments[0]["source_global_start"])) > EPSILON:
        errors.append("source-video coverage does not start at zero")
    for previous, current in zip(source_segments, source_segments[1:]):
        if abs(float(previous["source_global_end"]) - float(current["source_global_start"])) > EPSILON:
            errors.append(f"source coverage gap/overlap between {previous['id']} and {current['id']}")
    if source_segments and abs(float(source_segments[-1]["source_global_end"]) - source_duration) > EPSILON:
        errors.append("source-video coverage does not reach full duration")
    for item in segments:
        if item["kind"] == "dialogue" and abs(float(item["speed"]) - 1.0) > EPSILON:
            errors.append(f"dialogue is not 1x at {item['id']}")
        if item["kind"] != "dialogue" and float(item["speed"]) > max_acceleration + EPSILON:
            errors.append(
                f"non-dialogue acceleration exceeds {max_acceleration:.3f}x at {item['id']}"
            )
    for region in voice_regions:
        source_length = float(region["source_end"]) - float(region["source_start"])
        target_length = float(region["timeline_end"]) - float(region["timeline_start"])
        if abs(source_length - target_length) > EPSILON:
            errors.append(f"voice region is retimed at {region['id']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--dialogues", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New preview_vNN directory")
    parser.add_argument("--subtitle-language", help="Defaults to the narration transcript language")
    parser.add_argument(
        "--beat-map", type=Path,
        help="Reviewed semantic_narration_video_sync anchors for narration-to-picture card points",
    )
    parser.add_argument(
        "--max-acceleration", type=float, default=MAX_ACCELERATION,
        help="Hard cap for non-dialogue acceleration; values above 1.2 are rejected",
    )
    parser.add_argument("--hold-frame-duration", type=float, default=1 / 30)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"Refusing to overwrite non-empty output directory: {args.output}")
    if not 0 < args.max_acceleration <= MAX_ACCELERATION:
        parser.error("--max-acceleration must be greater than 0 and cannot exceed 1.2")

    assets_data = load_json(args.assets)
    narration = load_json(args.narration)
    dialogue_data = load_json(args.dialogues)
    assets = assets_data.get("assets", [])
    if not assets:
        parser.error("Assets manifest has no videos")
    offsets, source_duration = asset_offsets(assets)
    voice_duration = float(
        narration.get("duration") or max(float(item["end"]) for item in narration.get("segments", []))
    )
    try:
        semantic_beats = load_semantic_beats(args.beat_map, source_duration, voice_duration)
    except ValueError as error:
        parser.error(str(error))
    unused_beat_ids = {str(item.get("id") or "unknown") for item in semantic_beats}
    language = args.subtitle_language or str(narration.get("language", ""))
    if not language:
        parser.error("Subtitle language is missing; pass --subtitle-language")

    anchors = dialogue_data.get("anchors", [])
    unresolved = [item.get("id", "unknown") for item in anchors if item.get("status") != "READY"]
    if unresolved:
        parser.error("Dialogue map has unresolved anchors: " + ", ".join(unresolved))
    if not anchors:
        parser.error("Dialogue map has no anchors")
    anchors = sorted(anchors, key=lambda item: float(item["target_start"]))

    intervals: list[dict] = []
    voice_regions: list[dict] = []
    output_anchors: list[dict] = []
    source_cursor = 0.0
    voice_cursor = 0.0
    final_cursor = 0.0

    def append_gap_piece(source_start: float, source_end: float,
                         voice_start: float, voice_end: float,
                         beat_id: str | None) -> None:
        nonlocal final_cursor
        source_length = source_end - source_start
        voice_length = voice_end - voice_start
        if source_length <= EPSILON and voice_length <= EPSILON:
            return
        gap_start = final_cursor
        if source_length > EPSILON and voice_length > EPSILON:
            # Slow motion is unrestricted.  Acceleration is a hard 1.2x cap;
            # if necessary the picture interval is lengthened and the next
            # narration beat ripples later instead of speeding the video up.
            target_length = max(voice_length, source_length / args.max_acceleration)
            final_cursor += target_length
            add_interval(
                intervals, kind="gap", source_start=source_start, source_end=source_end,
                timeline_start=gap_start, timeline_end=final_cursor, has_voice=True,
                beat_id=beat_id,
            )
            voice_regions.append({
                "id": f"voice-{len(voice_regions) + 1:03d}",
                **({"beat_id": beat_id} if beat_id is not None else {}),
                "source_start": round(voice_start, 6), "source_end": round(voice_end, 6),
                "timeline_start": round(gap_start, 6),
                "timeline_end": round(gap_start + voice_length, 6),
            })
        elif source_length > EPSILON:
            # With no narration to constrain it, preserve the picture at 1x.
            final_cursor += source_length
            add_interval(
                intervals, kind="gap", source_start=source_start, source_end=source_end,
                timeline_start=gap_start, timeline_end=final_cursor, has_voice=False,
                beat_id=beat_id,
            )
        else:
            final_cursor += voice_length
            add_hold(
                intervals, offsets, source_start, gap_start, final_cursor,
                max(EPSILON * 2, float(args.hold_frame_duration)), beat_id=beat_id,
            )
            voice_regions.append({
                "id": f"voice-{len(voice_regions) + 1:03d}",
                **({"beat_id": beat_id} if beat_id is not None else {}),
                "source_start": round(voice_start, 6), "source_end": round(voice_end, 6),
                "timeline_start": round(gap_start, 6), "timeline_end": round(final_cursor, 6),
            })

    def append_gap(source_start: float, source_end: float,
                   voice_start: float, voice_end: float) -> None:
        internal = [
            beat for beat in semantic_beats
            if source_start + EPSILON < float(beat["source_time"]) < source_end - EPSILON
            and voice_start + EPSILON < float(beat["voice_time"]) < voice_end - EPSILON
        ]
        points = [(source_start, voice_start, None)]
        points.extend(
            (float(beat["source_time"]), float(beat["voice_time"]), str(beat.get("id") or "unknown"))
            for beat in internal
        )
        points.append((source_end, voice_end, None))
        for index in range(len(points) - 1):
            source_a, voice_a, current_beat_id = points[index]
            source_b, voice_b, _ = points[index + 1]
            append_gap_piece(source_a, source_b, voice_a, voice_b, current_beat_id)
            if current_beat_id is not None:
                unused_beat_ids.discard(current_beat_id)

    for anchor in anchors:
        source_start, source_end = float(anchor["source_start"]), float(anchor["source_end"])
        target_start, target_end = float(anchor["target_start"]), float(anchor["target_end"])
        if source_start < source_cursor - EPSILON or target_start < voice_cursor - EPSILON:
            parser.error(f"Dialogue anchors are not monotonic or overlap at {anchor.get('id')}")
        append_gap(source_cursor, source_start, voice_cursor, target_start)
        dialogue_start = final_cursor
        dialogue_duration = source_end - source_start
        if dialogue_duration <= EPSILON:
            parser.error(f"Dialogue anchor has no source duration: {anchor.get('id')}")
        final_cursor += dialogue_duration
        try:
            add_interval(
                intervals, kind="dialogue", source_start=source_start, source_end=source_end,
                timeline_start=dialogue_start, timeline_end=final_cursor, anchor=anchor,
            )
        except ValueError as error:
            parser.error(str(error))
        output_anchors.append({
            **anchor,
            "narration_source_start": round(target_start, 6),
            "narration_source_end": round(target_end, 6),
            "timeline_start": round(dialogue_start, 6),
            "timeline_end": round(final_cursor, 6),
            "speed": 1.0,
            "narration_action": "DELETE",
        })
        source_cursor, voice_cursor = source_end, target_end

    append_gap(source_cursor, source_duration, voice_cursor, voice_duration)
    if unused_beat_ids:
        parser.error(
            "Semantic beats do not fall inside the same source/narration gap: "
            + ", ".join(sorted(unused_beat_ids))
        )
    try:
        segments = split_intervals(intervals, offsets, args.max_acceleration)
    except ValueError as error:
        parser.error(str(error))
    errors = audit(
        segments, voice_regions, source_duration, final_cursor, args.max_acceleration
    )
    if errors:
        raise SystemExit("Timeline audit failed: " + "; ".join(errors))

    cues = build_voice_cues(narration, voice_regions, language)
    cues.extend({
        "kind": "dialogue", "start": float(anchor["timeline_start"]),
        "end": float(anchor["timeline_end"]),
        "text": str(anchor.get("target_text") or anchor.get("source_text") or "").strip(),
    } for anchor in output_anchors)
    try:
        cues, subtitle_profile = normalize_cues(cues, language)
    except ValueError as error:
        parser.error(str(error))

    args.output.mkdir(parents=True, exist_ok=True)
    deleted_duration = sum(
        float(anchor["narration_source_end"]) - float(anchor["narration_source_start"])
        for anchor in output_anchors
    )
    inserted_dialogue_duration = sum(
        float(anchor["timeline_end"]) - float(anchor["timeline_start"])
        for anchor in output_anchors
    )
    document = {
        "version": 3, "mode": "dialogue_replacement_ripple",
        "source_assets": str(args.assets.resolve()),
        "source_narration": str(args.narration.resolve()),
        "source_dialogues": str(args.dialogues.resolve()),
        "voice_source_duration": round(voice_duration, 6),
        "source_video_duration": round(source_duration, 6),
        "final_duration": round(final_cursor, 6),
        "canvas": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "preservation": {
            "full_video": True, "source_order_preserved": True,
            "dialogue_video_speed_one": True, "voice_dialogue_deleted": True,
            "remaining_voice_ranges_preserved": True,
            "non_dialogue_acceleration_capped": True,
            "semantic_alignment_reviewed": bool(semantic_beats),
        },
        "audio_policy": {
            "gap": {
                "voice_present": True, "video_volume": 0.0,
                "max_video_acceleration": args.max_acceleration,
                "slow_motion_minimum": None,
            },
            "dialogue": {"voice_present": False, "video_volume": 1.0, "video_speed": 1.0},
            "deleted_voice_duration": round(deleted_duration, 6),
            "inserted_dialogue_duration": round(inserted_dialogue_duration, 6),
        },
        "subtitle_profile": {
            "language": language, "profile": subtitle_profile["key"],
            "font_name": subtitle_profile["font_name"], "size": subtitle_profile["size"],
            "line_spacing": subtitle_profile["line_spacing"], "letter_spacing": 0,
            "color": "#FFFFFF", "outline_color": "#000000", "outline_width": 8,
            "max_line_width": 0.70, "transform_y": -0.833333,
            "y_pixels": -800, "max_lines": 2,
            "horizontal_alignment": "center", "vertical_alignment": "top",
        },
        "semantic_alignment": {
            "beat_map": str(args.beat_map.resolve()) if args.beat_map else None,
            "reviewed": bool(semantic_beats),
            "anchor_count": len(semantic_beats),
            "anchors": semantic_beats,
        },
        "voice_regions": voice_regions, "dialogue_anchors": output_anchors,
        "segments": segments, "subtitle_cues": cues,
    }
    (args.output / "timeline.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (args.output / "subtitle.srt").open("w", encoding="utf-8") as handle:
        for number, cue in enumerate(cues, start=1):
            handle.write(
                f"{number}\n{srt_time(float(cue['start']))} --> {srt_time(float(cue['end']))}\n"
                f"{str(cue['text']).strip()}\n\n"
            )

    preview = [
        "# Dialogue replacement timeline preview", "", "Canvas: 1080x1920 (9:16).",
        f"Full ordered source video preserved: yes ({source_duration:.3f}s source).",
        f"Narration dialogue deleted: {deleted_duration:.3f}s.",
        f"Final timeline: {final_cursor:.3f}s.",
        "All source-video dialogue segments: fixed 1.0000x.", "",
        f"Non-dialogue acceleration: hard capped at {args.max_acceleration:.3f}x; slow motion has no minimum.",
        f"Reviewed semantic card points: {len(semantic_beats)}.", "",
        "| # | Type | Source file/range | Final range | Speed | Beat | Dialogue | Warning |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number, segment in enumerate(segments, start=1):
        preview.append(
            f"| {number} | {segment['kind']} | {Path(segment['video_file']).name} "
            f"{segment['source_start']:.3f}-{segment['source_end']:.3f}s | "
            f"{segment['timeline_start']:.3f}-{segment['timeline_end']:.3f}s | "
            f"{segment['speed']:.4f}x | {segment.get('beat_id') or ''} | "
            f"{segment.get('text', '')} | {segment.get('warning') or ''} |"
        )
    (args.output / "timeline_preview.md").write_text("\n".join(preview) + "\n", encoding="utf-8")

    warnings = [f"{item['id']}: {item['warning']}" for item in segments if item.get("warning")]
    report = [
        "# Edit report", "", "Ripple replacement audit: PASS",
        "Canvas is fixed before media insertion: 1080x1920 (9:16).",
        f"Source videos covered continuously in order: 0.000-{source_duration:.3f}s.",
        f"Narration dialogue deleted from the audio track: {deleted_duration:.3f}s.",
        f"Remaining narration ranges retain 1x audio and ripple to a {final_cursor:.3f}s final timeline.",
        f"Dialogue anchors at fixed 1x: {len(output_anchors)}.",
        f"Non-dialogue acceleration hard cap: {args.max_acceleration:.3f}x; slow motion unrestricted.",
        f"Reviewed semantic narration-picture anchors: {len(semantic_beats)}.",
        f"Subtitle cues after ripple and semantic wrapping: {len(cues)}.", "",
        "Every subtitle cue: at most two explicit safe-width lines; target Y=-800.", "",
        "## Timeline warnings",
    ]
    report.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        report.append("- none")
    (args.output / "edit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(
        f"Created ripple replacement timeline {args.output}; {len(output_anchors)} dialogue anchor(s) at 1x, "
        f"{len(voice_regions)} retained narration region(s), {len(cues)} subtitle cue(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
