#!/usr/bin/env python3
"""Match ordered source-video speech to the same words in a narration transcript."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", flags=re.UNICODE)
COMMON_SINGLE_WORDS = {
    "a", "al", "de", "del", "el", "ella", "en", "es", "la", "las", "lo", "los",
    "me", "mi", "no", "o", "por", "que", "se", "si", "su", "te", "un", "una", "y",
}


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    match = WORD_RE.search(value)
    return match.group(0) if match else ""


def fallback_words(text: str, start: float, end: float) -> list[dict]:
    tokens = WORD_RE.findall(text)
    if not tokens:
        return []
    duration = max(0.001, end - start)
    return [
        {
            "word": token,
            "start": start + duration * index / len(tokens),
            "end": start + duration * (index + 1) / len(tokens),
        }
        for index, token in enumerate(tokens)
    ]


def narration_words(data: dict) -> list[dict]:
    output = []
    for segment_index, segment in enumerate(data.get("segments", [])):
        words = segment.get("words") or fallback_words(
            str(segment.get("text", "")), float(segment["start"]), float(segment["end"])
        )
        usable = [word for word in words if normalize(str(word.get("word", "")))]
        for word_index, word in enumerate(usable):
            token = normalize(str(word.get("word", "")))
            if token:
                output.append({
                    "token": token, "word": str(word.get("word", "")).strip(),
                    "start": float(word["start"]), "end": float(word["end"]),
                    "probability": float(word["probability"]) if word.get("probability") is not None else None,
                    "segment_index": segment_index,
                    "segment_start": float(segment["start"]), "segment_end": float(segment["end"]),
                    "segment_word_index": word_index, "segment_word_count": len(usable),
                })
    return output


def source_words(data: dict) -> list[dict]:
    output = []
    for asset_index, asset in enumerate(data.get("assets", [])):
        offset = float(asset.get("source_offset", 0.0))
        for segment_index, segment in enumerate(asset.get("segments", [])):
            words = segment.get("words") or fallback_words(
                str(segment.get("text", "")), float(segment["start"]), float(segment["end"])
            )
            usable = [word for word in words if normalize(str(word.get("word", "")))]
            for word_index, word in enumerate(usable):
                token = normalize(str(word.get("word", "")))
                if not token:
                    continue
                local_start = float(word["start"])
                local_end = float(word["end"])
                output.append({
                    "token": token, "word": str(word.get("word", "")).strip(),
                    "probability": float(word["probability"]) if word.get("probability") is not None else None,
                    "asset_index": asset_index, "video_file": asset["path"],
                    "asset_duration": float(asset["duration"]),
                    "start": local_start, "end": local_end,
                    "global_start": float(word.get("global_start", offset + local_start)),
                    "global_end": float(word.get("global_end", offset + local_end)),
                    "segment_index": segment_index,
                    "segment_start": float(segment["start"]),
                    "segment_end": float(segment["end"]),
                    "segment_word_index": word_index,
                    "segment_word_count": len(usable),
                })
    return output


def split_block_by_asset(block: tuple[int, int, int], source: list[dict]) -> list[tuple[int, int, int]]:
    source_index, target_index, size = block
    result = []
    run_start = 0
    for position in range(1, size + 1):
        boundary = position == size or source[source_index + position]["asset_index"] != source[source_index + run_start]["asset_index"]
        if boundary:
            run_size = position - run_start
            result.append((source_index + run_start, target_index + run_start, run_size))
            run_start = position
    return result


def merge_blocks(blocks: list[tuple[int, int, int]], source: list[dict], target: list[dict]) -> list[tuple[int, int, int, int]]:
    merged: list[list[int]] = []
    for source_index, target_index, size in blocks:
        source_end = source_index + size
        target_end = target_index + size
        if merged:
            previous = merged[-1]
            same_asset = source[previous[0]]["asset_index"] == source[source_index]["asset_index"]
            source_gap = source_index - previous[1]
            target_gap = target_index - previous[3]
            if same_asset and 0 <= source_gap <= 1 and 0 <= target_gap <= 1:
                previous[1] = source_end
                previous[3] = target_end
                continue
        merged.append([source_index, source_end, target_index, target_end])
    return [tuple(item) for item in merged]


def count_occurrences(tokens: list[str], phrase: list[str]) -> int:
    if not phrase or len(phrase) > len(tokens):
        return 0
    width = len(phrase)
    return sum(tokens[index:index + width] == phrase for index in range(len(tokens) - width + 1))


def mean_probability(words: list[dict]) -> float:
    values = [float(item["probability"]) for item in words if item.get("probability") is not None]
    return sum(values) / len(values) if values else 1.0


def protected_span(words: list[dict], duration: float, head: float, tail: float) -> tuple[float, float]:
    """Legacy isolated-span helper kept for callers without neighbouring words."""
    if not words:
        raise ValueError("Cannot protect an empty dialogue span")
    first, last = words[0], words[-1]
    start = float(first["start"]) - max(0.0, head)
    end = float(last["end"]) + max(0.0, tail)
    if int(first.get("segment_word_index", -1)) == 0:
        start = min(start, float(first.get("segment_start", first["start"])))
    if int(last.get("segment_word_index", -1)) == int(last.get("segment_word_count", 0)) - 1:
        end = max(end, float(last.get("segment_end", last["end"])))
    return max(0.0, start), min(float(duration), end)


def adaptive_protected_span(
    words: list[dict], duration: float, head: float, tail: float,
    previous_word: dict | None = None, next_word: dict | None = None,
    guard: float = 0.01,
) -> tuple[float, float, bool]:
    """Protect matched speech while staying inside adjacent spoken words."""
    if not words:
        raise ValueError("Cannot protect an empty dialogue span")
    first, last = words[0], words[-1]
    speech_start = float(first["start"])
    speech_end = float(last["end"])
    lower = 0.0
    upper = float(duration)
    if previous_word is not None:
        lower = min(speech_start, float(previous_word["end"]) + max(0.0, guard))
    if next_word is not None:
        upper = max(speech_end, float(next_word["start"]) - max(0.0, guard))
    start = max(lower, speech_start - max(0.0, head))
    end = min(upper, speech_end + max(0.0, tail))
    safe = (
        start <= speech_start + 0.002
        and end >= speech_end - 0.002
        and (previous_word is None or start >= float(previous_word["end"]) - 0.002)
        and (next_word is None or end <= float(next_word["start"]) + 0.002)
    )
    return max(0.0, start), min(float(duration), end), safe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--videos", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-block-words", type=int, default=2)
    parser.add_argument("--min-confidence", type=float, default=0.72)
    parser.add_argument("--source-head-handle", type=float, default=0.08)
    parser.add_argument("--source-tail-handle", type=float, default=0.12)
    parser.add_argument("--narration-head-handle", type=float, default=0.08)
    parser.add_argument("--narration-tail-handle", type=float, default=0.15)
    parser.add_argument(
        "--boundary-padding", type=float,
        help="Deprecated symmetric override for all four protected-boundary handles.",
    )
    args = parser.parse_args()
    narration = load_json(args.narration)
    videos = load_json(args.videos)
    if args.boundary_padding is not None:
        if args.boundary_padding < 0:
            parser.error("--boundary-padding cannot be negative")
        args.source_head_handle = args.boundary_padding
        args.source_tail_handle = args.boundary_padding
        args.narration_head_handle = args.boundary_padding
        args.narration_tail_handle = args.boundary_padding
    handles = (
        args.source_head_handle, args.source_tail_handle,
        args.narration_head_handle, args.narration_tail_handle,
    )
    if any(value < 0 for value in handles):
        parser.error("Dialogue boundary handles cannot be negative")
    target = narration_words(narration)
    source = source_words(videos)
    if not target:
        parser.error("Narration transcript has no usable words")
    if not source:
        parser.error("Source-video transcript has no usable words")

    source_tokens = [item["token"] for item in source]
    target_tokens = [item["token"] for item in target]
    matcher = SequenceMatcher(None, source_tokens, target_tokens, autojunk=False)
    raw_blocks = []
    for block in matcher.get_matching_blocks():
        if not block.size:
            continue
        keep_single = block.size == 1 and len(source[block.a]["token"]) >= 4 and source[block.a]["token"] not in COMMON_SINGLE_WORDS
        if block.size >= args.min_block_words or keep_single:
            raw_blocks.extend(split_block_by_asset((block.a, block.b, block.size), source))
    blocks = merge_blocks(raw_blocks, source, target)
    anchors = []
    covered_source_words: set[int] = set()
    for index, (source_start_index, source_end_index, target_start_index, target_end_index) in enumerate(blocks, start=1):
        source_span = source[source_start_index:source_end_index]
        target_span = target[target_start_index:target_end_index]
        phrase = [item["token"] for item in source_span]
        source_occurrences = count_occurrences(source_tokens, phrase)
        target_occurrences = count_occurrences(target_tokens, phrase)
        source_asr_confidence = mean_probability(source_span)
        target_asr_confidence = mean_probability(target_span)
        confidence = min(source_asr_confidence, target_asr_confidence)
        review_reasons = []
        if len(source_span) < args.min_block_words:
            review_reasons.append("single-word anchor")
        if source_occurrences > 1:
            review_reasons.append(f"phrase repeats {source_occurrences} times in source video speech")
        if target_occurrences > 1:
            review_reasons.append(f"phrase repeats {target_occurrences} times in narration")
        if confidence < args.min_confidence:
            review_reasons.append(f"ASR confidence {confidence:.4f} is below {args.min_confidence:.4f}")
        review_reasons.append(
            "listen to the protected source and narration boundaries; set status READY only after "
            "the full source line is audible and no duplicate narration dialogue remains"
        )
        status = "REVIEW"
        covered_source_words.update(range(source_start_index, source_end_index))
        source_previous = (
            source[source_start_index - 1]
            if source_start_index > 0
            and source[source_start_index - 1]["asset_index"] == source_span[0]["asset_index"]
            else None
        )
        source_next = (
            source[source_end_index]
            if source_end_index < len(source)
            and source[source_end_index]["asset_index"] == source_span[0]["asset_index"]
            else None
        )
        source_local_start, source_local_end, source_boundary_safe = adaptive_protected_span(
            source_span, source_span[-1]["asset_duration"],
            args.source_head_handle, args.source_tail_handle,
            source_previous, source_next,
        )
        source_global_start = source_span[0]["global_start"] - (source_span[0]["start"] - source_local_start)
        source_global_end = source_span[-1]["global_end"] + (source_local_end - source_span[-1]["end"])
        narration_duration = float(
            narration.get("duration")
            or max(float(item["end"]) for item in narration.get("segments", []))
        )
        target_previous = target[target_start_index - 1] if target_start_index > 0 else None
        target_next = target[target_end_index] if target_end_index < len(target) else None
        target_start, target_end, target_boundary_safe = adaptive_protected_span(
            target_span, narration_duration,
            args.narration_head_handle, args.narration_tail_handle,
            target_previous, target_next,
        )
        boundary_safe = source_boundary_safe and target_boundary_safe
        if not boundary_safe:
            review_reasons.append("no clean pause boundary exists around the complete matched line")
        if anchors:
            previous = anchors[-1]
            if target_start < float(previous["target_end"]):
                review_reasons.append("protected narration boundary overlaps the previous anchor")
            if (
                int(previous["source_asset_index"]) == int(source_span[0]["asset_index"])
                and source_global_start < float(previous["source_end"])
            ):
                review_reasons.append("protected source boundary overlaps the previous anchor")
        anchors.append({
            "id": f"dialogue-{index:03d}", "status": status,
            "video_file": source_span[0]["video_file"],
            "source_asset_index": source_span[0]["asset_index"],
            "source_local_start": round(source_local_start, 3),
            "source_local_end": round(source_local_end, 3),
            "source_start": round(source_global_start, 3),
            "source_end": round(source_global_end, 3),
            "target_start": round(target_start, 3),
            "target_end": round(target_end, 3),
            "source_speech_start": round(float(source_span[0]["global_start"]), 3),
            "source_speech_end": round(float(source_span[-1]["global_end"]), 3),
            "target_speech_start": round(float(target_span[0]["start"]), 3),
            "target_speech_end": round(float(target_span[-1]["end"]), 3),
            "source_previous_end": (
                round(float(source_previous["global_end"]), 3) if source_previous else None
            ),
            "source_next_start": (
                round(float(source_next["global_start"]), 3) if source_next else None
            ),
            "target_previous_end": (
                round(float(target_previous["end"]), 3) if target_previous else None
            ),
            "target_next_start": (
                round(float(target_next["start"]), 3) if target_next else None
            ),
            "boundary_safe": boundary_safe,
            "source_text": " ".join(item["word"] for item in source_span),
            "target_text": " ".join(item["word"] for item in target_span),
            "token_count": len(source_span), "confidence": round(confidence, 4),
            "source_asr_confidence": round(source_asr_confidence, 4),
            "target_asr_confidence": round(target_asr_confidence, 4),
            "source_occurrences": source_occurrences,
            "target_occurrences": target_occurrences,
            "boundary_mode": "adaptive_pause_clamped",
            "source_head_handle": round(args.source_head_handle, 3),
            "source_tail_handle": round(args.source_tail_handle, 3),
            "narration_head_handle": round(args.narration_head_handle, 3),
            "narration_tail_handle": round(args.narration_tail_handle, 3),
            "review_reasons": review_reasons,
        })

    unmatched = [item for word_index, item in enumerate(source) if word_index not in covered_source_words]
    output = {
        "version": 3, "mode": "dialogue_word_alignment_delete_voice",
        "source_narration": str(args.narration.resolve()),
        "source_videos": str(args.videos.resolve()),
        "anchors": anchors,
        "summary": {
            "source_word_count": len(source), "target_word_count": len(target),
            "matched_source_words": len(covered_source_words),
            "unmatched_source_words": len(unmatched),
            "ready_anchors": sum(item["status"] == "READY" for item in anchors),
            "review_anchors": sum(item["status"] != "READY" for item in anchors),
        },
        "unmatched_source_speech": [
            {
                "video_file": item["video_file"], "start": round(item["start"], 3),
                "end": round(item["end"], 3), "word": item["word"],
            }
            for item in unmatched
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Matched {len(covered_source_words)}/{len(source)} source speech word(s) "
        f"into {len(anchors)} dialogue anchor(s): {args.output}"
    )
    return 0 if anchors and not output["summary"]["review_anchors"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
