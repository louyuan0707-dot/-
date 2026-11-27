#!/usr/bin/env python3
"""Create a vertical Jianying draft from an approved dialogue timeline."""
from __future__ import annotations

import argparse
import json
import unicodedata
import uuid
from pathlib import Path

from subtitle_rules import (
    MAX_SUBTITLE_LINES,
    display_units,
    profile_for_language,
    resolve_font_path,
)

EPSILON = 0.002
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
CANVAS_RATIO = "9:16"
SUBTITLE_Y = -400 / (CANVAS_HEIGHT / 2)
SUBTITLE_Y_PIXELS = -400


def microseconds(seconds: float) -> int:
    return round(float(seconds) * 1_000_000)


def timerange(draft, start: float, end: float):
    return draft.Timerange(microseconds(start), microseconds(end - start))


def validate_timeline(data: dict, parser: argparse.ArgumentParser) -> None:
    version = data.get("version")
    mode = data.get("mode")
    if (version, mode) not in {
        (3, "dialogue_replacement_ripple"),
        (4, "shot_level_dialogue_replacement"),
        (5, "full_source_retime_only"),
    }:
        parser.error("This builder requires an approved v3, v4, or v5 full-source timeline")
    canvas = data.get("canvas", {})
    if (canvas.get("width"), canvas.get("height")) != (CANVAS_WIDTH, CANVAS_HEIGHT):
        parser.error("Timeline canvas must be fixed to 1080x1920 before draft creation")
    required = {"source_order_preserved", "dialogue_video_speed_one",
                "voice_dialogue_deleted", "remaining_voice_ranges_preserved"}
    if version == 3:
        required.add("full_video")
    elif version == 4:
        required.update({"dialogue_boundaries_reviewed", "no_hold_segments"})
    else:
        required.update({
            "full_video", "dialogue_boundaries_reviewed", "audio_roles_reviewed",
            "no_source_trim", "no_source_reuse", "no_hold_segments", "natural_moving_tail",
        })
    preservation = data.get("preservation", {})
    missing = sorted(key for key in required if preservation.get(key) is not True)
    if missing:
        parser.error("Timeline does not declare required invariants: " + ", ".join(missing))
    policy = data.get("editing_policy", {})
    min_speed = float(policy.get("min_non_dialogue_speed", 0.0))
    max_speed = float(policy.get("max_non_dialogue_speed", 1.2))
    max_shot_duration = float(policy.get("max_non_dialogue_shot_duration", float("inf")))
    for item in data.get("segments", []):
        speed = float(item.get("speed", 0.0))
        duration = float(item.get("timeline_end", 0.0)) - float(item.get("timeline_start", 0.0))
        if item.get("kind") == "hold" or (
            item.get("reuses_source") and not item.get("moving_reuse")
        ):
            parser.error(f"Freeze/static hold segment is forbidden: {item.get('id', 'unknown')}")
        if version == 5 and item.get("reuses_source"):
            parser.error(f"Full-source v5 timeline cannot reuse footage: {item.get('id', 'unknown')}")
        if item.get("kind") == "dialogue" and abs(speed - 1.0) > EPSILON:
            parser.error(f"Dialogue segment is not exactly 1x: {item.get('id', 'unknown')}")
        if item.get("kind") != "dialogue" and not min_speed - EPSILON <= speed <= max_speed + EPSILON:
            parser.error(f"Non-dialogue speed is outside timeline policy: {item.get('id', 'unknown')}={speed:.6f}x")
        if item.get("kind") != "dialogue" and duration > max_shot_duration + EPSILON:
            parser.error(f"Non-dialogue shot exceeds duration policy: {item.get('id', 'unknown')}={duration:.6f}s")


def read_srt_texts(path: Path) -> list[str]:
    texts: list[str] = []
    for raw_block in path.read_text(encoding="utf-8-sig").replace("\r", "").split("\n\n"):
        lines = [line for line in raw_block.split("\n") if line.strip()]
        if not lines:
            continue
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            raise ValueError("SRT block is missing a timing line")
        text = "\n".join(lines[timing_index + 1:]).strip()
        if not text:
            raise ValueError("SRT block is missing subtitle text")
        texts.append(text)
    return texts


