from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class MatcherSafetyTests(unittest.TestCase):
    def test_repeated_phrase_is_detectable(self) -> None:
        matcher = load_module("match_dialogues")
        self.assertEqual(
            matcher.count_occurrences(
                ["no", "puede", "ser", "no", "puede", "ser"],
                ["no", "puede", "ser"],
            ),
            2,
        )

    def test_missing_asr_probability_does_not_invent_a_low_score(self) -> None:
        matcher = load_module("match_dialogues")
        self.assertEqual(matcher.mean_probability([{"probability": None}]), 1.0)
        self.assertAlmostEqual(
            matcher.mean_probability([{"probability": 0.8}, {"probability": 0.6}]),
            0.7,
        )

    def test_protected_span_keeps_utterance_edges_and_handles(self) -> None:
        matcher = load_module("match_dialogues")
        words = [
            {
                "start": 1.0, "end": 1.4, "segment_start": 0.9, "segment_end": 2.1,
                "segment_word_index": 0, "segment_word_count": 2,
            },
            {
                "start": 1.5, "end": 2.0, "segment_start": 0.9, "segment_end": 2.1,
                "segment_word_index": 1, "segment_word_count": 2,
            },
        ]
        self.assertEqual(matcher.protected_span(words, 3.0, 0.08, 0.12), (0.9, 2.12))

    def test_adaptive_span_never_crosses_adjacent_words(self) -> None:
        matcher = load_module("match_dialogues")
        words = [{"start": 1.0, "end": 1.8}]
        start, end, safe = matcher.adaptive_protected_span(
            words, 3.0, 0.20, 0.20,
            previous_word={"end": 0.95}, next_word={"start": 1.86},
        )
        self.assertAlmostEqual(start, 0.96)
        self.assertAlmostEqual(end, 1.85)
        self.assertTrue(safe)

    def test_boundary_audit_rejects_intrusion_into_previous_word(self) -> None:
        audit = load_module("audit_dialogue_boundaries")
        anchor = {
            "id": "dialogue-001", "source_start": 0.8, "source_end": 1.8,
            "source_speech_start": 1.0, "source_speech_end": 1.7,
            "source_previous_end": 0.9, "source_next_start": 2.0,
            "target_start": 3.0, "target_end": 4.0,
            "target_speech_start": 3.1, "target_speech_end": 3.9,
            "target_previous_end": 2.9, "target_next_start": 4.1,
            "boundary_safe": True,
        }
        self.assertTrue(any("previous spoken word" in item for item in audit.boundary_errors(anchor)))


class SubtitleRuleTests(unittest.TestCase):
    def test_requested_profiles_are_exact(self) -> None:
        rules = load_module("subtitle_rules")
        expected = {
            "es": ("Noto Sans SemiBold", 14.0, 0),
            "vi": ("Noto Sans SemiBold", 14.0, 0),
            "ja": ("Noto Sans JP SemiBold", 10.0, 5),
            "ko": ("Noto Sans KR SemiBold", 10.0, 5),
            "th": ("Noto Sans Thai SemiBold", 14.0, 0),
            "ar": ("Noto Naskh Arabic SemiBold", 18.0, 0),
            "zh-TW": ("Source Han Sans TW Medium", 10.0, 0),
        }
        for language, profile_values in expected.items():
            profile = rules.profile_for_language(language)
            self.assertEqual(
                (profile["font_name"], profile["size"], profile["line_spacing"]),
                profile_values,
            )

    def test_long_cue_is_split_to_at_most_two_lines(self) -> None:
        rules = load_module("subtitle_rules")
        cues, _ = rules.normalize_cues([{
            "start": 0.0, "end": 6.0,
            "text": "This is a deliberately long subtitle that must be split by meaning and pause into multiple safe cues instead of becoming three lines on screen.",
        }], "en")
        self.assertGreater(len(cues), 1)
        self.assertTrue(all(len(item["text"].splitlines()) <= 2 for item in cues))
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[-1]["end"], 6.0)
        profile = rules.profile_for_language("en")
        self.assertTrue(all(
            rules.display_units(line) <= profile["max_line_units"]
            for cue in cues for line in cue["text"].splitlines()
        ))

    def test_english_subtitles_remove_punctuation(self) -> None:
        rules = load_module("subtitle_rules")
        cues, profile = rules.normalize_cues([{
            "start": 0.0, "end": 2.0, "text": "Alex, are we ready? I'm here!",
        }], "en")
        self.assertTrue(profile["no_punctuation"])
        self.assertTrue(all(
            not any(unicodedata.category(char).startswith("P") for char in cue["text"])
            for cue in cues
        ))


