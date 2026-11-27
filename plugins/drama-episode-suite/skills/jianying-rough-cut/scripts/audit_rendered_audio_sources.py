#!/usr/bin/env python3
"""Verify that each rendered dialogue uses source-video audio and has no master-VO residue."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np


RATE = 8000


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def decode(path: Path, ffmpeg: Path, rate: int = RATE) -> np.ndarray:
    command = [
        str(ffmpeg), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
        "-ar", str(rate), "-f", "f32le", "-",
    ]
    try:
        raw = subprocess.check_output(command)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Cannot decode audio from {path}: ffmpeg exited {error.returncode}")
    return np.frombuffer(raw, dtype=np.float32).copy()


def clip(audio: np.ndarray, start: float, end: float, rate: int = RATE) -> np.ndarray:
    lower = max(0, round(start * rate))
    upper = min(len(audio), round(end * rate))
    return audio[lower:upper]


def preemphasis(values: np.ndarray) -> np.ndarray:
    if len(values) < 2:
        return values.astype(np.float64)
    output = np.empty(len(values), dtype=np.float64)
    output[0] = float(values[0])
    output[1:] = values[1:] - 0.97 * values[:-1]
    return output


def best_ncc(signal: np.ndarray, template: np.ndarray, rate: int = RATE) -> tuple[float, float]:
    """Return absolute normalized cross-correlation peak and its signal-relative time."""
    source = preemphasis(signal)
    probe = preemphasis(template)
    if len(probe) < round(0.16 * rate) or len(probe) >= len(source):
        return 0.0, 0.0
    probe -= probe.mean()
    probe_energy = float(np.dot(probe, probe))
    if probe_energy <= 1e-12:
        return 0.0, 0.0
    valid = len(source) - len(probe) + 1
    fft_size = 1 << (len(source) + len(probe) - 2).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(source, fft_size) * np.fft.rfft(probe[::-1], fft_size), fft_size
    )
    dots = correlation[len(probe) - 1:len(probe) - 1 + valid]
    cumulative = np.concatenate(([0.0], np.cumsum(source)))
    cumulative_square = np.concatenate(([0.0], np.cumsum(source * source)))
    sums = cumulative[len(probe):] - cumulative[:-len(probe)]
    squares = cumulative_square[len(probe):] - cumulative_square[:-len(probe)]
    energies = np.maximum(squares - sums * sums / len(probe), 1e-12)
    scores = dots / np.sqrt(probe_energy * energies)
    index = int(np.argmax(np.abs(scores)))
    return float(abs(scores[index])), index / rate


def evaluate_scores(
    source_score: float,
    master_score: float,
    master_head_score: float,
    master_tail_score: float,
    min_source_score: float,
    max_master_score: float,
    max_master_edge_score: float,
    source_margin: float,
) -> list[str]:
    errors = []
    if source_score < min_source_score:
        errors.append(
            f"source-video match {source_score:.3f} is below {min_source_score:.3f}"
        )
    if master_score > max_master_score:
        errors.append(
            f"complete master-VO match {master_score:.3f} exceeds {max_master_score:.3f}"
        )
    if master_head_score > max_master_edge_score:
        errors.append(
            f"master-VO head residue {master_head_score:.3f} exceeds {max_master_edge_score:.3f}"
        )
    if master_tail_score > max_master_edge_score:
        errors.append(
            f"master-VO tail residue {master_tail_score:.3f} exceeds {max_master_edge_score:.3f}"
        )
    if source_score < master_score + source_margin:
        errors.append(
            f"source-video match does not beat master VO by {source_margin:.3f}"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--search-padding", type=float, default=0.60)
    parser.add_argument("--min-source-score", type=float, default=0.55)
    parser.add_argument("--max-master-score", type=float, default=0.50)
    parser.add_argument("--max-master-edge-score", type=float, default=0.62)
    parser.add_argument("--source-margin", type=float, default=0.15)
    args = parser.parse_args()
    for path in (args.preview, args.timeline, args.voice, args.ffmpeg):
        if not path.exists():
            parser.error(f"Input does not exist: {path}")
    if args.output.exists():
        parser.error(f"Refusing to overwrite existing output: {args.output}")

    timeline = load_json(args.timeline)
    anchors = timeline.get("active_dialogues", [])
    if not anchors:
        parser.error("Timeline has no active dialogue anchors")
    preview_audio = decode(args.preview, args.ffmpeg)
    master_audio = decode(args.voice, args.ffmpeg)
    source_cache: dict[str, np.ndarray] = {}
    results = []
    failures = []
    for anchor in anchors:
        anchor_id = str(anchor.get("id") or "unknown")
        source_path = Path(anchor["video_file"])
        source_key = str(source_path.resolve())
        if source_key not in source_cache:
            source_cache[source_key] = decode(source_path, args.ffmpeg)
        asset_global_start = float(anchor["source_start"]) - float(anchor["source_local_start"])
        source_local_start = float(anchor["source_speech_start"]) - asset_global_start
        source_local_end = float(anchor["source_speech_end"]) - asset_global_start
        source_template = clip(source_cache[source_key], source_local_start, source_local_end)
        master_template = clip(
            master_audio, float(anchor["target_speech_start"]), float(anchor["target_speech_end"])
        )
        expected_start = float(anchor["timeline_start"]) + (
            float(anchor["source_speech_start"]) - float(anchor["source_start"])
        )
        expected_end = expected_start + len(source_template) / RATE
        window_start = max(0.0, expected_start - args.search_padding)
        window_end = expected_end + args.search_padding
        preview_window = clip(preview_audio, window_start, window_end)
        source_score, source_at = best_ncc(preview_window, source_template)
        master_score, master_at = best_ncc(preview_window, master_template)
        edge_length = min(len(master_template) // 2, round(0.55 * RATE))
        if edge_length >= round(0.16 * RATE):
            master_head_score, master_head_at = best_ncc(
                preview_window, master_template[:edge_length]
            )
            master_tail_score, master_tail_at = best_ncc(
                preview_window, master_template[-edge_length:]
            )
        else:
            master_head_score = master_tail_score = 0.0
            master_head_at = master_tail_at = 0.0
        errors = evaluate_scores(
            source_score, master_score, master_head_score, master_tail_score,
            args.min_source_score, args.max_master_score, args.max_master_edge_score,
            args.source_margin,
        )
        result = {
            "id": anchor_id,
            "text": anchor.get("target_text"),
            "status": "FAIL" if errors else "PASS",
            "expected_timeline_start": round(expected_start, 3),
            "source_score": round(source_score, 4),
            "source_match_at": round(window_start + source_at, 3),
            "master_score": round(master_score, 4),
            "master_match_at": round(window_start + master_at, 3),
            "master_head_score": round(master_head_score, 4),
            "master_head_match_at": round(window_start + master_head_at, 3),
            "master_tail_score": round(master_tail_score, 4),
            "master_tail_match_at": round(window_start + master_tail_at, 3),
            "errors": errors,
        }
        results.append(result)
        if errors:
            failures.append(result)
    payload = {
        "version": 1,
        "status": "FAIL" if failures else "PASS",
        "preview": str(args.preview.resolve()),
        "timeline": str(args.timeline.resolve()),
        "dialogue_count": len(results),
        "failed_dialogue_count": len(failures),
        "thresholds": {
            "min_source_score": args.min_source_score,
            "max_master_score": args.max_master_score,
            "max_master_edge_score": args.max_master_edge_score,
            "source_margin": args.source_margin,
        },
        "dialogues": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        print(
            "Rendered-audio source audit FAIL: "
            + "; ".join(f"{item['id']}: {', '.join(item['errors'])}" for item in failures)
        )
        return 3
    print(f"Rendered-audio source audit PASS: {len(results)} dialogue line(s): {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
