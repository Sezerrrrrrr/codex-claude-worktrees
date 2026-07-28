from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .common import AgentWorktreesError, read_json, write_json
from .security import safe_repository_path


@dataclass(frozen=True)
class ModelConfig:
    model: str
    audit_effort: str
    parity_effort: str
    checkpoint_effort: str


@dataclass(frozen=True)
class ProjectConfig:
    remote: str
    worktree_root: str
    lanes: tuple[str, ...]
    auto_checkpoint: bool
    validation_commands: tuple[tuple[str, ...], ...]
    codex: ModelConfig
    claude: ModelConfig


DEFAULT_CONFIG = {
    "version": 1,
    "remote": "origin",
    "worktreeRoot": ".codex/worktrees",
    "lanes": ["a", "b", "c", "d", "e"],
    "autoCheckpoint": True,
    "validationCommands": [],
    "models": {
        "codex": {
            "model": "gpt-5.6-sol",
            "auditEffort": "high",
            "parityEffort": "medium",
            "checkpointEffort": "medium",
        },
        "claude": {
            "model": "fable",
            "auditEffort": "high",
            "parityEffort": "medium",
            "checkpointEffort": "medium",
        },
    },
}


def config_path(root: Path) -> Path:
    return root / ".agent-worktrees/config.json"


def ensure_default_config(root: Path) -> Path:
    path = config_path(root)
    if not safe_repository_path(root, ".agent-worktrees/config.json"):
        raise AgentWorktreesError(".agent-worktrees must be a real directory inside the repository")
    if not path.exists():
        write_json(path, DEFAULT_CONFIG)
    return path


def _model(raw: object, name: str) -> ModelConfig:
    if not isinstance(raw, dict):
        raise AgentWorktreesError(f"models.{name} must be an object")
    try:
        return ModelConfig(
            model=str(raw["model"]),
            audit_effort=str(raw["auditEffort"]),
            parity_effort=str(raw["parityEffort"]),
            checkpoint_effort=str(raw["checkpointEffort"]),
        )
    except KeyError as error:
        raise AgentWorktreesError(f"models.{name} is missing {error.args[0]}") from error


def load_config(root: Path) -> ProjectConfig:
    if not safe_repository_path(root, ".agent-worktrees/config.json"):
        raise AgentWorktreesError(".agent-worktrees must be a real directory inside the repository")
    raw = read_json(config_path(root))
    if not isinstance(raw, dict):
        raise AgentWorktreesError(".agent-worktrees/config.json must contain an object")
    models = raw.get("models")
    if not isinstance(models, dict):
        raise AgentWorktreesError("config models must contain codex and claude")
    lanes_value = raw.get("lanes")
    if not isinstance(lanes_value, list) or not lanes_value:
        raise AgentWorktreesError("config lanes must be a non-empty array")
    lanes = tuple(str(value) for value in lanes_value)
    if any(len(lane) != 1 or not lane.isalpha() or not lane.islower() for lane in lanes):
        raise AgentWorktreesError("every lane must be one lowercase letter")
    commands_value = raw.get("validationCommands", [])
    if not isinstance(commands_value, list):
        raise AgentWorktreesError("validationCommands must be an array of argv arrays")
    commands: list[tuple[str, ...]] = []
    for command in commands_value:
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) for part in command
        ):
            raise AgentWorktreesError("each validation command must be a non-empty string array")
        commands.append(tuple(command))
    remote = str(raw.get("remote", "origin"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
        raise AgentWorktreesError("config remote must be a plain Git remote name")
    worktree_root = str(raw.get("worktreeRoot", ".codex/worktrees"))
    if worktree_root != ".codex/worktrees":
        raise AgentWorktreesError("config worktreeRoot must remain .codex/worktrees")
    return ProjectConfig(
        remote=remote,
        worktree_root=worktree_root,
        lanes=lanes,
        auto_checkpoint=bool(raw.get("autoCheckpoint", True)),
        validation_commands=tuple(commands),
        codex=_model(models.get("codex"), "codex"),
        claude=_model(models.get("claude"), "claude"),
    )