def validate_subtitle_texts(texts: list[str], profile: dict) -> None:
    if not texts:
        raise ValueError("Subtitle file contains no cues")
    max_units = int(profile["max_line_units"])
    for cue_index, text in enumerate(texts, start=1):
        if profile.get("no_punctuation") and any(
            unicodedata.category(char).startswith("P") for char in text
        ):
            raise ValueError(f"Subtitle cue {cue_index} contains punctuation")
        lines = text.splitlines()
        if len(lines) > MAX_SUBTITLE_LINES:
            raise ValueError(
                f"Subtitle cue {cue_index} has {len(lines)} lines; maximum is {MAX_SUBTITLE_LINES}"
            )
        for line in lines:
            units = display_units(line.strip())
            if not line.strip() or units > max_units:
                raise ValueError(
                    f"Subtitle cue {cue_index} is not safe for the 70% box: "
                    f"line units {units}, maximum {max_units}"
                )


def patch_exported_subtitles(content_path: Path, profile: dict, font_path: Path,
                             outline_width: float, transform_y: float) -> None:
    """Enforce fields that pyJianYingDraft 0.3 cannot express exactly."""
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["canvas_config"] = {
        "width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "ratio": CANVAS_RATIO,
    }
    text_materials = content.get("materials", {}).get("texts", [])
    for material in text_materials:
        raw = material.get("content", "{}")
        material_content = json.loads(raw) if isinstance(raw, str) else dict(raw)
        for style in material_content.get("styles", []):
            style["size"] = float(profile["size"])
            style["bold"] = False
            style["font"] = {"id": profile["font_name"], "path": str(font_path)}
            style["fill"] = {
                "alpha": 1.0,
                "content": {
                    "render_type": "solid",
                    "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]},
                },
            }
            style["strokes"] = [{
                "content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                "width": outline_width / 100.0 * 0.2,
            }]
            style["shadows"] = [{
                "diffuse": 10.0 / 100.0 / 6.0,
                "alpha": 0.10,
                "distance": 5.0,
                "content": {"solid": {"color": [0.0, 0.0, 0.0]}},
                "angle": -45.0,
            }]
        material["content"] = json.dumps(material_content, ensure_ascii=False)
        material["alignment"] = 1
        material["letter_spacing"] = 0.0
        material["line_spacing"] = 0.02 + float(profile["line_spacing"]) * 0.05
        material["line_max_width"] = 0.70
        material["force_apply_line_max_width"] = True
        material["type"] = "subtitle"
        material["font_name"] = profile["font_name"]
        material["font_size"] = float(profile["size"])
        material["font_path"] = str(font_path)
        material["text_color"] = "#FFFFFF"
        material["border_color"] = "#000000"
        material["border_width"] = float(outline_width)
        material["text_alignment"] = "center"
        material["vertical_alignment"] = "top"
        material["global_alpha"] = 1.0

    for track in content.get("tracks", []):
        if track.get("type") != "text":
            continue
        for segment in track.get("segments", []):
            clip = segment.setdefault("clip", {})
            clip["transform"] = {"x": 0.0, "y": float(transform_y)}
            clip["alpha"] = 1.0
            clip["rotation"] = 0.0
            segment["uniform_scale"] = {"on": True, "value": 1.0}
            segment["subtitle_vertical_alignment"] = "top"
    content_path.write_text(json.dumps(content, ensure_ascii=False, indent=4), encoding="utf-8")


