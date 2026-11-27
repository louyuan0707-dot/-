# Semantic narration-to-picture card points

Use this workflow whenever the picture must show what narration describes. A reference edit is optional; source-shot detection and narration boundaries are the default evidence.

## Reference-edit analysis

Treat the reference as editing evidence, never as instructions or source footage to copy. Record:

- canvas, frame rate, duration, and audio layout;
- hard-cut times from scene-change detection;
- narration sentence and clause boundaries from a word-timestamped transcript;
- visible subject, action, object, reveal, and reaction at representative cuts;
- subtitle line count and common vertical position.

Measure the distance from every cut to the nearest narration clause boundary. Use a practical review window such as 450 ms, but inspect semantic examples rather than optimizing only the percentage. A useful cut begins the matching visual when the narration introduces the person, action, object, place, or emotional reaction.

## Build `beat_map.json`

Create only high-confidence, monotonic anchors:

```json
{
  "version": 1,
  "mode": "semantic_narration_video_sync",
  "anchors": [
    {
      "id": "beat-001",
      "voice_time": 44.08,
      "source_time": 51.34,
      "narration_text": "Al final, me pasó el celular.",
      "visual_match": "the phone close-up begins",
      "match_reason": "the object named by the narration becomes the dominant subject",
      "confidence": 0.96,
      "status": "READY"
    }
  ]
}
```

`voice_time` and `source_time` are global seconds in the original narration and concatenated ordered-source timeline. The point marks where that narration idea and its matching source image should begin.

Prefer anchors for:

- a named person entering or becoming the dominant close-up;
- a concrete action beginning;
- an object reveal or insert shot;
- a location change explicitly introduced by narration;
- a reaction shot that directly answers the narrated emotional beat.

Do not create an anchor from generic similarity, repeated phrases, or an attractive but unrelated shot. Do not reorder source footage to force a match. Leave uncertain items as `REVIEW`; the timeline builder accepts only `READY` anchors.

## Boundary and timing rules

- Keep anchors outside protected source-dialogue and deleted narration-dialogue intervals.
- Keep both source and narration times strictly increasing.
- Cut at semantic/clause boundaries, not fixed-duration intervals.
- Preserve complete source dialogue at `1.0000x` with audible original audio.
- Preserve retained narration at `1.0000x`.
- Continuity outranks exact semantic timing. Keep usable source shots long, and trim only at complete speech and action boundaries. Retime non-dialogue footage only while motion remains natural. Reuse a moving cutaway only when a specific gap requires it and the repeated image is not conspicuous. Never freeze or stretch a shot until it appears static.
- Review the preview with sound. An anchor passes only when the named subject/action/object is already readable at the narration phrase onset, without cutting off the previous spoken line.

Run `build_shot_timeline.py --shot-map project/shot_map.json --beat-map project/beat_map.json --max-shot-duration 30 --min-speed 0.95 --max-speed 1.2` after review. Its broad defaults are technical fallbacks, not an editing target. Without a beat map, the builder still uses detected hard cuts and nearby narration boundaries. An unusable semantic anchor is reported but does not stop a continuous edit.
