from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class DialogueAnnotationTests(unittest.TestCase):
    def test_complete_notes_turn_unlisted_source_speech_into_master_vo(self) -> None:
        notes_module = load_module("apply_dialogue_notes")
        dialogue_map = {"anchors": [
            {"id": "a", "target_text": "Clara Torres.", "video_file": "clip.mp4"},
            {"id": "b", "target_text": "Demasiado tranquila.", "video_file": "clip.mp4"},
        ]}
        notes = {
            "dialogue_lines_complete": True,
            "lines": [{
                "id": "line-013", "text": "Clara Torres", "speaker": "Mateo",
                "role": notes_module.DIALOGUE,
            }],
        }
        result = notes_module.apply_notes(dialogue_map, notes)
        self.assertEqual(result["anchors"][0]["annotation_role"], notes_module.DIALOGUE)
        self.assertEqual(result["anchors"][0]["annotation_speaker"], "Mateo")
        self.assertEqual(result["anchors"][1]["annotation_role"], notes_module.NARRATION)
        self.assertTrue(result["dialogue_annotations"]["dialogue_lines_complete"])

    def test_partial_notes_are_rejected_as_a_hard_gate(self) -> None:
        notes_module = load_module("apply_dialogue_notes")
        with self.assertRaisesRegex(ValueError, "dialogue_lines_complete"):
            notes_module.validate_notes({
                "dialogue_lines_complete": False,
                "lines": [{"text": "Clara Torres", "role": notes_module.DIALOGUE}],
            })


class RenderedAudioAuditTests(unittest.TestCase):
    def test_ncc_finds_the_inserted_source_audio(self) -> None:
        audit = load_module("audit_rendered_audio_sources")
        rng = np.random.default_rng(17)
        template = rng.normal(0.0, 0.2, 3200).astype(np.float32)
        signal = rng.normal(0.0, 0.005, 12000).astype(np.float32)
        signal[4200:7400] += template
        score, position = audit.best_ncc(signal, template)
        self.assertGreater(score, 0.95)
        self.assertAlmostEqual(position, 4200 / audit.RATE, places=3)

    def test_master_residue_or_weak_source_fails(self) -> None:
        audit = load_module("audit_rendered_audio_sources")
        errors = audit.evaluate_scores(
            source_score=0.42,
            master_score=0.80,
            master_head_score=0.77,
            master_tail_score=0.71,
            min_source_score=0.55,
            max_master_score=0.50,
            max_master_edge_score=0.62,
            source_margin=0.15,
        )
        self.assertTrue(any("source-video match" in item for item in errors))
        self.assertTrue(any("master-VO" in item for item in errors))
        self.assertTrue(any("head residue" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