def validate_exported_draft(content_path: Path, profile: dict, expected_texts: list[str],
                            outline_width: float) -> None:
    content = json.loads(content_path.read_text(encoding="utf-8"))
    if content.get("canvas_config") != {
        "width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "ratio": CANVAS_RATIO,
    }:
        raise ValueError("Exported draft canvas is not exactly 1080x1920 9:16")

    text_materials = content.get("materials", {}).get("texts", [])
    if len(text_materials) != len(expected_texts):
        raise ValueError(
            f"Exported draft has {len(text_materials)} text materials but "
            f"the validated SRT has {len(expected_texts)} cues"
        )
    exported_texts: list[str] = []
    for index, material in enumerate(text_materials, start=1):
        material_content = json.loads(material.get("content", "{}"))
        exported_text = str(material_content.get("text", ""))
        exported_texts.append(exported_text)
        validate_subtitle_texts([exported_text], profile)
        styles = material_content.get("styles", [])
        if not styles:
            raise ValueError(f"Subtitle material {index} has no style")
        style = styles[0]
        if style.get("font", {}).get("id") != profile["font_name"]:
            raise ValueError(f"Subtitle material {index} has the wrong font")
        if abs(float(style.get("size", -1)) - float(profile["size"])) > EPSILON:
            raise ValueError(f"Subtitle material {index} has the wrong size")
        if abs(float(material.get("border_width", -1)) - outline_width) > EPSILON:
            raise ValueError(f"Subtitle material {index} has the wrong outline width")
        if material.get("vertical_alignment") != "top":
            raise ValueError(f"Subtitle material {index} is not top aligned")
        if abs(float(material.get("line_max_width", -1)) - 0.70) > EPSILON:
            raise ValueError(f"Subtitle material {index} does not use the 70% safe width")
    if exported_texts != expected_texts:
        raise ValueError("Exported subtitle text differs from the validated SRT")

    text_tracks = [track for track in content.get("tracks", []) if track.get("type") == "text"]
    text_segments = [segment for track in text_tracks for segment in track.get("segments", [])]
    if len(text_segments) != len(expected_texts):
        raise ValueError("Exported draft subtitle segment count differs from the validated SRT")
    for index, segment in enumerate(text_segments, start=1):
        transform_y = float(segment.get("clip", {}).get("transform", {}).get("y", 999))
        y_pixels = transform_y * (CANVAS_HEIGHT / 2)
        if abs(y_pixels - SUBTITLE_Y_PIXELS) > 0.01:
            raise ValueError(
                f"Subtitle segment {index} is at Y={y_pixels:.3f}, required Y={SUBTITLE_Y_PIXELS}"
            )
        if segment.get("subtitle_vertical_alignment") != "top":
            raise ValueError(f"Subtitle segment {index} lost top alignment")


