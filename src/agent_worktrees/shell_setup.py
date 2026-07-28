from __future__ import annotations

import datetime as dt
import re
import shlex
from pathlib import Path

from .common import AgentWorktreesError, atomic_write
from .config import ProjectConfig
from .git_lanes import primary_checkout_root


START = "# >>> codex-claude-worktrees >>>"
END = "# <<< codex-claude-worktrees <<<"


def _shortcut_block(primary: Path, config: ProjectConfig) -> str:
    lines = [START, "# Persistent lanes: plain z<letter> = Claude Code; trailing c = Codex."]
    root = primary / config.worktree_root
    for lane in config.lanes:
        destination = shlex.quote(str(root / lane))
        lines.extend(
            [
                f"z{lane}() {{",
                f"  cd {destination} || return",
                '  if [ "$#" -gt 0 ]; then claude "$@"; else claude --continue || claude; fi',
                "}",
                f"z{lane}c() {{",
                f"  cd {destination} || return",
                '  if [ "$#" -gt 0 ]; then codex "$@"; else codex resume --last || codex; fi',
                "}",
            ]
        )
    lines.append(END)
    return "\n".join(lines)


def shortcut_conflicts(zshrc: Path, config: ProjectConfig) -> list[str]:
    if not zshrc.is_file():
        return []
    text = zshrc.read_text(encoding="utf-8")
    without_managed = re.sub(
        re.escape(START) + r".*?" + re.escape(END), "", text, flags=re.DOTALL
    )
    conflicts: list[str] = []
    for lane in config.lanes:
        for name in (f"z{lane}", f"z{lane}c"):
            pattern = rf"(?m)^\s*(?:alias\s+{re.escape(name)}=|function\s+{re.escape(name)}\b|{re.escape(name)}\s*\(\))"
            if re.search(pattern, without_managed):
                conflicts.append(name)
    return conflicts


def install_shortcuts(root: Path, config: ProjectConfig, zshrc: Path | None = None) -> dict[str, object]:
    target = zshrc or Path.home() / ".zshrc"
    conflicts = shortcut_conflicts(target, config)
    if conflicts:
        raise AgentWorktreesError(
            "existing shell shortcuts must be removed or renamed first: " + ", ".join(conflicts)
        )
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    replacement = _shortcut_block(primary_checkout_root(root), config)
    if START in existing and END in existing:
        updated = re.sub(
            re.escape(START) + r".*?" + re.escape(END),
            replacement,
            existing,
            flags=re.DOTALL,
        )
    else:
        updated = existing.rstrip("\n") + ("\n\n" if existing.strip() else "") + replacement + "\n"
    if updated == existing:
        return {"status": "clean", "path": str(target)}
    if target.is_file():
        timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"{target.name}.agent-worktrees.{timestamp}.bak")
        backup.write_bytes(target.read_bytes())
    else:
        backup = None
    atomic_write(target, updated)
    return {
        "status": "installed",
        "path": str(target),
        "backup": str(backup) if backup else None,
        "commands": [value for lane in config.lanes for value in (f"z{lane}", f"z{lane}c")],
    }
