from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

from .common import AgentWorktreesError, atomic_write, ensure_lines, read_json, write_json
from .config import ensure_default_config
from .security import safe_repository_path


PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = PACKAGE_ROOT / "templates"
ROOT_GUIDANCE = {
    "AGENTS.md": """## Persistent agent worktrees

- Codex and Claude Code share the physical worktrees under `.codex/worktrees/<letter>`.
- Each lane must keep matching folder, local branch, and `origin/<letter>` names.
- Use the native `$pull`, `$ship`, and `$walkthrough` skills for lane operations.
- Provider-native configuration stays semantically aligned without adapters, symlinks, or cross-provider loaders.
- Ordinary Stop hooks may checkpoint and push only the matching letter branch; they never push the default branch.
""",
    "CLAUDE.md": """## Persistent agent worktrees

- Claude Code and Codex share the physical worktrees under `.codex/worktrees/<letter>`.
- Each lane must keep matching folder, local branch, and `origin/<letter>` names.
- Use the native `/pull`, `/ship`, and `/walkthrough` skills for lane operations.
- Provider-native configuration stays semantically aligned without adapters, symlinks, or cross-provider loaders.
- Ordinary Stop hooks may checkpoint and push only the matching letter branch; they never push the default branch.
""",
}
GUIDANCE_START = "<!-- codex-claude-worktrees:start -->"
GUIDANCE_END = "<!-- codex-claude-worktrees:end -->"


def _template_files(provider: str, names: tuple[str, ...] | None = None) -> list[Path]:
    base = TEMPLATES / provider
    files = [path for path in base.rglob("*") if path.is_file()]
    if names is None:
        return files
    return [path for path in files if any(f"/skills/{name}/" in path.as_posix() or path.name == f"agent-worktrees-{name}.py" for name in names)]


def _install_templates(root: Path, names: tuple[str, ...], *, allow_existing: bool = False) -> None:
    for provider in ("codex", "claude"):
        provider_root = TEMPLATES / provider
        for source in _template_files(provider, names):
            relative = source.relative_to(provider_root)
            if not safe_repository_path(root, relative.as_posix()):
                raise AgentWorktreesError(f"unsafe native install path: {relative}")
            destination = root / relative
            if destination.is_file() and destination.read_bytes() != source.read_bytes() and not allow_existing:
                raise AgentWorktreesError(
                    f"refusing to overwrite existing native file {relative}; resolve it in the audit"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if destination.parent.name == "hooks" or destination.suffix == ".py":
                destination.chmod(destination.stat().st_mode | stat.S_IXUSR)


def _append_guidance(root: Path) -> None:
    for name, body in ROOT_GUIDANCE.items():
        if not safe_repository_path(root, name):
            raise AgentWorktreesError(f"unsafe native guidance path: {name}")
        path = root / name
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        block = f"{GUIDANCE_START}\n{body.strip()}\n{GUIDANCE_END}"
        if GUIDANCE_START in existing and GUIDANCE_END in existing:
            start = existing.index(GUIDANCE_START)
            end = existing.index(GUIDANCE_END, start) + len(GUIDANCE_END)
            updated = existing[:start] + block + existing[end:]
        else:
            updated = existing.rstrip("\n") + ("\n\n" if existing.strip() else "") + block + "\n"
        atomic_write(path, updated)


def bootstrap_install(root: Path) -> dict[str, object]:
    legacy_state = root / ".agent-worktrees/walkthrough-state.json"
    if legacy_state.exists() or legacy_state.is_symlink():
        raise AgentWorktreesError(
            "refusing repository-local walkthrough state; review and remove "
            ".agent-worktrees/walkthrough-state.json before setup"
        )
    for relative in (".agent-worktrees/config.json", ".gitignore"):
        if not safe_repository_path(root, relative):
            raise AgentWorktreesError(f"unsafe bootstrap path: {relative}")
    ensure_default_config(root)
    _install_templates(root, ("walkthrough",), allow_existing=False)
    ensure_lines(
        root / ".gitignore",
        (
            ".codex/worktrees/",
            ".agent-worktrees/audit/",
            ".agent-worktrees/locks/",
            ".claude/settings.local.json",
        ),
    )
    return {"status": "installed", "root": str(root), "mode": "bootstrap"}


def full_install(root: Path) -> dict[str, object]:
    _install_templates(root, ("pull", "ship"), allow_existing=False)
    _append_guidance(root)
    return {"status": "installed", "root": str(root), "mode": "full"}


def _append_hook(raw: dict[str, object], event: str, command: str, status: str) -> None:
    hooks = raw.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise AgentWorktreesError("native hook settings must contain a hooks object")
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise AgentWorktreesError(f"hooks.{event} must be an array")
    if any(command in json.dumps(entry) for entry in entries):
        return
    entries.append(
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 960,
                    "statusMessage": status,
                }
            ],
        }
    )


def activate_hooks(root: Path) -> dict[str, object]:
    for relative in (".codex/hooks.json", ".claude/settings.json"):
        if not safe_repository_path(root, relative):
            raise AgentWorktreesError(f"unsafe native hook settings path: {relative}")
    codex_path = root / ".codex/hooks.json"
    codex_raw = read_json(codex_path, {"description": "Project hooks.", "hooks": {}})
    if not isinstance(codex_raw, dict):
        raise AgentWorktreesError(".codex/hooks.json must contain an object")
    _append_hook(
        codex_raw,
        "Stop",
        "agent-worktrees hook --harness codex",
        "Aligning native agent configuration and checkpointing the lane",
    )
    write_json(codex_path, codex_raw)
    claude_path = root / ".claude/settings.json"
    claude_raw = read_json(claude_path, {"hooks": {}})
    if not isinstance(claude_raw, dict):
        raise AgentWorktreesError(".claude/settings.json must contain an object")
    _append_hook(
        claude_raw,
        "Stop",
        "agent-worktrees hook --harness claude",
        "Aligning native agent configuration and checkpointing the lane",
    )
    write_json(claude_path, claude_raw)
    return {"status": "activated", "codex": str(codex_path), "claude": str(claude_path)}


def extra_manifest_groups(root: Path) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = [
        {
            "name": "agent-worktrees-instructions",
            "classification": "adapted",
            "allowCrossProviderReferences": True,
            "codex": ["AGENTS.md"],
            "claude": ["CLAUDE.md"],
        },
        {
            "name": "agent-worktrees-walkthrough",
            "classification": "adapted",
            "allowCrossProviderReferences": True,
            "codex": [".agents/skills/walkthrough"],
            "claude": [".claude/skills/walkthrough"],
        },
        {
            "name": "agent-worktrees-pull",
            "classification": "adapted",
            "codex": [".agents/skills/pull"],
            "claude": [".claude/skills/pull"],
        },
        {
            "name": "agent-worktrees-ship",
            "classification": "adapted",
            "codex": [".agents/skills/ship"],
            "claude": [".claude/skills/ship"],
        },
        {
            "name": "agent-worktrees-stop",
            "classification": "adapted",
            "codex": [".codex/hooks.json"],
            "claude": [".claude/settings.json"],
        },
    ]
    return groups
