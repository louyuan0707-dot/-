# Full-source and shot-level dialogue-replacement timeline schemas

Version 5 applies when all ordered source frames are usable and only mild retiming is needed. It keeps all ordered source frames exactly once, assigns source volume per reviewed audio interval, preserves real spoken dialogue at `1x`, and retains natural moving room after narration. If that would require visibly slow picture or prevent use of a supplied repair clip, choose version 4.

Required v5 policy fields:

```json
{
  "version": 5,
  "mode": "full_source_retime_only",
  "preservation": {
    "full_video": true,
    "source_order_preserved": true,
    "dialogue_video_speed_one": true,
    "voice_dialogue_deleted": true,
    "remaining_voice_ranges_preserved": true,
    "dialogue_boundaries_reviewed": true,
    "audio_roles_reviewed": true,
    "no_source_trim": true,
    "no_source_reuse": true,
    "no_hold_segments": true,
    "natural_moving_tail": true
  },
  "editing_policy": {
    "min_non_dialogue_speed": 0.95,
    "max_non_dialogue_speed": 1.20,
    "max_non_dialogue_shot_duration": 30.0,
    "allow_trim": false,
    "allow_reuse": false,
    "allow_freeze": false
  }
}
```

Each v5 segment includes `asset_index`, local and global source ranges, `source_audio_volume`, `audio_role`, `speed`, and `reuses_source: false`. Segments on `MUTE_USE_MASTER_VO` assets have volume `0`; segments on `PRESERVE_SOURCE_AUDIO` assets keep reviewed source sound. Dialogue anchors found on a muted source-narration asset are recorded in `ignored_dialogue_candidates` and never delete the master narration.

When `dialogue_annotations.dialogue_lines_complete` is true, per-line roles take precedence only
inside their protected intervals. `DIALOGUE_USE_VIDEO_AUDIO` creates an active `1x` dialogue anchor
and forces source volume `1`, even within an otherwise muted mixed-role asset.
`NARRATION_USE_MASTER_VO` keeps the master narration and forces source volume `0` for that protected
source interval, even within an otherwise preserved asset. Each overridden segment records both
`asset_audio_role` and `audio_override_annotation_id`; missing annotation roles are a hard failure.

V5 narration placement avoids `source_speech_start/end`, while the complete safety-handle interval remains at `1x`. This permits narration over non-spoken source ambience without masking or overlapping character speech. `natural_room` records unused moving picture before each next dialogue and after the final spoken line. The last entry is the natural dynamic tail.

The v5 audit must report `source_trimmed_duration: 0`, `source_reused_duration: 0`, reviewed role lists, all dialogue at `1x`, and a nonnegative natural tail.

After rendering, `audit_rendered_audio_sources.py` must compare every active dialogue in the encoded
preview with both its original source-video speech and the removed master-VO speech. A dialogue
fails if the source match is weak, the master voice or either master-voice edge remains, or the
source match does not clearly beat the master match. Timeline metadata alone cannot pass this gate.

Version 4 is the ordinary option when a source clip needs a repair, a duplicated take needs removal, or a natural cut helps the episode. It detects source hard cuts, deletes duplicated dialogue from narration, inserts clean source dialogue at exactly `1x`, and plans each non-dialogue shot against narration boundaries. Keep useful long takes intact; trim only where the edit requires it.

Required v4 policy fields:

```json
{
  "version": 4,
  "mode": "shot_level_dialogue_replacement",
  "preservation": {
    "full_video": false,
    "source_order_preserved": true,
    "dialogue_video_speed_one": true,
    "dialogue_boundaries_reviewed": true,
    "voice_dialogue_deleted": true,
    "remaining_voice_ranges_preserved": true,
    "no_hold_segments": true,
    "ordinary_non_dialogue_trim_allowed": true
  },
  "editing_policy": {
    "template_required": false,
    "dialogue_speed": 1.0,
    "min_non_dialogue_speed": 0.95,
    "max_non_dialogue_speed": 1.2,
    "max_non_dialogue_shot_duration": 30.0,
    "boundary_snap_window": 0.55,
    "freeze_allowed": false,
    "moving_non_dialogue_reuse_allowed": false,
    "max_reuse_segment_duration": 3.0
  }
}
```

Every v4 non-dialogue segment carries its source range and the reason for a trim or repair. Keep it inside one detected source shot. If a generic builder proposes repeated moving coverage, review that proposal and reject it when it creates a repeated action or a fragmented sequence. A static hold is invalid unless the user explicitly requests a final freeze.

Every `READY` dialogue anchor must include `source_speech_start/end`, `target_speech_start/end`, adjacent-word boundary evidence, `boundary_mode: adaptive_pause_clamped`, and `boundary_safe: true`. The cut ranges must fully contain matched speech without crossing previous or next spoken words.