def update_native_meta(draft_dir: Path, draft_name: str, duration_us: int) -> None:
    meta_path = draft_dir / "draft_meta_info.json"
    if not meta_path.is_file():
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["draft_id"] = str(uuid.uuid4()).upper()
    meta["draft_name"] = draft_name
    meta["draft_fold_path"] = str(draft_dir.resolve())
    meta["draft_root_path"] = str(draft_dir.resolve())
    meta["tm_duration"] = int(duration_us)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=4), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--subtitles", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--draft-dir", type=Path, help="New, empty draft folder")
    destination.add_argument("--draft-root", type=Path, help="Jianying draft root for native registration")
    parser.add_argument("--draft-name", help="Required with --draft-root")
    parser.add_argument("--subtitle-language", help="Defaults to timeline subtitle language")
    parser.add_argument("--font-file", type=Path, help="Exact requested font file override")
    parser.add_argument("--outline-width", type=float, default=15.0, help="Black outline; personal default 15")
    parser.add_argument("--music", type=Path, help="User-supplied music, prepared to cover the final timeline")
    parser.add_argument("--music-gain-db", type=float, default=-6.0)
    parser.add_argument("--sfx-manifest", type=Path, help="JSON list of optional additive sound effects")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--voice-volume", type=float, default=1.0)
    parser.add_argument("--gap-video-volume", type=float, help="Default: 0 for legacy v3, 1 for v4/v5 unless a segment specifies its volume")
    parser.add_argument("--dialogue-video-volume", type=float, default=1.0)
    parser.add_argument("--maintrack-adsorb", action="store_true")
    args = parser.parse_args()

    for path, label in ((args.timeline, "timeline"), (args.voice, "voice"), (args.subtitles, "subtitles")):
        if not path.is_file():
            parser.error(f"--{label} does not exist: {path}")
    if not 0.0 <= args.outline_width <= 100.0:
        parser.error("--outline-width must be between 0 and 100")
    if min(x for x in (args.voice_volume, args.gap_video_volume, args.dialogue_video_volume) if x is not None) < 0:
        parser.error("Volume multipliers cannot be negative")
    if args.draft_root is not None and not args.draft_name:
        parser.error("--draft-name is required with --draft-root")
    if args.draft_dir is not None and args.draft_name:
        parser.error("--draft-name is only valid with --draft-root")
    if args.music is not None and not args.music.is_file():
        parser.error(f"Music does not exist: {args.music}")
    if args.sfx_manifest is not None and not args.sfx_manifest.is_file():
        parser.error(f"SFX manifest does not exist: {args.sfx_manifest}")

    try:
        import pyJianYingDraft as draft
    except ImportError as error:
        parser.error(f"Missing pyJianYingDraft. Install requirements first. ({error})")

    data = json.loads(args.timeline.read_text(encoding="utf-8"))
    validate_timeline(data, parser)
    segments = data.get("segments", [])
    voice_regions = data.get("voice_regions", [])
    if not segments:
        parser.error("Timeline has no video segments")
    if not voice_regions:
        parser.error("Timeline has no retained narration regions")
    blocked = [item.get("id", "unknown") for item in segments if item.get("status") != "READY"]
    if blocked:
        parser.error("Timeline has unresolved segments: " + ", ".join(blocked))

    language = args.subtitle_language or data.get("subtitle_profile", {}).get("language")
    try:
        profile = profile_for_language(str(language or ""))
        font_path = resolve_font_path(profile, args.font_file)
        subtitle_texts = read_srt_texts(args.subtitles)
        validate_subtitle_texts(subtitle_texts, profile)
    except (ValueError, FileNotFoundError) as error:
        parser.error(str(error))

    voice_material = draft.AudioMaterial(str(args.voice.resolve()))
    voice_duration = voice_material.duration / 1_000_000
    expected_voice = float(data["voice_source_duration"])
    if abs(voice_duration - expected_voice) > 0.1:
        parser.error(
            f"Voice material is {voice_duration:.3f}s but timeline expects {expected_voice:.3f}s; "
            "rebuild the timeline from this exact voice file"
        )

    if args.draft_root is not None:
        if not args.draft_root.is_dir():
            parser.error(f"Draft root does not exist: {args.draft_root}")
        try:
            script = draft.DraftFolder(str(args.draft_root.resolve())).create_draft(
                args.draft_name, CANVAS_WIDTH, CANVAS_HEIGHT, args.fps,
                maintrack_adsorb=args.maintrack_adsorb, allow_replace=False,
            )
        except (FileExistsError, FileNotFoundError) as error:
            parser.error(str(error))
        draft_dir = args.draft_root / args.draft_name
    else:
        draft_dir = args.draft_dir
        if draft_dir.exists() and any(draft_dir.iterdir()):
            parser.error(f"Refusing to overwrite non-empty draft directory: {draft_dir}")
        draft_dir.mkdir(parents=True, exist_ok=True)
        script = draft.ScriptFile(CANVAS_WIDTH, CANVAS_HEIGHT, args.fps, args.maintrack_adsorb)

    script.append_track(draft.TrackSpec(draft.TrackType.video, "ordered footage"))
    script.append_track(draft.TrackSpec(draft.TrackType.audio, "retained narration"))
    if args.music is not None:
        music_material = draft.AudioMaterial(str(args.music.resolve()))
        music_seconds = music_material.duration / 1_000_000
        target_seconds = float(data.get("timeline_duration", data.get("final_duration", 0.0)))
        if music_seconds + 0.15 < target_seconds:
            parser.error(
                f"Music ends {target_seconds - music_seconds:.3f}s early; prepare a longer or gently retimed user track"
            )
        script.append_track(draft.TrackSpec(draft.TrackType.audio, "background music"))
        script.add_segment(
            draft.AudioSegment(
                music_material, timerange(draft, 0.0, min(music_seconds, target_seconds)),
                volume=10 ** (args.music_gain_db / 20), change_pitch=False,
            ),
            "background music",
        )
    if args.sfx_manifest is not None:
        effects = json.loads(args.sfx_manifest.read_text(encoding="utf-8"))
        if not isinstance(effects, list):
            parser.error("SFX manifest must be a JSON list")
        script.append_track(draft.TrackSpec(draft.TrackType.audio, "added sound effects"))
        for effect in effects:
            effect_path = Path(effect["path"])
            if not effect_path.is_file():
                parser.error(f"Sound effect does not exist: {effect_path}")
            start = float(effect["timeline_start"])
            length = float(effect["duration"])
            if start < 0 or length <= 0 or start + length > float(data.get("timeline_duration", data.get("final_duration", 0.0))) + EPSILON:
                parser.error(f"Sound effect is outside the timeline: {effect_path}")
            script.add_segment(
                draft.AudioSegment(
                    draft.AudioMaterial(str(effect_path.resolve())),
                    timerange(draft, start, start + length),
                    volume=float(effect.get("volume", 1.0)), change_pitch=False,
                ),
                "added sound effects",
            )
    for region in voice_regions:
        source_start, source_end = float(region["source_start"]), float(region["source_end"])
        target_start, target_end = float(region["timeline_start"]), float(region["timeline_end"])
        if abs((source_end - source_start) - (target_end - target_start)) > EPSILON:
            parser.error(f"Retained narration region is retimed: {region.get('id', 'unknown')}")
        script.add_segment(
            draft.AudioSegment(
                voice_material,
                timerange(draft, target_start, target_end),
                source_timerange=timerange(draft, source_start, source_end),
                volume=args.voice_volume,
                change_pitch=False,
            ),
            "retained narration",
        )

    for item in segments:
        source_start, source_end = float(item["source_start"]), float(item["source_end"])
        target_start, target_end = float(item["timeline_start"]), float(item["timeline_end"])
        if item.get("kind") == "dialogue" and (
            abs((source_end - source_start) - (target_end - target_start)) > EPSILON
            or abs(float(item.get("speed", 0.0)) - 1.0) > EPSILON
        ):
            parser.error(f"Dialogue segment must remain exactly 1x: {item.get('id', 'unknown')}")
        default_volume = (
            args.dialogue_video_volume if item.get("kind") == "dialogue"
            else args.gap_video_volume if args.gap_video_volume is not None
            else 0.0 if data.get("version") == 3 else 1.0
        )
        volume = float(item.get("source_audio_volume", default_volume))
        if volume < 0:
            parser.error(f"Segment has a negative source-audio volume: {item.get('id', 'unknown')}")
        script.add_segment(
            draft.VideoSegment(
                str(Path(item["video_file"]).resolve()),
                timerange(draft, target_start, target_end),
                source_timerange=timerange(draft, source_start, source_end),
                volume=volume,
                change_pitch=False,
            ),
            "ordered footage",
        )

    subtitle_style = draft.TextStyle(
        size=float(profile["size"]), color=(1.0, 1.0, 1.0), align=1,
        letter_spacing=0, line_spacing=int(profile["line_spacing"]),
        auto_wrapping=True, max_line_width=0.70,
    )
    subtitle_reference = draft.TextSegment(
        "subtitle", draft.Timerange(0, 1), style=subtitle_style,
        clip_settings=draft.ClipSettings(transform_x=0.0, transform_y=SUBTITLE_Y),
        border=draft.TextBorder(color=(0.0, 0.0, 0.0), width=args.outline_width),
        shadow=draft.TextShadow(alpha=0.10, color=(0.0, 0.0, 0.0), diffuse=10.0, distance=5.0, angle=-45.0),
    )
    script.import_srt(
        str(args.subtitles.resolve()), track_name="subtitles",
        style_reference=subtitle_reference, clip_settings=None,
    )

    content_path = draft_dir / "draft_content.json"
    if args.draft_root is not None:
        script.save()
        update_native_meta(draft_dir, args.draft_name, script.duration)
    else:
        script.dump(str(content_path))
    patch_exported_subtitles(content_path, profile, font_path, args.outline_width, SUBTITLE_Y)
    try:
        validate_exported_draft(content_path, profile, subtitle_texts, args.outline_width)
    except ValueError as error:
        parser.error(f"Exported draft validation failed: {error}")
    print(
        f"Created 1080x1920 ripple Jianying draft: {draft_dir} "
        f"({len(segments)} video segments; {len(voice_regions)} retained 1x narration regions; "
        "narration dialogue deleted; source dialogue fixed at 1x)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
