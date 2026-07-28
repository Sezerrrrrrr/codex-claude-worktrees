#!/usr/bin/env python3
"""Restore the canonical memory for the Forge run bound to a compacted session."""

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
    session_id = event.get("session_id")
    cwd = event.get("cwd")
    if not isinstance(session_id, str) or not isinstance(cwd, str):
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
    matches: list[tuple[str, Path]] = []
    active_feature = os.environ.get("FORGE_ACTIVE_FEATURE")
    for state_path in sorted(state_root.glob("*/state.json")):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        feature_matches = active_feature == state_path.parent.name if active_feature else False
        session_matches = session_id in state.get("coordinator_session_ids", [])
        if state.get("state") == "COMPLETE" or not (feature_matches or session_matches):
            continue
        memory_path = state.get("memory", {}).get("path")
        if isinstance(memory_path, str) and Path(memory_path).is_file():
            matches.append((str(state.get("updated_at", "")), Path(memory_path)))
    if not matches:
        return 0
    memory = max(matches, key=lambda item: item[0])[1].read_text(encoding="utf-8")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": memory,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