class TimelineTests(unittest.TestCase):
    def test_short_source_reuses_moving_coverage_without_pause_or_hold(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            assets = {"assets": [{"path": str(root / "01.mp4"), "duration": 2.0, "source_index": 0}]}
            narration = {
                "duration": 10.0, "language": "en",
                "segments": [{"start": 0.0, "end": 10.0, "text": "continuous narration",
                              "words": [{"word": f"word{index}", "start": float(index),
                                         "end": float(index + 1)} for index in range(10)]}],
            }
            dialogues = {"anchors": [{
                "id": "dialogue-001", "status": "READY",
                "source_start": 0.8, "source_end": 1.2,
                "target_start": 4.0, "target_end": 5.0,
                "source_speech_start": 0.85, "source_speech_end": 1.15,
                "target_speech_start": 4.1, "target_speech_end": 4.9,
                "source_previous_end": 0.75, "source_next_start": 1.25,
                "target_previous_end": 3.9, "target_next_start": 5.1,
                "boundary_safe": True, "target_text": "matched dialogue",
            }]}
            shot_map = {"version": 1, "mode": "ordered_source_hard_cuts", "shots": [
                {"id": "shot-001", "global_start": 0.0, "global_end": 1.0, "motion_score": 0.08},
                {"id": "shot-002", "global_start": 1.0, "global_end": 2.0, "motion_score": 0.06},
            ]}
            for name, data in (("assets", assets), ("narration", narration),
                               ("dialogues", dialogues), ("shots", shot_map)):
                (root / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
            output = root / "continuous_preview"
            result = subprocess.run([
                sys.executable, str(SCRIPTS / "build_shot_timeline.py"),
                "--assets", str(root / "assets.json"), "--narration", str(root / "narration.json"),
                "--dialogues", str(root / "dialogues.json"), "--shot-map", str(root / "shots.json"),
                "--output", str(output),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
            ordered = sorted(timeline["segments"], key=lambda item: item["timeline_start"])
            self.assertAlmostEqual(ordered[0]["timeline_start"], 0.0)
            for previous, current in zip(ordered, ordered[1:]):
                self.assertAlmostEqual(previous["timeline_end"], current["timeline_start"], places=5)
            self.assertAlmostEqual(ordered[-1]["timeline_end"], timeline["final_duration"], places=5)
            reused = [item for item in ordered if item["kind"] == "coverage_reuse"]
            self.assertTrue(reused)
            self.assertTrue(all(item["moving_reuse"] and item["reuses_source"] for item in reused))
            self.assertTrue(all(item["kind"] != "hold" for item in ordered))
            self.assertGreater(timeline["summary"]["reused_moving_coverage_duration"], 0.0)

    def test_shot_level_timeline_has_no_holds_and_uses_policy_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            assets = {"assets": [{"path": str(root / "01.mp4"), "duration": 10.0, "source_index": 0}]}
            narration = {
                "duration": 10.0, "language": "en",
                "segments": [{"start": 0.0, "end": 10.0, "text": "complete narration",
                              "words": [{"word": f"word{index}", "start": float(index),
                                         "end": float(index + 1)} for index in range(10)]}],
            }
            dialogues = {"anchors": [{
                "id": "dialogue-001", "status": "READY",
                "source_start": 3.0, "source_end": 4.5,
                "target_start": 4.0, "target_end": 5.0,
                "source_speech_start": 3.1, "source_speech_end": 4.4,
                "target_speech_start": 4.1, "target_speech_end": 4.9,
                "source_previous_end": 2.9, "source_next_start": 4.6,
                "target_previous_end": 3.9, "target_next_start": 5.1,
                "boundary_safe": True, "target_text": "matched dialogue",
            }]}
            shot_map = {"version": 1, "mode": "ordered_source_hard_cuts", "shots": [
                {"id": "shot-001", "global_start": 0.0, "global_end": 2.0},
                {"id": "shot-002", "global_start": 2.0, "global_end": 5.0},
                {"id": "shot-003", "global_start": 5.0, "global_end": 7.0},
                {"id": "shot-004", "global_start": 7.0, "global_end": 10.0},
            ]}
            for name, data in (("assets", assets), ("narration", narration),
                               ("dialogues", dialogues), ("shots", shot_map)):
                (root / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
            output = root / "preview_v04"
            result = subprocess.run([
                sys.executable, str(SCRIPTS / "build_shot_timeline.py"),
                "--assets", str(root / "assets.json"), "--narration", str(root / "narration.json"),
                "--dialogues", str(root / "dialogues.json"), "--shot-map", str(root / "shots.json"),
                "--output", str(output),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
            self.assertEqual((timeline["version"], timeline["mode"]),
                             (4, "shot_level_dialogue_replacement"))
            self.assertFalse(timeline["editing_policy"]["template_required"])
            self.assertTrue(timeline["preservation"]["no_hold_segments"])
            self.assertTrue(all(not item["reuses_source"] and item["kind"] != "hold"
                                for item in timeline["segments"]))
            non_dialogue = [item for item in timeline["segments"] if item["kind"] != "dialogue"]
            self.assertTrue(all(0.60 <= item["speed"] <= 2.60 for item in non_dialogue))
            self.assertTrue(all(item["timeline_end"] - item["timeline_start"] <= 6.0
                                for item in non_dialogue))

    def test_dialogue_is_one_x_and_voice_dialogue_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            assets = {
                "assets": [
                    {"path": str(root / "01.mp4"), "duration": 5.0, "source_index": 0},
                    {"path": str(root / "02.mp4"), "duration": 5.0, "source_index": 1},
                ]
            }
            words = [
                {"word": f"word{index}", "start": float(index), "end": float(index + 1)}
                for index in range(10)
            ]
            narration = {
                "duration": 10.0, "language": "en",
                "segments": [{"start": 0.0, "end": 10.0, "text": "complete narration", "words": words}],
            }
            dialogues = {
                "anchors": [{
                    "id": "dialogue-001", "status": "READY",
                    "source_start": 2.0, "source_end": 3.5,
                    "target_start": 4.0, "target_end": 5.0,
                    "target_text": "matched dialogue",
                }]
            }
            beat_map = {
                "version": 1,
                "mode": "semantic_narration_video_sync",
                "anchors": [{
                    "id": "beat-001", "voice_time": 2.0, "source_time": 1.0,
                    "narration_text": "the pictured action begins",
                    "visual_match": "matching ordered-source action begins",
                    "status": "READY",
                }],
            }
            for name, data in (
                ("assets", assets), ("narration", narration),
                ("dialogues", dialogues), ("beat_map", beat_map),
            ):
                (root / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
            output = root / "preview_v03"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "build_dialogue_timeline.py"),
                    "--assets", str(root / "assets.json"),
                    "--narration", str(root / "narration.json"),
                    "--dialogues", str(root / "dialogues.json"),
                    "--beat-map", str(root / "beat_map.json"),
                    "--output", str(output),
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(timeline["version"], 3)
            self.assertEqual(timeline["mode"], "dialogue_replacement_ripple")
            self.assertEqual(timeline["canvas"], {"width": 1080, "height": 1920, "ratio": "9:16"})
            self.assertAlmostEqual(timeline["final_duration"], 10.916667)
            dialogue = next(item for item in timeline["segments"] if item["kind"] == "dialogue")
            self.assertAlmostEqual(dialogue["speed"], 1.0)
            self.assertAlmostEqual(dialogue["timeline_end"] - dialogue["timeline_start"], 1.5)
            self.assertEqual(
                timeline["voice_regions"],
                [
                    {"id": "voice-001", "source_start": 0.0, "source_end": 2.0, "timeline_start": 0.0, "timeline_end": 2.0},
                    {"id": "voice-002", "beat_id": "beat-001", "source_start": 2.0, "source_end": 4.0, "timeline_start": 2.0, "timeline_end": 4.0},
                    {"id": "voice-003", "source_start": 5.0, "source_end": 10.0, "timeline_start": 5.5, "timeline_end": 10.5},
                ],
            )
            self.assertTrue(timeline["preservation"]["voice_dialogue_deleted"])
            self.assertTrue(timeline["preservation"]["non_dialogue_acceleration_capped"])
            self.assertTrue(timeline["preservation"]["semantic_alignment_reviewed"])
            self.assertEqual(timeline["semantic_alignment"]["anchor_count"], 1)
            self.assertTrue(any(item.get("beat_id") == "beat-001" for item in timeline["segments"]))
            self.assertLessEqual(
                max(item["speed"] for item in timeline["segments"] if item["kind"] != "dialogue"),
                1.2,
            )
            self.assertAlmostEqual(timeline["audio_policy"]["deleted_voice_duration"], 1.0)


class DraftEndToEndTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for the synthetic draft test")
    def test_draft_has_no_muted_voice_placeholder_and_uses_vertical_subtitles(self) -> None:
        try:
            import pyJianYingDraft  # noqa: F401
        except ImportError:
            self.skipTest("pyJianYingDraft is not installed")
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            font = root / "NotoSans-SemiBold.ttf"
            font.write_bytes(b"test-font-placeholder")
            subtitles = root / "subtitle.srt"
            subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nSubtitle test\n", encoding="utf-8")
            video_result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(video),
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(video_result.returncode, 0, video_result.stderr)
            voice_result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
                    "-c:a", "pcm_s16le", str(voice),
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(voice_result.returncode, 0, voice_result.stderr)
            music = root / "music.wav"
            effect = root / "effect.wav"
            for target, frequency, length in ((music, 220, 2.3), (effect, 880, 0.2)):
                result_audio = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={length}",
                     "-c:a", "pcm_s16le", str(target)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result_audio.returncode, 0, result_audio.stderr)
            effects_path = root / "effects.json"
            effects_path.write_text(json.dumps([{"path": str(effect), "timeline_start": 1.5,
                                                "duration": 0.2, "volume": 0.1}]), encoding="utf-8")
            timeline = {
                "version": 3, "mode": "dialogue_replacement_ripple",
                "voice_source_duration": 2.0, "final_duration": 2.2,
                "canvas": {"width": 1080, "height": 1920, "ratio": "9:16"},
                "preservation": {
                    "full_video": True, "source_order_preserved": True,
                    "dialogue_video_speed_one": True, "voice_dialogue_deleted": True,
                    "remaining_voice_ranges_preserved": True,
                },
                "subtitle_profile": {"language": "en"},
                "voice_regions": [
                    {"id": "voice-001", "source_start": 0.0, "source_end": 0.8, "timeline_start": 0.0, "timeline_end": 0.8},
                    {"id": "voice-002", "source_start": 1.2, "source_end": 2.0, "timeline_start": 1.4, "timeline_end": 2.2},
                ],
                "segments": [
                    {
                        "id": "segment-001", "kind": "gap", "status": "READY", "speed": 0.875,
                        "video_file": str(video), "source_start": 0.0, "source_end": 0.7,
                        "timeline_start": 0.0, "timeline_end": 0.8,
                    },
                    {
                        "id": "segment-002", "kind": "dialogue", "status": "READY", "speed": 1.0,
                        "video_file": str(video), "source_start": 0.7, "source_end": 1.3,
                        "timeline_start": 0.8, "timeline_end": 1.4,
                    },
                    {
                        "id": "segment-003", "kind": "gap", "status": "READY", "speed": 0.875,
                        "video_file": str(video), "source_start": 1.3, "source_end": 2.0,
                        "timeline_start": 1.4, "timeline_end": 2.2,
                    },
                ],
            }
            timeline_path = root / "timeline.json"
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
            draft_dir = root / "draft"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "build_jianying_draft.py"),
                    "--timeline", str(timeline_path), "--voice", str(voice),
                    "--subtitles", str(subtitles), "--draft-dir", str(draft_dir),
                    "--font-file", str(font), "--music", str(music),
                    "--sfx-manifest", str(effects_path),
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
            self.assertEqual(content["canvas_config"], {"width": 1080, "height": 1920, "ratio": "9:16"})
            video_track = next(item for item in content["tracks"] if item["type"] == "video")
            audio_track = next(item for item in content["tracks"] if item["type"] == "audio")
            text_track = next(item for item in content["tracks"] if item["type"] == "text")
            music_track = next(item for item in content["tracks"] if item["name"] == "background music")
            effect_track = next(item for item in content["tracks"] if item["name"] == "added sound effects")
            self.assertAlmostEqual(music_track["segments"][0]["volume"], 10 ** (-6 / 20))
            self.assertAlmostEqual(effect_track["segments"][0]["volume"], 0.1)
            self.assertEqual([item["volume"] for item in video_track["segments"]], [0.0, 1.0, 0.0])
            self.assertEqual([item["volume"] for item in audio_track["segments"]], [1.0, 1.0])
            self.assertEqual(len(audio_track["segments"]), 2)
            self.assertEqual(audio_track["segments"][1]["source_timerange"]["start"], 1_200_000)
            self.assertEqual(audio_track["segments"][1]["target_timerange"]["start"], 1_400_000)
            dialogue = video_track["segments"][1]
            self.assertEqual(dialogue["source_timerange"]["duration"], dialogue["target_timerange"]["duration"])
            text_material = content["materials"]["texts"][0]
            style = json.loads(text_material["content"])["styles"][0]
            self.assertEqual(style["size"], 14.0)
            self.assertEqual(style["fill"]["content"]["solid"]["color"], [1.0, 1.0, 1.0])
            self.assertEqual(style["strokes"][0]["content"]["solid"]["color"], [0.0, 0.0, 0.0])
            self.assertAlmostEqual(style["strokes"][0]["width"], 0.03)
            self.assertEqual(style["shadows"][0]["alpha"], 0.10)
            self.assertEqual(style["shadows"][0]["distance"], 5.0)
            self.assertEqual(text_material["alignment"], 1)
            self.assertEqual(text_material["letter_spacing"], 0.0)
            self.assertEqual(text_material["line_max_width"], 0.70)
            self.assertTrue(text_material["force_apply_line_max_width"])
            self.assertEqual(text_material["font_path"], str(font.resolve()))
            self.assertAlmostEqual(text_track["segments"][0]["clip"]["transform"]["y"], -0.4166666667)
            self.assertEqual(text_track["segments"][0]["uniform_scale"], {"on": True, "value": 1.0})
            self.assertEqual(text_track["segments"][0]["subtitle_vertical_alignment"], "top")


if __name__ == "__main__":
    unittest.main()
