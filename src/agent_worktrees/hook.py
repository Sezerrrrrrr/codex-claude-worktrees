from __future__ import annotations

import json
import os
from pathlib import Path

from .checkpoint import checkpoint
from .common import AgentWorktreesError, run
from .config import ProjectConfig
from .git_lanes import validate_lane
from .parity import synchronize


def _output(harness: str, reason: str) -> dict[str, object]:
    if harness == "codex":
        return {"decision": "block", "reason": reason}
    return {"decision": "block", "reason": reason}


def stop_hook(
    root: Path, config: ProjectConfig, harness: str, payload: dict[str, object]
) -> dict[str, object]:
    if payload.get("stop_hook_active") is True or os.environ.get("AGENT_WORKTREES_CHILD") == "1":
        return {}
    parity = synchronize(root, config, harness)
    status = parity.get("status")
    if status in {"conflict", "needs_user"}:
        return _output(
            harness,
            f"Native agent parity needs a decision: {parity.get('summary')} "
            f"Groups: {', '.join(str(value) for value in parity.get('groups', []))}",
        )
    if status == "applied":
        return _output(
            harness,
            f"Native parity updated the {parity.get('target')} configuration. "
            f"Review and report these groups before stopping: {', '.join(str(value) for value in parity.get('groups', []))}.",
        )
    for command, label in (
        (["git", "diff", "--check"], "working-tree whitespace"),
        (["git", "diff", "--cached", "--check"], "staged whitespace"),
    ):
        result = run(command, cwd=root)
        if result.returncode != 0:
            return _output(harness, f"Resolve the {label} errors before stopping: {(result.stderr or result.stdout).strip()}")
    if not config.auto_checkpoint:
        return {}
    try:
        validate_lane(root, config)
    except AgentWorktreesError:
        return {}
    saved = checkpoint(root, config, harness)
    if saved.get("status") == "checkpointed":
        return _output(
            harness,
            f"Checkpointed and pushed this lane as: {saved.get('subject')}. Report the checkpoint before stopping.",
        )
    return {}


def parse_payload(text: str) -> dict[str, object]:
    try:
        value = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
