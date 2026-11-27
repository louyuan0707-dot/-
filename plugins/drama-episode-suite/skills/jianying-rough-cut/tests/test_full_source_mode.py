from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_full_source_timeline.py"
sys.path.insert(0, str(ROOT / "scripts"))

from build_full_source_timeline import split_short_semantic_cues
from subtitle_rules import display_units


class FullSourceModeTests(unittest.TestCase):
    def test_short_caption_chunks_are_balanced_and_safe(self) -> None:
        cue = {
            "start": 0.0,
            "end": 3.0,
            "text": "A la mañana siguiente me llegó otro mensaje de una cuenta anónima.",
        }
        parts = split_short_semantic_cues(cue)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(display_units(item["text"]) <= 28 for item in parts))
        self.assertTrue(all(len(item["text"].split()) >= 3 for item in parts))

    def test_muted_narration_asset_is_not_a_dialogue_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            asset0, asset1 = work / "a.mp4", work / "b.mp4"
            assets = {
                "assets": [
                    {"path": str(asset0), "duration": 2.0, "source_index": 0},
                    {"path": str(asset1), "duration": 4.0, "source_index": 1},
                ]
            }
            narration = {
                "duration": 5.0,
                "segments": [
                    {"start": 0.0, "end": 0.8, "text": "Inicio."},
                    {"start": 1.0, "end": 1.8, "text": "Sigue la narración."},
                    {"start": 2.0, "end": 3.0, "text": "Diálogo real."},
                    {"start": 3.2, "end": 4.5, "text": "Final de la historia."},
                ],
            }
            dialogues = {"anchors": [
                {
                    "id": "false-anchor", "status": "READY", "source_asset_index": 0,
                    "source_start": 0.1, "source_speech_start": 0.2,
                    "source_speech_end": 1.7, "source_end": 1.8,
                    "target_start": 0.0, "target_speech_start": 0.0,
                    "target_speech_end": 1.8, "target_end": 1.9,
                    "target_text": "Narración del vídeo.",
                },
                {
                    "id": "real-anchor", "status": "READY", "source_asset_index": 1,
                    "source_start": 2.9, "source_speech_start": 3.1,
                    "source_speech_end": 3.9, "source_end": 4.1,
                    "target_start": 1.9, "target_speech_start": 2.0,
                    "target_speech_end": 3.0, "target_end": 3.1,
                    "target_text": "Diálogo real.",
                },
            ]}
            shot_map = {"shots": [
                {"id": "shot-001", "asset_index": 0, "global_start": 0.0,
                 "global_end": 2.0, "duration": 2.0},
                {"id": "shot-002", "asset_index": 1, "global_start": 2.0,
                 "global_end": 6.0, "duration": 4.0},
            ]}
            roles = {
                "all_assets_reviewed": True,
                "roles": [
                    {"asset_index": 0, "status": "READY", "role": "MUTE_USE_MASTER_VO",
                     "source_audio_volume": 0.0},
                    {"asset_index": 1, "status": "READY", "role": "PRESERVE_SOURCE_AUDIO",
                     "source_audio_volume": 1.0},
                ],
            }
            plan = {"status": "READY", "shots": [
                {"shot_id": "shot-001", "speed": 1.0},
                {"shot_id": "shot-002", "speed": 1.0},
            ]}
            inputs = {
                "assets": assets, "narration": narration, "dialogues": dialogues,
                "shot_map": shot_map, "roles": roles, "plan": plan,
            }
            paths = {}
            for name, payload in inputs.items():
                paths[name] = work / f"{name}.json"
                paths[name].write_text(json.dumps(payload), encoding="utf-8")
            output = work / "preview"
            command = [
                sys.executable, str(SCRIPT), "--assets", str(paths["assets"]),
                "--narration", str(paths["narration"]), "--dialogues", str(paths["dialogues"]),
                "--shot-map", str(paths["shot_map"]), "--audio-role-map", str(paths["roles"]),
                "--retime-plan", str(paths["plan"]), "--subtitle-language", "es",
                "--output", str(output),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
            audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(timeline["version"], 5)
            self.assertEqual([item["id"] for item in timeline["ignored_dialogue_candidates"]], ["false-anchor"])
            self.assertEqual([item["id"] for item in timeline["active_dialogues"]], ["real-anchor"])
            self.assertTrue(all(not item["reuses_source"] for item in timeline["segments"]))
            self.assertEqual(audit["source_trimmed_duration"], 0.0)
            self.assertEqual(audit["source_reused_duration"], 0.0)
            muted = [item for item in timeline["segments"] if item["asset_index"] == 0]
            self.assertTrue(muted and all(item["source_audio_volume"] == 0.0 for item in muted))
            dialogue = [item for item in timeline["segments"] if item["kind"] == "dialogue"]
            self.assertTrue(dialogue and all(abs(item["speed"] - 1.0) < 0.003 for item in dialogue))

    def test_explicit_dialogue_note_overrides_muted_asset_only_for_that_interval(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            asset = work / "mixed.mp4"
            payloads = {
                "assets": {"assets": [
                    {"path": str(asset), "duration": 4.0, "source_index": 0},
                ]},
                "narration": {
                    "duration": 3.0,
                    "segments": [
                        {"start": 0.0, "end": 0.8, "text": "Inicio."},
                        {"start": 1.0, "end": 2.0, "text": "Clara Torres."},
                        {"start": 2.2, "end": 3.0, "text": "Final."},
                    ],
                },
                "dialogues": {
                    "dialogue_annotations": {
                        "status": "READY", "dialogue_lines_complete": True,
                    },
                    "anchors": [{
                        "id": "name", "status": "READY", "source_asset_index": 0,
                        "source_start": 0.9, "source_speech_start": 1.0,
                        "source_speech_end": 2.0, "source_end": 2.1,
                        "target_start": 0.9, "target_speech_start": 1.0,
                        "target_speech_end": 2.0, "target_end": 2.1,
                        "target_text": "Clara Torres.",
                        "annotation_id": "line-013",
                        "annotation_role": "DIALOGUE_USE_VIDEO_AUDIO",
                    }],
                },
                "shots": {"shots": [{
                    "id": "shot-001", "asset_index": 0, "global_start": 0.0,
                    "global_end": 4.0, "duration": 4.0,
                }]},
                "roles": {
                    "all_assets_reviewed": True,
                    "roles": [{
                        "asset_index": 0, "status": "READY", "role": "MUTE_USE_MASTER_VO",
                        "source_audio_volume": 0.0,
                    }],
                },
                "plan": {"status": "READY", "shots": [
                    {"shot_id": "shot-001", "speed": 1.0},
                ]},
            }
            paths = {}
            for name, payload in payloads.items():
                paths[name] = work / f"{name}.json"
                paths[name].write_text(json.dumps(payload), encoding="utf-8")
            output = work / "preview"
            completed = subprocess.run([
                sys.executable, str(SCRIPT), "--assets", str(paths["assets"]),
                "--narration", str(paths["narration"]),
                "--dialogues", str(paths["dialogues"]),
                "--shot-map", str(paths["shots"]),
                "--audio-role-map", str(paths["roles"]),
                "--retime-plan", str(paths["plan"]),
                "--subtitle-language", "es", "--output", str(output),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
            dialogue = [item for item in timeline["segments"] if item["kind"] == "dialogue"]
            picture = [item for item in timeline["segments"] if item["kind"] == "picture"]
            self.assertTrue(dialogue and all(item["source_audio_volume"] == 1.0 for item in dialogue))
            self.assertTrue(picture and all(item["source_audio_volume"] == 0.0 for item in picture))
            self.assertEqual(timeline["active_dialogues"][0]["annotation_id"], "line-013")


if __name__ == "__main__":
    unittest.main()
