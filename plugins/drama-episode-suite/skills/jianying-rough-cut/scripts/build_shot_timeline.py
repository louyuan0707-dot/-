#!/usr/bin/env python3
"""Build a template-free shot-level timeline with clean 1x dialogue replacement."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from audit_dialogue_boundaries import boundary_errors
from subtitle_rules import normalize_cues

EPSILON = 0.003
PUNCTUATION_RE = re.compile(r"[.!?。！？…]$")


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


def asset_offsets(assets: list[dict]) -> tuple[list[tuple[float, float, dict]], float]:
    result: list[tuple[float, float, dict]] = []
    cursor = 0.0
    for asset in assets:
        end = cursor + float(asset["duration"])
        result.append((cursor, end, asset))
        cursor = end
    return result, cursor


def asset_for(offsets: list[tuple[float, float, dict]], position: float) -> tuple[float, dict]:
    for start, end, asset in offsets:
        if start - EPSILON <= position < end - EPSILON or abs(position - start) <= EPSILON:
            return start, asset
    start, _, asset = offsets[-1]
    return start, asset


def narration_boundaries(narration: dict, start: float, end: float) -> list[float]:
    values: set[float] = set()
    all_words: list[dict] = []
    for segment in narration.get("segments", []):
        segment_end = float(segment["end"])
        if start + 0.12 < segment_end < end - 0.12:
            values.add(segment_end)
        all_words.extend(segment.get("words", []))
    all_words.sort(key=lambda item: float(item["start"]))
    for index, word in enumerate(all_words):
        word_end = float(word["end"])
        pause = (
            float(all_words[index + 1]["start"]) - word_end
            if index + 1 < len(all_words) else 0.0
        )
        if start + 0.12 < word_end < end - 0.12 and (
            pause >= 0.12 or PUNCTUATION_RE.search(str(word.get("word", "")).strip())
        ):
            values.add(word_end)
    return sorted(values)


def shot_fragments(shots: list[dict], start: float, end: float) -> list[dict]:
    fragments: list[dict] = []
    for shot in shots:
        shot_start = float(shot["global_start"])
        shot_end = float(shot["global_end"])
        overlap_start = max(start, shot_start)
        overlap_end = min(end, shot_end)
        if overlap_end > overlap_start + EPSILON:
            fragments.append({
                "shot_id": shot["id"], "source_start": overlap_start,
                "source_end": overlap_end, "available": overlap_end - overlap_start,
            })
    return fragments


def allocate_with_bounds(weights: list[float], total: float,
                         lower: list[float], upper: list[float]) -> list[float]:
    if total < sum(lower) - EPSILON or total > sum(upper) + EPSILON:
        raise ValueError("target duration is outside the feasible speed/duration range")
    values = list(lower)
    remaining = total - sum(values)
    active = {index for index in range(len(values)) if upper[index] - values[index] > EPSILON}
    while remaining > EPSILON and active:
        weight_total = sum(max(weights[index], EPSILON) for index in active)
        consumed = 0.0
        for index in list(active):
            share = remaining * max(weights[index], EPSILON) / weight_total
            addition = min(share, upper[index] - values[index])
            values[index] += addition
            consumed += addition
            if upper[index] - values[index] <= EPSILON:
                active.discard(index)
        if consumed <= EPSILON:
            break
        remaining -= consumed
    if values:
        values[-1] += total - sum(values)
    return values


def snap_to_voice_boundaries(durations: list[float], lower: list[float], upper: list[float],
                             boundaries: list[float], window: float) -> list[float]:
    if len(durations) < 2 or not boundaries:
        return durations
    total = sum(durations)
    base_cuts: list[float] = []
    cursor = 0.0
    for duration in durations[:-1]:
        cursor += duration
        base_cuts.append(cursor)
    cuts: list[float] = []
    previous = 0.0
    unused = list(boundaries)
    for index, base in enumerate(base_cuts):
        suffix_lower = sum(lower[index + 1:])
        suffix_upper = sum(upper[index + 1:])
        low = max(previous + lower[index], total - suffix_upper)
        high = min(previous + upper[index], total - suffix_lower)
        candidates = [value for value in unused if low - EPSILON <= value <= high + EPSILON]
        nearest = min(candidates, key=lambda value: abs(value - base)) if candidates else None
        chosen = nearest if nearest is not None and abs(nearest - base) <= window else min(high, max(low, base))
        cuts.append(chosen)
        previous = chosen
        if nearest is not None and chosen == nearest:
            unused.remove(nearest)
    points = [0.0, *cuts, total]
    return [points[index + 1] - points[index] for index in range(len(points) - 1)]


def build_voice_cues(narration: dict, regions: list[dict], language: str) -> list[dict]:
    cues: list[dict] = []
    separator = "" if language.casefold().split("-", 1)[0] in {"zh", "ja", "th"} else " "
    for region in regions:
        source_start = float(region["source_start"])
        source_end = float(region["source_end"])
        timeline_start = float(region["timeline_start"])
        for segment in narration.get("segments", []):
            overlap_start = max(source_start, float(segment["start"]))
            overlap_end = min(source_end, float(segment["end"]))
            if overlap_end <= overlap_start + EPSILON:
                continue
            words = [word for word in segment.get("words", [])
                     if overlap_start - EPSILON <= (float(word["start"]) + float(word["end"])) / 2 <= overlap_end + EPSILON]
            text = separator.join(str(word.get("word", "")).strip() for word in words).strip()
            if not text and overlap_start <= float(segment["start"]) + EPSILON and overlap_end >= float(segment["end"]) - EPSILON:
                text = str(segment.get("text", "")).strip()
            if text:
                cue_start = float(words[0]["start"]) if words else overlap_start
                cue_end = float(words[-1]["end"]) if words else overlap_end
                cues.append({"kind": "narration", "start": timeline_start + cue_start - source_start,
                             "end": timeline_start + cue_end - source_start, "text": text})
    return cues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--dialogues", type=Path, required=True)
    parser.add_argument("--shot-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--beat-map", type=Path)
    parser.add_argument("--subtitle-language")
    parser.add_argument("--min-speed", type=float, default=0.45)
    parser.add_argument("--max-speed", type=float, default=2.60)
    parser.add_argument("--max-shot-duration", type=float, default=6.0)
    parser.add_argument("--boundary-snap-window", type=float, default=0.25)
    parser.add_argument("--max-reuse-duration", type=float, default=3.0)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"Refusing to overwrite non-empty output directory: {args.output}")
    if not 0.25 <= args.min_speed <= 1.0:
        parser.error("--min-speed must be between 0.25 and 1.0")
    if not 1.0 <= args.max_speed <= 4.0 or args.max_speed <= args.min_speed:
        parser.error("--max-speed must be greater than min speed and no more than 4.0")
    if args.max_shot_duration < 1.0:
        parser.error("--max-shot-duration must be at least 1 second")
    if not 0.5 <= args.max_reuse_duration <= args.max_shot_duration:
        parser.error("--max-reuse-duration must be between 0.5 and max shot duration")

    assets = load_json(args.assets).get("assets", [])
    narration = load_json(args.narration)
    dialogue_data = load_json(args.dialogues)
    shot_data = load_json(args.shot_map)
    shots = shot_data.get("shots", [])
    if not assets or not shots:
        parser.error("Assets and shot map must both contain media")
    if shot_data.get("mode") != "ordered_source_hard_cuts":
        parser.error("Shot map mode must be ordered_source_hard_cuts")
    offsets, source_duration = asset_offsets(assets)
    voice_duration = float(narration.get("duration") or max(float(item["end"]) for item in narration["segments"]))
    language = args.subtitle_language or str(narration.get("language", ""))
    if not language:
        parser.error("Subtitle language is missing")

    anchors = sorted(dialogue_data.get("anchors", []), key=lambda item: float(item["target_start"]))
    if not anchors:
        parser.error("Dialogue map has no anchors")
    dialogue_errors: list[str] = []
    for anchor in anchors:
        if anchor.get("status") != "READY":
            dialogue_errors.append(f"{anchor.get('id', 'unknown')}: status is not READY")
        dialogue_errors.extend(boundary_errors(anchor))
    if dialogue_errors:
        parser.error("Dialogue boundaries are not clean: " + "; ".join(dialogue_errors))

    dialogue_ranges = [(float(item["source_start"]), float(item["source_end"])) for item in anchors]
    reuse_pool: list[dict] = []
    for shot in shots:
        pieces = [(float(shot["global_start"]), float(shot["global_end"]))]
        for dialogue_start, dialogue_end in dialogue_ranges:
            next_pieces = []
            for piece_start, piece_end in pieces:
                if dialogue_end <= piece_start + EPSILON or dialogue_start >= piece_end - EPSILON:
                    next_pieces.append((piece_start, piece_end))
                    continue
                if dialogue_start - piece_start >= 0.5:
                    next_pieces.append((piece_start, dialogue_start))
                if piece_end - dialogue_end >= 0.5:
                    next_pieces.append((dialogue_end, piece_end))
            pieces = next_pieces
        for piece_start, piece_end in pieces:
            if piece_end - piece_start >= 0.5:
                reuse_pool.append({
                    "shot_id": shot["id"], "source_start": piece_start, "source_end": piece_end,
                    "available": piece_end - piece_start,
                    "motion_score": float(shot.get("motion_score", 0.0)),
                })
    if not reuse_pool:
        reuse_pool = [{"shot_id": shot["id"], "source_start": float(shot["global_start"]),
                       "source_end": float(shot["global_end"]),
                       "available": float(shot["global_end"]) - float(shot["global_start"]),
                       "motion_score": float(shot.get("motion_score", 0.0))}
                      for shot in shots if float(shot["global_end"]) - float(shot["global_start"]) >= 0.25]
    reuse_pool.sort(key=lambda item: (-float(item["motion_score"]), str(item["shot_id"])))
    if not reuse_pool:
        parser.error("Source videos contain no usable moving frames")

    semantic_beats = []
    if args.beat_map:
        beat_data = load_json(args.beat_map)
        if beat_data.get("mode") != "semantic_narration_video_sync":
            parser.error("Beat map mode must be semantic_narration_video_sync")
        semantic_beats = sorted(beat_data.get("anchors", []), key=lambda item: float(item["voice_time"]))
        if any(item.get("status") != "READY" for item in semantic_beats):
            parser.error("All semantic beats must be READY")

    segments: list[dict] = []
    voice_regions: list[dict] = []
    output_anchors: list[dict] = []
    final_cursor = 0.0
    source_cursor = 0.0
    voice_cursor = 0.0
    trimmed_source = 0.0
    used_beat_ids: set[str] = set()
    reuse_cursor = 0
    reused_coverage = 0.0
    reuse_counts: dict[str, int] = {}

    def add_segment(kind: str, source_start: float, source_end: float,
                    target_start: float, target_end: float, **extra: object) -> None:
        reuses_source = bool(extra.pop("reuses_source", False))
        asset_offset, asset = asset_for(offsets, source_start)
        speed = (source_end - source_start) / (target_end - target_start)
        segments.append({
            "id": f"segment-{len(segments) + 1:03d}", "kind": kind, "status": "READY",
            "video_file": asset["path"], "source_asset_index": int(asset.get("source_index", 0)),
            "source_start": round(source_start - asset_offset, 6),
            "source_end": round(source_end - asset_offset, 6),
            "source_global_start": round(source_start, 6), "source_global_end": round(source_end, 6),
            "timeline_start": round(target_start, 6), "timeline_end": round(target_end, 6),
            "speed": round(speed, 6), "reuses_source": reuses_source, **extra,
        })

    def append_dynamic_coverage(duration: float, beat_id: str | None) -> None:
        """Fill narration with short moving cutaways; never freeze or create silence."""
        nonlocal final_cursor, reuse_cursor, reused_coverage
        if duration <= EPSILON:
            return
        maximum = min(args.max_reuse_duration, max(float(item["available"]) for item in reuse_pool))
        piece_count = max(1, math.ceil(duration / maximum))
        piece_duration = duration / piece_count
        for _ in range(piece_count):
            candidates = [item for item in reuse_pool if float(item["available"]) >= piece_duration - EPSILON]
            if not candidates:
                candidates = reuse_pool
            candidate = candidates[reuse_cursor % len(candidates)]
            reuse_cursor += 1
            available = float(candidate["available"])
            source_length = min(available, piece_duration)
            target_length = piece_duration
            speed = source_length / target_length
            if speed < args.min_speed:
                source_length = min(available, target_length * args.min_speed)
                speed = source_length / target_length
            key = str(candidate["shot_id"])
            count = reuse_counts.get(key, 0)
            travel = max(0.0, available - source_length)
            offset = (count * source_length * 0.67) % travel if travel > EPSILON else 0.0
            reuse_counts[key] = count + 1
            source_a = float(candidate["source_start"]) + offset
            source_b = source_a + source_length
            add_segment(
                "coverage_reuse", source_a, source_b, final_cursor, final_cursor + target_length,
                shot_id=candidate["shot_id"], beat_id=beat_id, has_voice=True,
                source_trimmed=0.0, reuses_source=True, moving_reuse=True,
                motion_score=round(float(candidate["motion_score"]), 6),
            )
            final_cursor += target_length
            reused_coverage += target_length

    def plan_gap_piece(source_start: float, source_end: float, voice_start: float,
                       voice_end: float, beat_id: str | None) -> None:
        nonlocal final_cursor, trimmed_source
        voice_length = voice_end - voice_start
        available = source_end - source_start
        if voice_length <= EPSILON:
            trimmed_source += max(0.0, available)
            return
        fragments = shot_fragments(shots, source_start, source_end)
        region_start = final_cursor
        usable_fragments = [item for item in fragments if float(item["available"]) >= 0.12]
        trimmed_source += sum(float(item["available"]) for item in fragments if float(item["available"]) < 0.12)
        fragments = usable_fragments
        if not fragments:
            append_dynamic_coverage(voice_length, beat_id)
            voice_regions.append({
                "id": f"voice-{len(voice_regions) + 1:03d}",
                **({"beat_id": beat_id} if beat_id else {}),
                "source_start": round(voice_start, 6), "source_end": round(voice_end, 6),
                "timeline_start": round(region_start, 6), "timeline_end": round(final_cursor, 6),
            })
            return
        available_lengths = [float(item["available"]) for item in fragments]
        max_targets = [min(length / args.min_speed, args.max_shot_duration) for length in available_lengths]
        original_target = min(voice_length, sum(max_targets))
        kept_lengths = list(available_lengths)
        min_targets = [length / args.max_speed for length in kept_lengths]
        if original_target < sum(min_targets) - EPSILON:
            scale = max(0.0, original_target * args.max_speed / sum(kept_lengths))
            kept_lengths = [length * scale for length in kept_lengths]
            trimmed_source += sum(available_lengths) - sum(kept_lengths)
            fragments = [item for item, length in zip(fragments, kept_lengths) if length >= 0.08]
            kept_lengths = [length for length in kept_lengths if length >= 0.08]
            if not fragments:
                append_dynamic_coverage(voice_length, beat_id)
                voice_regions.append({
                    "id": f"voice-{len(voice_regions) + 1:03d}",
                    **({"beat_id": beat_id} if beat_id else {}),
                    "source_start": round(voice_start, 6), "source_end": round(voice_end, 6),
                    "timeline_start": round(region_start, 6), "timeline_end": round(final_cursor, 6),
                })
                return
        while len(fragments) > 1:
            candidate_upper = [min(length / args.min_speed, args.max_shot_duration) for length in kept_lengths]
            candidate_lower = [min(upper, max(length / args.max_speed, 0.18))
                               for length, upper in zip(kept_lengths, candidate_upper)]
            if sum(candidate_lower) <= original_target + EPSILON:
                break
            drop_index = min(range(len(kept_lengths)), key=lambda index: kept_lengths[index])
            trimmed_source += float(fragments[drop_index]["available"])
            fragments.pop(drop_index)
            kept_lengths.pop(drop_index)
        upper = [min(length / args.min_speed, args.max_shot_duration) for length in kept_lengths]
        lower = [min(upper_value, max(length / args.max_speed, 0.18))
                 for length, upper_value in zip(kept_lengths, upper)]
        original_target = min(original_target, sum(upper))
        reuse_needed = voice_length - original_target
        if EPSILON < reuse_needed < 0.18 and voice_length - 0.18 >= sum(lower) - EPSILON:
            original_target = voice_length - 0.18
        durations = allocate_with_bounds(kept_lengths, original_target, lower, upper)
        relative_boundaries = [value - voice_start for value in narration_boundaries(narration, voice_start, voice_end)]
        durations = snap_to_voice_boundaries(
            durations, lower, upper, relative_boundaries, args.boundary_snap_window
        )
        for fragment, kept, target_duration in zip(fragments, kept_lengths, durations):
            source_a = float(fragment["source_start"])
            source_b = source_a + kept
            add_segment(
                "gap", source_a, source_b, final_cursor, final_cursor + target_duration,
                shot_id=fragment["shot_id"], beat_id=beat_id, has_voice=True,
                source_trimmed=round(float(fragment["available"]) - kept, 6),
            )
            final_cursor += target_duration
        append_dynamic_coverage(voice_length - original_target, beat_id)
        voice_regions.append({
            "id": f"voice-{len(voice_regions) + 1:03d}",
            **({"beat_id": beat_id} if beat_id else {}),
            "source_start": round(voice_start, 6), "source_end": round(voice_end, 6),
            "timeline_start": round(region_start, 6), "timeline_end": round(final_cursor, 6),
        })

    def plan_gap(source_start: float, source_end: float, voice_start: float, voice_end: float) -> None:
        internal = [beat for beat in semantic_beats
                    if source_start + EPSILON < float(beat["source_time"]) < source_end - EPSILON
                    and voice_start + EPSILON < float(beat["voice_time"]) < voice_end - EPSILON]
        points = [(source_start, voice_start, None)]
        points.extend((float(beat["source_time"]), float(beat["voice_time"]), str(beat.get("id") or "unknown"))
                      for beat in internal)
        points.append((source_end, voice_end, None))
        for index in range(len(points) - 1):
            source_a, voice_a, beat_id = points[index]
            source_b, voice_b, _ = points[index + 1]
            plan_gap_piece(source_a, source_b, voice_a, voice_b, beat_id)
            if beat_id:
                used_beat_ids.add(beat_id)

    try:
        for anchor in anchors:
            source_start, source_end = float(anchor["source_start"]), float(anchor["source_end"])
            target_start, target_end = float(anchor["target_start"]), float(anchor["target_end"])
            if source_start < source_cursor - EPSILON or target_start < voice_cursor - EPSILON:
                parser.error(f"Dialogue anchors overlap or are not monotonic at {anchor.get('id')}")
            plan_gap(source_cursor, source_start, voice_cursor, target_start)
            dialogue_start = final_cursor
            duration = source_end - source_start
            if duration <= EPSILON:
                parser.error(f"Dialogue anchor has no duration: {anchor.get('id')}")
            add_segment("dialogue", source_start, source_end, dialogue_start, dialogue_start + duration,
                        anchor_id=anchor.get("id"), shot_id=None, has_voice=False,
                        source_trimmed=0.0, text=anchor.get("target_text", ""))
            final_cursor += duration
            output_anchors.append({**anchor, "narration_source_start": round(target_start, 6),
                                   "narration_source_end": round(target_end, 6),
                                   "timeline_start": round(dialogue_start, 6),
                                   "timeline_end": round(final_cursor, 6), "speed": 1.0,
                                   "narration_action": "DELETE"})
            source_cursor, voice_cursor = source_end, target_end
        plan_gap(source_cursor, source_duration, voice_cursor, voice_duration)
    except ValueError as error:
        parser.error(str(error))

    missing_beats = {str(item.get("id") or "unknown") for item in semantic_beats} - used_beat_ids
    if any(item.get("kind") == "hold" or (item.get("reuses_source") and not item.get("moving_reuse"))
           for item in segments):
        parser.error("Timeline contains a forbidden freeze/static hold segment")
    for item in segments:
        speed = float(item["speed"])
        duration = float(item["timeline_end"]) - float(item["timeline_start"])
        if item["kind"] == "dialogue" and abs(speed - 1.0) > EPSILON:
            parser.error(f"Dialogue is not 1x at {item['id']}")
        if item["kind"] != "dialogue" and not args.min_speed - EPSILON <= speed <= args.max_speed + EPSILON:
            parser.error(f"Non-dialogue speed is outside policy at {item['id']}: {speed:.4f}x")
        if item["kind"] != "dialogue" and duration > args.max_shot_duration + EPSILON:
            parser.error(f"Non-dialogue shot is too long at {item['id']}: {duration:.3f}s")

    cues = build_voice_cues(narration, voice_regions, language)
    cues.extend({"kind": "dialogue", "start": float(anchor["timeline_start"]),
                 "end": float(anchor["timeline_end"]),
                 "text": str(anchor.get("target_text") or anchor.get("source_text") or "").strip()}
                for anchor in output_anchors)
    cues, subtitle_profile = normalize_cues(cues, language)
    args.output.mkdir(parents=True, exist_ok=True)
    deleted_duration = sum(float(item["target_end"]) - float(item["target_start"]) for item in anchors)
    document = {
        "version": 4, "mode": "shot_level_dialogue_replacement",
        "source_assets": str(args.assets.resolve()), "source_narration": str(args.narration.resolve()),
        "source_dialogues": str(args.dialogues.resolve()), "source_shot_map": str(args.shot_map.resolve()),
        "voice_source_duration": round(voice_duration, 6), "source_video_duration": round(source_duration, 6),
        "final_duration": round(final_cursor, 6), "canvas": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "preservation": {"full_video": False, "source_order_preserved": True,
                         "dialogue_video_speed_one": True, "dialogue_boundaries_reviewed": True,
                         "voice_dialogue_deleted": True, "remaining_voice_ranges_preserved": True,
                         "no_hold_segments": True, "ordinary_non_dialogue_trim_allowed": True,
                         "continuous_picture": True, "moving_coverage_reuse_allowed": True},
        "editing_policy": {"template_required": False, "dialogue_speed": 1.0,
                           "min_non_dialogue_speed": args.min_speed,
                           "max_non_dialogue_speed": args.max_speed,
                           "max_non_dialogue_shot_duration": args.max_shot_duration,
                            "boundary_snap_window": args.boundary_snap_window,
                            "freeze_allowed": False, "moving_non_dialogue_reuse_allowed": True,
                            "max_reuse_segment_duration": args.max_reuse_duration,
                            "priority": ["continuous_narration", "continuous_moving_picture", "clean_dialogue",
                                         "rough_semantic_alignment"],
                            "card_point_fallback": ["trim_idle_non_dialogue", "retime_within_bounds",
                                                    "redistribute_neighboring_shots", "reuse_short_moving_coverage"]},
        "audio_policy": {"gap": {"voice_present": True, "video_volume": 0.0},
                         "dialogue": {"voice_present": False, "video_volume": 1.0, "video_speed": 1.0},
                         "deleted_voice_duration": round(deleted_duration, 6)},
        "subtitle_profile": {"language": language, "profile": subtitle_profile["key"],
                             "font_name": subtitle_profile["font_name"], "size": subtitle_profile["size"],
                             "line_spacing": subtitle_profile["line_spacing"], "letter_spacing": 0,
                             "color": "#FFFFFF", "outline_color": "#000000", "outline_width": 8,
                             "max_line_width": 0.70, "transform_y": -0.833333, "y_pixels": -800,
                             "max_lines": 2, "horizontal_alignment": "center", "vertical_alignment": "top"},
        "semantic_alignment": {"beat_map": str(args.beat_map.resolve()) if args.beat_map else None,
                               "reviewed": bool(semantic_beats), "anchor_count": len(semantic_beats),
                               "anchors": semantic_beats},
        "summary": {"source_shot_count": len(shots), "timeline_video_segment_count": len(segments),
                    "trimmed_non_dialogue_source_duration": round(trimmed_source, 6),
                    "reused_moving_coverage_duration": round(reused_coverage, 6),
                    "unused_semantic_beat_ids": sorted(missing_beats)},
        "voice_regions": voice_regions, "dialogue_anchors": output_anchors,
        "segments": segments, "subtitle_cues": cues,
    }
    (args.output / "timeline.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output / "subtitle.srt").open("w", encoding="utf-8") as handle:
        for index, cue in enumerate(cues, start=1):
            handle.write(f"{index}\n{srt_time(float(cue['start']))} --> {srt_time(float(cue['end']))}\n{cue['text']}\n\n")
    speeds = [float(item["speed"]) for item in segments if item["kind"] != "dialogue"]
    report = ["# Shot-level edit report", "", "Template-free shot timing audit: PASS",
              f"Detected source shots: {len(shots)}.", f"Final timeline: {final_cursor:.3f}s.",
              f"Trimmed ordinary non-dialogue source: {trimmed_source:.3f}s.",
              f"Dialogue anchors: {len(output_anchors)} at exact 1.0000x with adjacent-word boundary evidence.",
              f"Non-dialogue speed range: {min(speeds):.4f}x-{max(speeds):.4f}x." if speeds else "No non-dialogue segments.",
              f"Maximum non-dialogue shot duration: {max((float(item['timeline_end']) - float(item['timeline_start']) for item in segments if item['kind'] != 'dialogue'), default=0.0):.3f}s.",
              f"Short moving coverage reuse: {reused_coverage:.3f}s.",
              "Picture/narration gaps: 0. Freeze/static holds: 0."]
    (args.output / "edit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Created template-free v4 timeline: {args.output}; {len(segments)} video segments, no holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
