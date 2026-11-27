#!/usr/bin/env python3
"""Run a Skill script with the dedicated local Python and FFmpeg configuration."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: run.cmd <script.py> [arguments...]", file=sys.stderr)
        return 2
    task_root = Path.home() / ".codex"
    ffmpeg_bin = task_root / "tools" / "ffmpeg-9.0" / "ffmpeg-9.0-full_build" / "bin"
    if not (ffmpeg_bin / "ffprobe.exe").is_file():
        print(f"Missing configured FFmpeg: {ffmpeg_bin}", file=sys.stderr)
        return 2
    script = Path(sys.argv[1]).resolve()
    if not script.is_file():
        print(f"Script does not exist: {script}", file=sys.stderr)
        return 2
    os.environ["PATH"] = str(ffmpeg_bin) + os.pathsep + os.environ.get("PATH", "")
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