## Legacy v3 example

Version 3 below is retained for backward compatibility only. It fully covered source video, allowed unrestricted slow motion, and capped acceleration at `1.2x`; do not use it for new previews.

```json
{
  "version": 3,
  "mode": "dialogue_replacement_ripple",
  "voice_source_duration": 80.062,
  "source_video_duration": 78.176,
  "final_duration": 80.702,
  "canvas": {"width": 1080, "height": 1920, "ratio": "9:16"},
  "preservation": {
    "full_video": true,
    "source_order_preserved": true,
    "dialogue_video_speed_one": true,
    "voice_dialogue_deleted": true,
    "remaining_voice_ranges_preserved": true,
    "non_dialogue_acceleration_capped": true,
    "semantic_alignment_reviewed": true
  },
  "audio_policy": {
    "gap": {
      "voice_present": true,
      "video_volume": 0.0,
      "max_video_acceleration": 1.2,
      "slow_motion_minimum": null
    },
    "dialogue": {"voice_present": false, "video_volume": 1.0, "video_speed": 1.0},
    "deleted_voice_duration": 0.640,
    "inserted_dialogue_duration": 1.280
  },
  "subtitle_profile": {
    "language": "es",
    "font_name": "Noto Sans SemiBold",
    "size": 14,
    "line_spacing": 0,
    "letter_spacing": 0,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_width": 15,
    "max_line_width": 0.70,
    "transform_y": -0.416667,
    "y_pixels": -400,
    "max_lines": 2,
    "horizontal_alignment": "center",
    "vertical_alignment": "top"
  },
  "semantic_alignment": {
    "beat_map": "C:/project/beat_map.json",
    "reviewed": true,
    "anchor_count": 1,
    "anchors": [
      {
        "id": "beat-001",
        "voice_time": 2.500,
        "source_time": 3.100,
        "narration_text": "He handed me the phone.",
        "visual_match": "phone close-up begins",
        "status": "READY"
      }
    ]
  },
  "voice_regions": [
    {
      "id": "voice-001",
      "source_start": 0.0,
      "source_end": 6.640,
      "timeline_start": 0.0,
      "timeline_end": 6.640
    },
    {
      "id": "voice-002",
      "source_start": 7.280,
      "source_end": 80.062,
      "timeline_start": 7.920,
      "timeline_end": 80.702
    }
  ],
  "dialogue_anchors": [
    {
      "id": "dialogue-001",
      "source_start": 8.087,
      "source_end": 9.367,
      "narration_source_start": 6.640,
      "narration_source_end": 7.280,
      "timeline_start": 6.640,
      "timeline_end": 7.920,
      "speed": 1.0,
      "narration_action": "DELETE"
    }
  ],
  "segments": [
    {
      "id": "segment-001",
      "kind": "gap",
      "video_file": "C:/project/videos/01_opening.mp4",
      "source_start": 0.0,
      "source_end": 8.087,
      "timeline_start": 0.0,
      "timeline_end": 6.640,
      "speed": 1.218,
      "status": "READY"
    },
    {
      "id": "segment-002",
      "kind": "dialogue",
      "video_file": "C:/project/videos/01_opening.mp4",
      "source_start": 8.087,
      "source_end": 9.367,
      "timeline_start": 6.640,
      "timeline_end": 7.920,
      "speed": 1.0,
      "status": "READY"
    }
  ]
}
```

For any segment, `speed = source duration / target duration`. For every dialogue segment, source and target durations must match within 2 ms and `speed` must be `1.0`. New v4 non-dialogue segments use the bounds declared in `editing_policy`.

`voice_regions` contains only retained narration. Every region maps its original source range to an equal-duration target range, so narration is never time-stretched. The gaps between consecutive source ranges are the physically deleted dialogue ranges; they must not appear as muted audio segments in the draft.

`semantic_alignment.anchors` are manually reviewed card points. Each maps the start of a narration idea to the ordered source moment that visibly shows that person, action, object, reaction, or reveal. Anchors must be strictly monotonic in both timelines, must stay outside deleted-dialogue spans, and must have `status: READY`. The timeline builder splits non-dialogue gaps at these points so the matching picture begins when the matching narration idea begins.

Every subtitle cue contains one or two explicit lines only. Each line must also pass the language profile's conservative safe-width limit; relying on Jianying auto-wrap is invalid because it can turn two explicit lines into three or four rendered lines. After export, verify every text segment resolves to pixel `Y=-800` (`transform_y=-0.833333` on a 1920-high canvas).

V4 forbids `hold` segments and any reused segment without `moving_reuse: true`. Timeline targets must cover zero through `final_duration` continuously. Primary source segments remain monotonic; supplemental moving cutaways may revisit earlier source ranges, while dialogue coverage stays exact.
