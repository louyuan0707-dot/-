#!/usr/bin/env python3
"""Transcribe narration into timestamped JSON using faster-whisper."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="small", help="faster-whisper model name or local path")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="int8")
    args = parser.parse_args()
    if not args.audio.is_file():
        parser.error(f"Audio does not exist: {args.audio}")
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        parser.error(f"Missing faster-whisper. Install requirements first. ({error})")
    prompt = args.prompt_file.read_text(encoding="utf-8").strip() if args.prompt_file and args.prompt_file.is_file() else None
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    segments, info = model.transcribe(
        str(args.audio), language=args.language, initial_prompt=prompt,
        vad_filter=True, word_timestamps=True,
    )
    output = {"version": 1, "language": info.language, "duration": info.duration, "segments": []}
    for index, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        if text:
            words = []
            for word in segment.words or []:
                token = word.word.strip()
                if token:
                    words.append({
                        "start": round(word.start, 3), "end": round(word.end, 3),
                        "word": token, "probability": round(float(word.probability), 4),
                    })
            output["segments"].append({
                "id": f"voice-{index:03d}", "start": round(segment.start, 3),
                "end": round(segment.end, 3), "text": text, "words": words,
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(output['segments'])} transcript segment(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
