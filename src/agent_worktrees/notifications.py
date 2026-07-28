from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

from .common import AgentWorktreesError, atomic_write, read_json, run, write_json
from .security import safe_repository_path


CODEX_START = "# >>> codex-claude-worktrees notifications >>>"
CODEX_END = "# <<< codex-claude-worktrees notifications <<<"


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.agent-worktrees.{timestamp}.bak")
    backup.write_bytes(path.read_bytes())
    return backup


def _codex_notify_block() -> str:
    command = json.dumps(["agent-worktrees", "notify-handler"])
    return f"{CODEX_START}\nnotify = {command}\n{CODEX_END}"


def configure_codex(config_path: Path | None = None) -> dict[str, object]:
    path = config_path or Path.home() / ".codex/config.toml"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    unmanaged = re.sub(
        re.escape(CODEX_START) + r".*?" + re.escape(CODEX_END), "", existing, flags=re.DOTALL
    )
    if re.search(r"(?m)^\s*notify\s*=", unmanaged):
        raise AgentWorktreesError(
            f"{path} already has a notify command; merge it manually instead of overwriting it"
        )
    block = _codex_notify_block()
    if CODEX_START in existing and CODEX_END in existing:
        updated = re.sub(
            re.escape(CODEX_START) + r".*?" + re.escape(CODEX_END),
            block,
            existing,
            flags=re.DOTALL,
        )
    else:
        updated = existing.rstrip("\n") + ("\n\n" if existing.strip() else "") + block + "\n"
    if updated == existing:
        return {"status": "clean", "path": str(path)}
    backup = _backup(path)
    atomic_write(path, updated)
    return {"status": "installed", "path": str(path), "backup": str(backup) if backup else None}


def _claude_hook(command: str) -> dict[str, object]:
    return {
        "matcher": "",
        "hooks": [{"type": "command", "command": command, "timeout": 10}],
    }


def configure_claude(root: Path) -> dict[str, object]:
    if not safe_repository_path(root, ".claude/settings.local.json"):
        raise AgentWorktreesError("unsafe Claude Code local settings path")
    path = root / ".claude/settings.local.json"
    raw = read_json(path, {})
    if not isinstance(raw, dict):
        raise AgentWorktreesError(f"{path} must contain a JSON object")
    hooks = raw.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise AgentWorktreesError(f"{path} hooks must be an object")
    command = "agent-worktrees notify-handler"
    changed = False
    for event in ("Notification", "Stop"):
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise AgentWorktreesError(f"{path} hooks.{event} must be an array")
        if not any(command in json.dumps(entry) for entry in entries):
            entries.append(_claude_hook(command))
            changed = True
    if not changed:
        return {"status": "clean", "path": str(path)}
    backup = _backup(path)
    write_json(path, raw)
    return {"status": "installed", "path": str(path), "backup": str(backup) if backup else None}


def configure(root: Path, claude_roots: list[Path] | None = None) -> dict[str, object]:
    roots = claude_roots or [root]
    return {
        "status": "installed",
        "codex": configure_codex(),
        "claude": [configure_claude(candidate) for candidate in roots],
    }


def send_notification(title: str, message: str) -> None:
    if shutil.which("osascript"):
        escaped_title = title.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        escaped_message = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        result = run(
            [
                "osascript",
                "-e",
                f'display notification "{escaped_message}" with title "{escaped_title}"',
            ]
        )
        if result.returncode == 0:
            return
    if shutil.which("notify-send"):
        run(["notify-send", title, message], check=True)
        return
    raise AgentWorktreesError("no supported desktop notification command is available")


def notification_main(arguments: list[str] | None = None) -> int:
    values = arguments if arguments is not None else sys.argv[1:]
    raw = values[0] if values else sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = str(payload.get("type") or payload.get("hook_event_name") or "Agent")
    if event == "agent-turn-complete":
        title = "Codex finished"
    elif event == "Notification":
        title = "Claude Code needs attention"
    else:
        title = "Claude Code finished"
    message = str(payload.get("message") or payload.get("last_assistant_message") or "Your agent is ready.")
    send_notification(title, message[:180])
    return 0


if __name__ == "__main__":
    raise SystemExit(notification_main())
