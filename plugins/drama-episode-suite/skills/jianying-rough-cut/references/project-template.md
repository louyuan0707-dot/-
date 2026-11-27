# Project input template

```text
project/
├── voice.mp3
├── music.mp4                  # 用户提供；也可为 mp3/m4a/wav
├── script.docx                 # Optional wording authority
├── script.txt                  # Optional plain-text ASR prompt
├── dialogue_notes.json         # Optional but authoritative: editor-marked dialogue lines
├── subtitle_language.txt       # Explicit tag: en/vi/es/ja/ko/th/ar/zh-TW
├── reference.mp4               # Optional user edit; editing-grammar evidence only
├── reference_transcript.json   # Optional timestamped transcript for cut analysis
├── beat_map.json               # Reviewed narration-to-picture semantic card points
├── video_order.txt             # One relative filename per line
├── repair_notes.json           # Optional: 需替换的句子、版本和连续性记录
├── videos/
│   ├── 01_opening.mp4
│   ├── 02_middle.mp4
│   └── 03_ending.mp4
└── output/
```

`video_order.txt` is required whenever filename sorting is not the intended story order:

```text
01_opening.mp4
03_middle.mp4
02_ending.mp4
```

Generated working files:

```text
assets.json
narration.json
video_transcript.json
dialogue_map.json
dialogue_map_annotated.json      # Required when dialogue_notes.json is supplied
shot_map.json                   # Required; detected from source videos, never from a template
audio_role_map_reviewed.json    # Required; every source asset has a reviewed sound role
retime_plan_reference_reviewed.json  # READY per-shot targets; reference-derived when available
reference_analysis/
├── contact_sheet.jpg
├── reference_profile.json
└── reference_profile.md
output/preview_v01/
├── timeline.json
├── timeline_preview.md
├── subtitle.srt
├── edit_report.md
└── rendered_audio_source_audit.json # Required after rendering when dialogue exists
```

An editor-marked `dialogue_notes.json` is a hard role gate, not timing evidence. Convert a supplied
DOCX/TXT note sheet into this UTF-8 JSON shape; obtain all timings from the media transcripts:

```json
{
  "version": 1,
  "dialogue_lines_complete": true,
  "lines": [
    {
      "id": "line-013",
      "text": "A complete character line.",
      "role": "DIALOGUE_USE_VIDEO_AUDIO",
      "speaker": "Character A",
      "video_file_hint": "shot-05.mp4",
      "source_start_hint": 23.7
    }
  ]
}
```

`dialogue_lines_complete: true` is valid only after checking that every character-dialogue sentence
is listed. The script treats unlisted source speech as `NARRATION_USE_MASTER_VO`, so do not set this
flag from a partial document or an unreviewed ASR result. File/time hints only disambiguate repeats.
`apply_dialogue_notes.py` must stop when a marked line has zero or multiple source matches.

The script helps transcription and review. Only media-derived transcripts supply timing. `dialogue_map.json` is the dialogue-boundary review gate: a `READY` anchor must include matched speech start/end, stay outside adjacent spoken words, and pass the boundary audit. `shot_map.json` is generated from hard cuts in the ordered source videos and is the required timing basis. `beat_map.json` is the optional semantic card-point review gate; it maps narration ideas to matching ordered-source picture moments and must stay monotonic.

`audio_role_map_reviewed.json` is a hard gate before production. Use `MUTE_USE_MASTER_VO` for a source video whose people are only voicing narration; it receives source volume `0` and cannot contribute dialogue anchors. Use `PRESERVE_SOURCE_AUDIO` only for reviewed diegetic ambience and character dialogue. Never infer an unknown role silently.

Complete dialogue notes can override those coarse per-asset roles only inside a reviewed speech
interval. This supports a source file that mixes narration and character dialogue: annotated
dialogue is audible at `1x`; annotated/unlisted source narration is muted only for its interval.

`reference.mp4` is never required. When present, use it to learn per-shot retiming, dialogue placement, subtitle scale/height, and natural-tail behavior. `build_reference_retime_plan.py` maps the reviewed reference cuts to detected source shots; extra detector cuts remain recorded as ignored evidence. Later episodes may use a manually reviewed source/narration retime plan instead.

Record the subtitle language explicitly and install its required exact font before draft generation. The pipeline must stop on a missing font rather than silently substitute one. Record the user's subtitle and music preferences with the project; the English defaults here are Y=-400, outline 15, no punctuation, and music around -6 dB. Every output preview uses a 1080x1920 canvas and versioned folders such as `preview_v03`; never overwrite an approved preview.
