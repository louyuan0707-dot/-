#!/usr/bin/env python3
"""Report local prerequisites without modifying the project."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    checks = {
        "python": {"ok": sys.version_info >= (3, 8), "version": sys.version.split()[0]},
        "ffprobe": {"ok": shutil.which("ffprobe") is not None},
        "faster_whisper": {"ok": importlib.util.find_spec("faster_whisper") is not None},
        "numpy": {"ok": importlib.util.find_spec("numpy") is not None},
        "pyJianYingDraft": {"ok": importlib.util.find_spec("pyJianYingDraft") is not None},
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(checks, handle, ensure_ascii=False, indent=2)
    return 0 if all(checks[name]["ok"] for name in ("python", "ffprobe", "numpy")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
