#!/usr/bin/env python3
"""Transcribe every ordered source video's original audio with word timestamps."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_assets(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assets = data.get("assets", [])
    if not assets:
        raise SystemExit("Assets manifest has no videos")
    return assets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="int8")
    args = parser.parse_args()
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        parser.error(f"Missing faster-whisper. Install requirements first. ({error})")

    assets = load_assets(args.assets)
    prompt = args.prompt_file.read_text(encoding="utf-8").strip() if args.prompt_file and args.prompt_file.is_file() else None
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    result = {"version": 2, "language": args.language, "source_assets": str(args.assets.resolve()), "assets": []}
    offset = 0.0
    for asset_index, asset in enumerate(assets):
        path = Path(asset["path"])
        if not path.is_file():
            raise SystemExit(f"Video does not exist: {path}")
        item = {
            "source_index": asset_index,
            "path": str(path.resolve()),
            "duration": float(asset["duration"]),
            "source_offset": round(offset, 6),
            "segments": [],
        }
        if not asset.get("has_audio", True):
            item["warning"] = "video has no audio stream"
            result["assets"].append(item)
            offset += float(asset["duration"])
            continue
        segments, info = model.transcribe(
            str(path), language=args.language, initial_prompt=prompt,
            vad_filter=True, word_timestamps=True,
        )
        for segment_index, segment in enumerate(segments, start=1):
            text = segment.text.strip()
            if not text:
                continue
            words = []
            for word in segment.words or []:
                token = word.word.strip()
                if token:
                    words.append({
                        "start": round(word.start, 3),
                        "end": round(word.end, 3),
                        "global_start": round(offset + word.start, 3),
                        "global_end": round(offset + word.end, 3),
                        "word": token,
                        "probability": round(float(word.probability), 4),
                    })
            item["segments"].append({
                "id": f"video-{asset_index + 1:03d}-{segment_index:03d}",
                "start": round(segment.start, 3), "end": round(segment.end, 3),
                "global_start": round(offset + segment.start, 3),
                "global_end": round(offset + segment.end, 3),
                "text": text, "words": words,
            })
        item["detected_language"] = info.language
        result["assets"].append(item)
        offset += float(asset["duration"])
    result["source_duration"] = round(offset, 6)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    count = sum(len(item["segments"]) for item in result["assets"])
    print(f"Wrote {count} source-video speech segment(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
