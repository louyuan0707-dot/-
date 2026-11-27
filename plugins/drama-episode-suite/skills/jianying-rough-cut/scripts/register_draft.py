#!/usr/bin/env python3
"""Register an existing editable draft after Jianying and its tray app have exited."""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import time
from pathlib import Path


def app_is_running() -> bool:
    command = (
        "Get-Process -Name JianyingPro,JianyingProTray -ErrorAction SilentlyContinue "
        "| Select-Object -First 1 -ExpandProperty Id"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True, text=True, check=True,
    )
    return bool(result.stdout.strip())


def media_paths(content: object) -> list[Path]:
    paths: list[Path] = []
    if isinstance(content, dict):
        for key, value in content.items():
            if key == "path" and isinstance(value, str) and value:
                paths.append(Path(value))
            else:
                paths.extend(media_paths(value))
    elif isinstance(content, list):
        for value in content:
            paths.extend(media_paths(value))
    return paths


def register(root: Path, name: str, *, check_process: bool = True) -> str:
    root = root.resolve()
    folder = root / name
    index_path = root / "root_meta_info.json"
    meta_path = folder / "draft_meta_info.json"
    content_path = folder / "draft_content.json"
    if check_process and app_is_running():
        raise RuntimeError("Save work and fully exit Jianying, including the tray process, before registration")
    for path in (index_path, meta_path, content_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    content = json.loads(content_path.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if Path(index["root_path"]).resolve() != root:
        raise ValueError("Index root_path does not match --draft-root")
    if meta.get("draft_name") != name:
        raise ValueError("Draft metadata name does not match folder name")
    missing = [path for path in media_paths(content) if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Draft references {len(missing)} missing media files; first: {missing[0]}")
    matches = [item for item in index["all_draft_store"] if item.get("draft_name") == name]
    if matches:
        if len(matches) == 1 and matches[0].get("draft_id") == meta.get("draft_id"):
            return "already-registered"
        raise ValueError("A draft with this name is indexed under a different identity")
    if not index["all_draft_store"]:
        raise ValueError("Index has no template entry")
    entry = copy.deepcopy(index["all_draft_store"][0])
    now = int(time.time() * 1_000_000)
    cover = folder / "draft_cover.jpg"
    entry.update({
        "draft_cover": str(cover) if cover.is_file() else "",
        "draft_fold_path": str(folder),
        "draft_id": meta["draft_id"],
        "draft_json_file": str(content_path),
        "draft_name": name,
        "draft_root_path": str(root),
        "draft_timeline_materials_size": sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()),
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": int(meta["tm_duration"]),
    })
    entry["draft_cloud_sync"] = False
    entry["draft_cloud_last_action_download"] = False
    entry["streaming_edit_draft_ready"] = True
    index["all_draft_store"].insert(0, entry)
    index["draft_ids"] = int(index.get("draft_ids", 0)) + 1
    backup = root / f"root_meta_info.before-{name}-{now}.json"
    backup.write_bytes(index_path.read_bytes())
    temporary = root / f"root_meta_info.{name}.tmp"
    temporary.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if check_process and app_is_running():
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Jianying reopened during registration; original index was kept")
    os.replace(temporary, index_path)
    verify = json.loads(index_path.read_text(encoding="utf-8"))
    if verify["all_draft_store"][0]["draft_id"] != meta["draft_id"]:
        raise RuntimeError("Draft registration did not persist")
    return "registered"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-root", type=Path, required=True)
    parser.add_argument("--draft-name", required=True)
    args = parser.parse_args()
    print(register(args.draft_root, args.draft_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
