#!/usr/bin/env python3
"""Emit the release compatibility and source-hash record for CASE.

The command is read-only unless --output is supplied.  It records the exact
engine, bridge, native usage sources and optional packaged app binary used for
a release review; it does not build or install anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"version not found in {path}")
    return match.group(1)


def build(root: Path, app: Path | None) -> dict:
    engine = root / "engine/claude_session_sync.py"
    bridge = root / "tools/case_sync_bridge.py"
    native = [root / "src/native_usage.rs", root / "src/native_usage_mac.rs"]
    app_binary = app / "Contents/MacOS/CASE" if app else None
    return {
        "app": {
            "version": version(root / "Cargo.toml", r'^version\s*=\s*"([^"]+)"'),
            "binary": str(app_binary) if app_binary else None,
            "sha256": sha(app_binary) if app_binary and app_binary.is_file() else None,
        },
        "engine": {
            "version": version(engine, r'^VERSION\s*=\s*"([^"]+)"'),
            "path": str(engine),
            "sha256": sha(engine),
        },
        "bridge": {
            "version": version(bridge, r'^BRIDGE_VERSION\s*=\s*"([^"]+)"'),
            "path": str(bridge),
            "sha256": sha(bridge),
        },
        "config": {
            "hook_schema": 1,
            "runtime_schema": 2,
            "snapshot_manifest_schemas": [1, 2],
            "max_accounts": 8,
        },
        "native_usage": {
            "platform": "macOS",
            "paths": [str(path) for path in native],
            "sha256": {str(path): sha(path) for path in native},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, help="optional CASE.app bundle to hash")
    parser.add_argument("--output", type=Path, help="write JSON instead of stdout")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    value = build(root, args.app)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
