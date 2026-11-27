#!/usr/bin/env python3
"""Create a reviewed per-asset audio-role map before dialogue matching or editing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


MUTE = "MUTE_USE_MASTER_VO"
PRESERVE = "PRESERVE_SOURCE_AUDIO"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read JSON {path}: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mute-asset", type=int, action="append", default=[])
    parser.add_argument("--preserve-asset", type=int, action="append", default=[])
    parser.add_argument(
        "--evidence",
        default="Reviewed by the editor against the supplied source and reference edit",
    )
    args = parser.parse_args()

    if not args.assets.is_file():
        parser.error(f"--assets does not exist: {args.assets}")
    if args.output.exists():
        parser.error(f"Refusing to overwrite existing output: {args.output}")
    assets = load_json(args.assets).get("assets", [])
    mute = set(args.mute_asset)
    preserve = set(args.preserve_asset)
    overlap = sorted(mute & preserve)
    if overlap:
        parser.error("Assets cannot be both muted and preserved: " + ", ".join(map(str, overlap)))

    known = {int(item.get("source_index", index)) for index, item in enumerate(assets)}
    unknown = sorted((mute | preserve) - known)
    if unknown:
        parser.error("Unknown asset indices: " + ", ".join(map(str, unknown)))
    unassigned = sorted(known - mute - preserve)
    if unassigned:
        parser.error(
            "Every asset needs an explicit reviewed audio role; unassigned: "
            + ", ".join(map(str, unassigned))
        )

    roles = []
    for index, asset in enumerate(assets):
        source_index = int(asset.get("source_index", index))
        role = MUTE if source_index in mute else PRESERVE
        roles.append({
            "asset_index": source_index,
            "video_file": str(Path(asset["path"]).resolve()),
            "role": role,
            "source_audio_volume": 0.0 if role == MUTE else 1.0,
            "eligible_for_dialogue_anchors": role == PRESERVE,
            "status": "READY",
            "review_evidence": args.evidence,
        })
    payload = {
        "version": 1,
        "mode": "reviewed_asset_audio_roles",
        "source_assets": str(args.assets.resolve()),
        "roles": roles,
        "all_assets_reviewed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created reviewed audio-role map: {args.output} ({len(roles)} assets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
