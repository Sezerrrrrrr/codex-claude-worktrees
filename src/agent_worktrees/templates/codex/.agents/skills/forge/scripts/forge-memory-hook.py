#!/usr/bin/env python3
"""Restore canonical Forge memory for an isolated Fable compaction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if event.get("hook_event_name") != "SessionStart" or event.get("source") != "compact":
        return 0
    cwd = event.get("cwd")
    feature = os.environ.get("FORGE_ACTIVE_FEATURE")
    if not isinstance(cwd, str) or not feature:
        return 0
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return 0
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (Path(cwd) / common).resolve()
    state_root = common.parent / ".forge-state" if common.name == ".git" else Path(cwd) / ".forge-state"
    state_path = state_root / feature / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if state.get("state") == "COMPLETE":
        return 0
    memory_path = state.get("memory", {}).get("path")
    if not isinstance(memory_path, str) or not Path(memory_path).is_file():
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": Path(memory_path).read_text(encoding="utf-8"),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
