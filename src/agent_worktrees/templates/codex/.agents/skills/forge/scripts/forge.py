#!/usr/bin/env python3
"""Durable coordinator state and external worker adapters for Forge."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


STATES = (
    "INTAKE",
    "SPECIFICATION",
    "TECHNICAL_REVIEW",
    "AWAITING_APPROVAL",
    "IMPLEMENTATION",
    "VERIFICATION",
    "ESCALATION",
    "COMPLETE",
)

TRANSITIONS = {
    "INTAKE": {"SPECIFICATION"},
    "SPECIFICATION": {"TECHNICAL_REVIEW"},
    "TECHNICAL_REVIEW": {"SPECIFICATION", "AWAITING_APPROVAL"},
    "AWAITING_APPROVAL": {"SPECIFICATION", "IMPLEMENTATION"},
    "IMPLEMENTATION": {"SPECIFICATION", "VERIFICATION", "ESCALATION"},
    "VERIFICATION": {"IMPLEMENTATION", "ESCALATION"},
    "ESCALATION": {"IMPLEMENTATION", "VERIFICATION"},
    "COMPLETE": set(),
}

FEATURE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WORKTREE_LETTERS = ("a", "b", "c", "d", "e")
TASK_OWNERS = ("terra", "kimi")
COMMON_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_REASONING_EFFORT = "high"
KIMI_REASONING_EFFORT = "xhigh"
COMPACTION_PERCENT = 40
OPENAI_CONTEXT_WINDOW = 272_000
OPENAI_COMPACT_TOKEN_LIMIT = 108_800
KIMI_CONTEXT_WINDOW = 262_144
KIMI_COMPACT_TOKEN_LIMIT = 104_858


class ForgeError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ForgeError(f"command failed: {shlex.join(command)}\n{detail}")
    return result


def git_output(arguments: list[str], cwd: Path | None = None) -> str:
    result = run(["git", *arguments], cwd=cwd, check=True)
    return result.stdout.strip()


def repository_context() -> tuple[Path, Path]:
    root = Path(git_output(["rev-parse", "--show-toplevel"])).resolve()
    common_raw = git_output(["rev-parse", "--git-common-dir"], cwd=root)
    common = Path(common_raw)
    if not common.is_absolute():
        common = (root / common).resolve()
    main_root = common.parent if common.name == ".git" else root
    return main_root.resolve(), common.resolve()


def state_parent() -> Path:
    override = os.environ.get("FORGE_STATE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    main_root, _ = repository_context()
    return main_root / ".forge-state"


def feature_dir(feature: str) -> Path:
    validate_feature(feature)
    return state_parent() / feature


def state_path(feature: str) -> Path:
    return feature_dir(feature) / "state.json"


def validate_feature(feature: str) -> None:
    if not FEATURE_RE.fullmatch(feature):
        raise ForgeError("feature id must be 1-64 lowercase letters, digits, or hyphens")


def validate_task(task: str) -> None:
    if not TASK_RE.fullmatch(task):
        raise ForgeError("task id must be 1-64 letters, digits, dots, underscores, or hyphens")


def validate_effort(effort: object) -> str:
    if not isinstance(effort, str) or effort not in COMMON_REASONING_EFFORTS:
        raise ForgeError(
            "Forge effort must be one of: " + ", ".join(COMMON_REASONING_EFFORTS)
        )
    return effort


def configured_effort(config: dict[str, Any]) -> str:
    return validate_effort(
        os.environ.get("FORGE_REASONING_EFFORT")
        or config.get("reasoning_effort", DEFAULT_REASONING_EFFORT)
    )


def state_effort(state: dict[str, Any]) -> str:
    return validate_effort(state.get("reasoning_effort", DEFAULT_REASONING_EFFORT))


def compaction_prompt_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "compaction-prompt.md"


def fable_settings_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "fable-settings.json"


def codex_compaction_arguments(context_window: int, token_limit: int) -> list[str]:
    return [
        "-c",
        f"model_context_window={context_window}",
        "-c",
        f"model_auto_compact_token_limit={token_limit}",
        "-c",
        'model_auto_compact_token_limit_scope="total"',
        "-c",
        f'experimental_compact_prompt_file="{compaction_prompt_path()}"',
    ]


def forge_worker_environment(feature: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["FORGE_ACTIVE_FEATURE"] = feature
    return environment


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_state(feature: str) -> dict[str, Any]:
    path = state_path(feature)
    if not path.exists():
        raise ForgeError(f"unknown Forge feature: {feature}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ForgeError(f"cannot read Forge state: {error}") from error
    state["schema_version"] = 4
    state.setdefault("required_validations", [])
    state.setdefault("advice", [])
    state.setdefault("fable_runs", [])
    state.setdefault("spec_dialogue", [])
    if not state["spec_dialogue"]:
        if state.get("request_path"):
            state["spec_dialogue"].append(
                {"role": "user", "path": state["request_path"], "kind": "initial-request"}
            )
        for fable_run in state["fable_runs"]:
            if fable_run.get("user_reply"):
                state["spec_dialogue"].append(
                    {"role": "user", "path": fable_run["user_reply"], "kind": "user-reply"}
                )
            if fable_run.get("report"):
                state["spec_dialogue"].append(
                    {"role": "assistant", "path": fable_run["report"], "kind": "fable-response"}
                )
    state.setdefault("decision_log", [])
    state.setdefault("coordinator_session_ids", [])
    state.setdefault("reasoning_effort", DEFAULT_REASONING_EFFORT)
    state_effort(state)
    state.setdefault("memory", {"path": str(memory_snapshot_path(feature)), "refreshed_at": None})
    state.setdefault("escalation_epoch", 0)
    state.setdefault("implementation_head", None)
    state.setdefault("final_commit", None)
    state.pop("worktrees", None)
    state.pop("commits", None)
    state.pop("reviews", None)
    state.pop("review_baseline_count", None)
    state.pop("acceptance", None)
    state.setdefault("workers", {}).setdefault("fable", {"session_id": None, "reports": []})
    state["workers"]["fable"].setdefault(
        "awaiting_user_reply",
        bool(
            state["workers"]["fable"].get("session_id")
            and state["workers"]["fable"].get("reports")
            and state.get("state") in {"SPECIFICATION", "TECHNICAL_REVIEW"}
        ),
    )
    state["workers"]["fable"].setdefault("user_replies", [])
    if "workspace" not in state:
        state["workspace"] = detect_workspace()
    workspace = state["workspace"]
    for key in ("letter", "path", "branch", "baseline_head"):
        if not workspace.get(key):
            detected = detect_workspace()
            workspace.update(detected)
            break
    for task in state.get("tasks", {}).values():
        task.setdefault("depends_on", [])
        task.setdefault("claim_baseline", None)
        task.setdefault("runner_report", None)
        task["worktree"] = workspace["letter"]
        task.pop("synced_dependencies", None)
        task.pop("synced_dependency_heads", None)
        task.pop("implementation_base", None)
        task.pop("integrated", None)
        task.pop("integration_commit", None)
        task.pop("runtime_fallback_reason", None)
    for requirement in state.get("required_validations", []):
        requirement.setdefault("tasks", [])
        requirement["scope"] = "feature"
    for validation in state.get("validations", []):
        validation["scope"] = "feature"
    return state


def record_text(path_value: object) -> str:
    if not isinstance(path_value, str):
        return "[missing record path]"
    path = Path(path_value)
    if not path.is_file():
        return f"[missing record: {path}]"
    return path.read_text(encoding="utf-8")


def memory_snapshot_path(feature: str) -> Path:
    return feature_dir(feature) / "memory" / "handoff.md"


def memory_snapshot_text(state: dict[str, Any]) -> str:
    spec = state.get("specification", {})
    spec_text = record_text(spec.get("path")) if spec.get("path") else "[No specification recorded yet.]"
    lines = [
        "# Forge Canonical Memory",
        "",
        "This file is generated from durable Forge state. Treat the specification and visible dialogue as verbatim records.",
        "",
        "## Run",
        "",
        f"- Feature: {state.get('feature_id')}",
        f"- Phase: {state.get('state')}",
        f"- Reasoning effort: {state_effort(state)} (Kimi remains {KIMI_REASONING_EFFORT})",
        f"- Worktree: {state.get('workspace', {}).get('letter')} ({state.get('workspace', {}).get('path')})",
        f"- Branch: {state.get('workspace', {}).get('branch')}",
        "",
        "## Exact Current Specification",
        "",
        "<exact_specification>",
        spec_text,
        "</exact_specification>",
        "",
        "## Exact Visible Specification Dialogue",
        "",
    ]
    dialogue = state.get("spec_dialogue", [])
    if not dialogue:
        lines.append("[No visible user/Fable exchange recorded yet.]")
    for index, item in enumerate(dialogue, start=1):
        role = str(item.get("role", "unknown")).upper()
        lines.extend(
            [
                f"### Turn {index}: {role}",
                "",
                f"<exact_{role.lower()}_message>",
                record_text(item.get("path")),
                f"</exact_{role.lower()}_message>",
                "",
            ]
        )
    lines.extend(["## Implementation Status", ""])
    tasks = state.get("tasks", {})
    if not tasks:
        lines.append("- No implementation tasks recorded.")
    for task_id, task in tasks.items():
        lines.append(
            f"- {task_id}: owner={task.get('owner')} status={task.get('status')} "
            f"report={task.get('report') or task.get('runner_report') or '-'}"
        )
        completion_report = task.get("report") if task.get("status") == "completed" else None
        if completion_report:
            lines.extend(
                [
                    "",
                    f"<completed_task_report task=\"{task_id}\">",
                    record_text(completion_report),
                    "</completed_task_report>",
                    "",
                ]
            )
    lines.extend(["", "## Required Validations and Latest Evidence", ""])
    latest = latest_validations(state) if "validations" in state else {}
    requirements = state.get("required_validations", [])
    if not requirements:
        lines.append("- No validation requirements recorded.")
    for requirement in requirements:
        result = latest.get((requirement["name"], requirement.get("scope", "feature")))
        if result:
            lines.append(
                f"- {requirement['name']}: {result.get('status')} at {result.get('workspace_head')} "
                f"evidence={result.get('evidence') or '-'}"
            )
        else:
            lines.append(f"- {requirement['name']}: not run")
    lines.extend(["", "## Decision and Deviation Audit Trail", ""])
    decisions = state.get("decision_log", [])
    if not decisions:
        lines.append("- No implementation deviation or escalation decision recorded.")
    for item in decisions:
        lines.extend(
            [
                f"### {item.get('at')} — {item.get('kind')} — {item.get('task') or 'feature'}",
                "",
                f"Summary: {item.get('summary')}",
                "",
                f"Reason: {item.get('reason')}",
                "",
            ]
        )
        if item.get("source"):
            lines.extend(
                [
                    "<decision_source>",
                    record_text(item.get("source")),
                    "</decision_source>",
                    "",
                ]
            )
    lines.extend(
        [
            "## Continuation Rules",
            "",
            "- Continue from the recorded phase; do not restart completed work.",
            "- The exact specification controls unless a later audit-trail entry explicitly records and explains a deviation.",
            "- Preserve the exact specification and exact visible user/Fable dialogue through every later compaction.",
            "- Load the current task packet and latest applicable Sol diagnosis before implementation continues.",
            "",
        ]
    )
    return "\n".join(lines)


def refresh_memory_snapshot(feature: str, state: dict[str, Any]) -> Path:
    path = memory_snapshot_path(feature)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(memory_snapshot_text(state), encoding="utf-8")
    state.setdefault("memory", {})["path"] = str(path)
    state["memory"]["refreshed_at"] = now()
    return path


def save_state(feature: str, state: dict[str, Any], event: str | None = None) -> None:
    state["updated_at"] = now()
    if event:
        state.setdefault("history", []).append({"at": state["updated_at"], "event": event})
    refresh_memory_snapshot(feature, state)
    atomic_json(state_path(feature), state)


def copy_record(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ForgeError(f"record file not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def parse_worktrees() -> dict[Path, str | None]:
    output = git_output(["worktree", "list", "--porcelain"])
    records: dict[Path, str | None] = {}
    current_path: Path | None = None
    current_branch: str | None = None
    for line in output.splitlines() + [""]:
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree ")).resolve()
            current_branch = None
        elif line.startswith("branch "):
            current_branch = line.removeprefix("branch refs/heads/")
        elif not line and current_path:
            records[current_path] = current_branch
            current_path = None
    return records


def expected_worktree_specs() -> dict[str, dict[str, Any]]:
    main_root, _ = repository_context()
    parent = Path(os.environ.get("FORGE_WORKTREE_ROOT", main_root / ".codex" / "worktrees"))
    configured = load_config().get("worktrees", {})
    if configured and not isinstance(configured, dict):
        raise ForgeError("Forge config worktrees must be an object of letter-to-path entries")
    specs = {
        letter: {"path": (parent / letter).expanduser().resolve(), "branch": letter}
        for letter in WORKTREE_LETTERS
    }
    for letter, value in configured.items():
        if letter not in WORKTREE_LETTERS:
            raise ForgeError(f"invalid configured worktree entry: {letter}")
        if isinstance(value, str) and value:
            specs[letter]["path"] = Path(value).expanduser().resolve()
            continue
        if not isinstance(value, dict) or not isinstance(value.get("path"), str) or not value["path"]:
            raise ForgeError(f"invalid configured worktree entry: {letter}")
        branch = value.get("branch", letter)
        if not isinstance(branch, str) or not branch:
            raise ForgeError(f"invalid configured worktree branch: {letter}")
        specs[letter] = {"path": Path(value["path"]).expanduser().resolve(), "branch": branch}
    return specs


def worktree_status(letter: str) -> dict[str, Any]:
    specs = expected_worktree_specs()
    if letter not in specs:
        raise ForgeError(f"unknown worktree: {letter}")
    path = specs[letter]["path"]
    expected_branch = specs[letter]["branch"]
    records = parse_worktrees()
    exists = path in records and path.is_dir()
    branch = records.get(path)
    dirty = None
    if exists:
        dirty = bool(git_output(["status", "--porcelain"], cwd=path))
    branch_matches = branch == expected_branch
    return {
        "path": str(path),
        "exists": exists,
        "branch": branch,
        "expected_branch": expected_branch,
        "branch_matches": branch_matches,
        "dirty": dirty,
    }


def detect_workspace() -> dict[str, Any]:
    root = Path(git_output(["rev-parse", "--show-toplevel"])).resolve()
    branch = git_output(["branch", "--show-current"], cwd=root)
    if branch not in WORKTREE_LETTERS:
        raise ForgeError(
            "Forge must be invoked from permanent worktree a, b, c, d, or e on its matching branch"
        )
    expected = expected_worktree_specs()[branch]
    if root != expected["path"] or expected["branch"] != branch:
        raise ForgeError(
            f"Forge worktree mismatch: branch {branch} must run from {expected['path']}, got {root}"
        )
    status = worktree_status(branch)
    if not status["exists"] or not status["branch_matches"]:
        raise ForgeError(f"Forge worktree is not safe: {json.dumps(status, sort_keys=True)}")
    return {
        "letter": branch,
        "path": str(root),
        "branch": branch,
        "baseline_head": git_output(["rev-parse", "HEAD"], cwd=root),
    }


def state_workspace(state: dict[str, Any]) -> dict[str, Any]:
    workspace = state.get("workspace")
    if not isinstance(workspace, dict):
        raise ForgeError("Forge state has no invocation workspace")
    active = detect_workspace()
    if active["path"] != workspace.get("path") or active["branch"] != workspace.get("branch"):
        raise ForgeError(
            "resume this Forge run from its original worktree "
            f"{workspace.get('path')} on branch {workspace.get('branch')}"
        )
    return workspace


def workspace_path(state: dict[str, Any]) -> Path:
    return Path(state_workspace(state)["path"])


def workspace_head(state: dict[str, Any]) -> str:
    return git_output(["rev-parse", "HEAD"], cwd=workspace_path(state))


def lock_path(letter: str) -> Path:
    return state_parent() / "_locks" / f"{letter}.json"


def read_lock(letter: str) -> dict[str, Any] | None:
    path = lock_path(letter)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ForgeError(f"cannot read global worktree lock {path}: {error}") from error
    if not isinstance(value, dict):
        raise ForgeError(f"invalid global worktree lock: {path}")
    return value


def acquire_lock(feature: str, task: str, letter: str) -> None:
    path = lock_path(letter)
    existing = read_lock(letter)
    if existing:
        if existing.get("feature") == feature and existing.get("task") == task:
            return
        raise ForgeError(
            f"worktree {letter} is globally claimed by {existing.get('feature')}/{existing.get('task')}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"feature": feature, "task": task, "worktree": letter, "claimed_at": now(), "pid": os.getpid()}
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        existing = read_lock(letter) or {}
        raise ForgeError(
            f"worktree {letter} is globally claimed by {existing.get('feature')}/{existing.get('task')}"
        ) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def release_lock(feature: str, task: str, letter: str) -> None:
    path = lock_path(letter)
    existing = read_lock(letter)
    if not existing:
        return
    if existing.get("feature") != feature or existing.get("task") != task:
        raise ForgeError(f"refusing to release another Forge run's worktree {letter} lock")
    path.unlink()


def model_run_lock_path(feature: str) -> Path:
    return feature_dir(feature) / "locks" / "model-run.json"


def acquire_model_run_lock(feature: str, actor: str) -> None:
    path = model_run_lock_path(feature)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ForgeError(f"cannot read Forge model-run lock {path}: {error}") from error
        raise ForgeError(f"Forge model run already active for {feature}: {existing.get('actor', 'unknown')}")
    payload = {"feature": feature, "actor": actor, "started_at": now(), "pid": os.getpid()}
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise ForgeError(f"Forge model run already active for {feature}") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def release_model_run_lock(feature: str, actor: str) -> None:
    path = model_run_lock_path(feature)
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ForgeError(f"cannot read Forge model-run lock {path}: {error}") from error
    if existing.get("actor") != actor:
        raise ForgeError("refusing to release another Forge model-run lock")
    path.unlink()


def load_config() -> dict[str, Any]:
    path = Path(os.environ.get("FORGE_CONFIG", Path.home() / ".config" / "forge" / "config.json"))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ForgeError(f"invalid Forge config {path}: {error}") from error
    if not isinstance(data, dict):
        raise ForgeError(f"Forge config must be a JSON object: {path}")
    return data


def kimi_home(config: dict[str, Any]) -> Path:
    value = os.environ.get("FORGE_KIMI_CODEX_HOME") or config.get("kimi_codex_home")
    return Path(value or Path.home() / ".codex-kimi").expanduser().resolve()


def kimi_command() -> list[str]:
    return [str(Path(__file__).with_name("forge-kimi-worker.sh"))]


def nearest_writable_parent(path: Path) -> bool:
    candidate = path.expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK)


def kimi_profile_status(home: Path) -> dict[str, object]:
    config_path = home / "config.toml"
    status: dict[str, object] = {
        "configured": False,
        "model": None,
        "provider": None,
        "base_url": None,
        "proxy_listening": None,
        "cc_switch_provider": None,
        "error": None,
    }
    if not config_path.is_file():
        status["error"] = f"missing {config_path}"
        return status

    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        status["error"] = f"invalid {config_path}: {error}"
        return status

    model = data.get("model")
    provider_id = data.get("model_provider")
    providers = data.get("model_providers")
    provider = providers.get(provider_id) if isinstance(providers, dict) and isinstance(provider_id, str) else None
    base_url = provider.get("base_url") if isinstance(provider, dict) else None
    status.update({"model": model, "provider": provider_id, "base_url": base_url})

    if not isinstance(model, str) or not re.search(r"kimi|moonshot", model, re.IGNORECASE):
        status["error"] = "model is not Kimi/Moonshot"
        return status
    if not isinstance(base_url, str):
        status["error"] = "model provider has no base_url"
        return status

    parsed = urlparse(base_url)
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        port = parsed.port
        if port is None:
            status["error"] = "local proxy URL has no port"
            status["proxy_listening"] = False
            return status
        lsof = shutil.which("lsof")
        if not lsof:
            status["error"] = "cannot check local proxy because lsof is unavailable"
            return status
        listener = run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
        if listener.returncode != 0:
            status["error"] = f"local proxy is not listening on {parsed.hostname}:{port}"
            status["proxy_listening"] = False
            return status
        status["proxy_listening"] = True

        database = Path.home() / ".cc-switch" / "cc-switch.db"
        if database.is_file():
            try:
                connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                try:
                    row = connection.execute(
                        "SELECT name FROM providers WHERE app_type = 'codex' AND is_current = 1 LIMIT 1"
                    ).fetchone()
                finally:
                    connection.close()
            except sqlite3.Error as error:
                status["error"] = f"cannot read CC Switch provider state: {error}"
                return status
            if row and isinstance(row[0], str):
                status["cc_switch_provider"] = row[0]
                if not re.search(r"kimi|moonshot", row[0], re.IGNORECASE):
                    status["error"] = f"CC Switch current Codex provider is {row[0]}, not Kimi"
                    return status

    status["configured"] = True
    return status


def command_doctor(args: argparse.Namespace) -> None:
    config = load_config()
    effort = configured_effort(config)
    commands = {name: shutil.which(name) for name in ("git", "python3", "codex", "claude")}
    workspace = detect_workspace()
    status = worktree_status(workspace["letter"])
    home = kimi_home(config)
    cc_switch_paths = [
        Path("/Applications/CC Switch.app"),
        Path.home() / "Applications" / "CC Switch.app",
    ]
    kimi_status = kimi_profile_status(home)
    report = {
        "commands": commands,
        "state_parent": str(state_parent()),
        "state_parent_writable": nearest_writable_parent(state_parent()),
        "workspace": status,
        "global_lock": read_lock(workspace["letter"]),
        "fable_model": os.environ.get("FORGE_FABLE_MODEL") or config.get("fable_model", "fable"),
        "reasoning_effort": effort,
        "fable_effort": effort,
        "sol_effort": effort,
        "terra_effort": effort,
        "compaction_percent": COMPACTION_PERCENT,
        "openai_compact_token_limit": OPENAI_COMPACT_TOKEN_LIMIT,
        "kimi_codex_home": str(home),
        "kimi_configured": kimi_status["configured"],
        "kimi_status": kimi_status,
        "kimi_reasoning_effort": KIMI_REASONING_EFFORT,
        "cc_switch_installed": any(path.exists() for path in cc_switch_paths),
    }
    report["ready"] = (
        report["state_parent_writable"]
        and all(commands.values())
        and status["exists"]
        and status["branch_matches"]
        and not report["global_lock"]
    )
    report["ready_for_new_feature"] = report["ready"] and status["dirty"] is False
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Forge state: {report['state_parent']}")
    for name, location in commands.items():
        print(f"command {name}: {location or 'MISSING'}")
    workspace_ready = status["exists"] and status["branch_matches"]
    print(
        f"workspace {workspace['letter']}: {'ready' if workspace_ready else 'BLOCKED'}; "
        f"branch={status['branch']}; dirty={status['dirty']}; path={status['path']}"
    )
    if report["global_lock"]:
        print(f"  global lock: {json.dumps(report['global_lock'], sort_keys=True)}")
    print(f"CC Switch installed: {report['cc_switch_installed']}")
    print(f"Fable route: model={report['fable_model']}; effort={report['fable_effort']}")
    print(f"Sol route: model=gpt-5.6-sol; effort={report['sol_effort']}")
    print(f"Terra route: model=gpt-5.6-terra; effort={report['terra_effort']}")
    print(
        f"automatic compaction: {report['compaction_percent']}% "
        f"({report['openai_compact_token_limit']} GPT-5.6 tokens)"
    )
    print(f"Kimi configured: {report['kimi_configured']} ({report['kimi_codex_home']})")
    print(
        f"Kimi route: model={kimi_status['model']}; effort={report['kimi_reasoning_effort']}; "
        f"provider={kimi_status['provider']}; "
        f"base_url={kimi_status['base_url']}; proxy_listening={kimi_status['proxy_listening']}; "
        f"cc_switch_provider={kimi_status['cc_switch_provider']}"
    )
    if kimi_status["error"]:
        print(f"  Kimi error: {kimi_status['error']}")
        print("  Kimi frontend tasks will remain blocked until the Kimi runtime is available.")
    print(f"ready: {report['ready']}")
    print(f"ready for new feature: {report['ready_for_new_feature']}")


def command_init(args: argparse.Namespace) -> None:
    validate_feature(args.feature)
    directory = feature_dir(args.feature)
    if directory.exists():
        raise ForgeError(f"Forge feature already exists: {args.feature}")
    main_root, common = repository_context()
    workspace = detect_workspace()
    status = worktree_status(workspace["letter"])
    if status["dirty"] is not False:
        raise ForgeError("start Forge from a clean invocation worktree")
    if read_lock(workspace["letter"]):
        raise ForgeError(f"worktree {workspace['letter']} is already claimed by another Forge run")
    directory.mkdir(parents=True)
    for name in ("tasks", "reports", "evidence", "findings", "locks", "memory"):
        (directory / name).mkdir()
    request_target = directory / "request.md"
    if args.request_file:
        copy_record(Path(args.request_file), request_target)
    else:
        request_target.write_text(args.request or "", encoding="utf-8")
    effort = validate_effort(args.effort or configured_effort(load_config()))
    coordinator_session = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CLAUDE_SESSION_ID")
    state = {
        "schema_version": 4,
        "feature_id": args.feature,
        "title": args.title,
        "state": "INTAKE",
        "created_at": now(),
        "updated_at": now(),
        "repository": {"main_root": str(main_root), "git_common_dir": str(common)},
        "workspace": workspace,
        "request_path": str(request_target),
        "reasoning_effort": effort,
        "coordinator_session_ids": [coordinator_session] if coordinator_session else [],
        "specification": {"version": 0, "path": None, "approved": False, "approved_at": None, "approved_by": None},
        "tasks": {},
        "workers": {
            "fable": {
                "session_id": None,
                "reports": [],
                "awaiting_user_reply": False,
                "user_replies": [],
            },
            "kimi": {"reports": []},
            "terra": {"reports": []},
            "sol": {"reports": []},
        },
        "attempts": {},
        "validations": [],
        "required_validations": [],
        "findings": [],
        "advice": [],
        "fable_runs": [],
        "spec_dialogue": [
            {"role": "user", "path": str(request_target), "kind": "initial-request"}
        ],
        "decision_log": [],
        "memory": {"path": str(memory_snapshot_path(args.feature)), "refreshed_at": None},
        "escalation_epoch": 0,
        "implementation_head": None,
        "final_commit": None,
        "history": [{"at": now(), "event": "feature initialized"}],
    }
    refresh_memory_snapshot(args.feature, state)
    atomic_json(state_path(args.feature), state)
    print(directory)


def command_show(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    workspace = state_workspace(state)
    if args.json:
        print(json.dumps(state, indent=2, sort_keys=True))
        return
    print(f"{state['feature_id']}: {state['title']}")
    print(f"state: {state['state']}")
    print(f"reasoning effort: {state_effort(state)}; Kimi={KIMI_REASONING_EFFORT}")
    print(f"workspace: {workspace['letter']} ({workspace['path']}) branch={workspace['branch']}")
    spec = state["specification"]
    print(f"specification: v{spec['version']} approved={spec['approved']}")
    print("tasks:")
    if not state["tasks"]:
        print("  (none)")
    for task_id, task in state["tasks"].items():
        print(
            f"  {task_id}: owner={task['owner']} "
            f"status={task['status']} "
            f"depends_on={','.join(task.get('depends_on', [])) or '-'}"
        )
    print("required validations:")
    for requirement in state["required_validations"]:
        print(
            f"  {requirement['name']}: scope={requirement['scope']} "
            f"evidence_required={requirement['evidence_required']}"
        )
    print("validations:")
    for validation in state["validations"][-10:]:
        print(f"  {validation['status']}: {validation['name']} ({validation['command']})")
    open_blocking = [item for item in state["findings"] if item["status"] == "open" and item["severity"] == "blocking"]
    print(f"open blocking findings: {len(open_blocking)}")
    print(f"fable awaiting user reply: {state['workers']['fable']['awaiting_user_reply']}")
    print(f"canonical memory: {state.get('memory', {}).get('path')}")


def command_session_bind(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    state_workspace(state)
    session_id = args.session_id or os.environ.get("CODEX_THREAD_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        raise ForgeError("cannot identify the current agent session; pass --session-id")
    sessions = state.setdefault("coordinator_session_ids", [])
    if session_id not in sessions:
        sessions.append(session_id)
    save_state(args.feature, state, f"coordinator session bound: {session_id}")
    print(session_id)


def command_decision_add(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] not in {"IMPLEMENTATION", "ESCALATION", "VERIFICATION"}:
        raise ForgeError("implementation decisions may be recorded only after approval")
    if args.task and args.task not in state.get("tasks", {}):
        raise ForgeError(f"unknown task: {args.task}")
    source = None
    if args.source_file:
        source = feature_dir(args.feature) / "reports" / (
            f"decision-{len(state.get('decision_log', [])) + 1:03d}.md"
        )
        copy_record(Path(args.source_file), source)
    state.setdefault("decision_log", []).append(
        {
            "kind": args.kind,
            "task": args.task,
            "summary": args.summary,
            "reason": args.reason,
            "source": str(source) if source else None,
            "at": now(),
        }
    )
    save_state(args.feature, state, f"{args.kind} recorded for {args.task or 'feature'}")
    print("recorded")


def latest_validations(state: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for validation in state["validations"]:
        latest[(validation["name"], validation.get("scope", "feature"))] = validation
    return latest


def validation_gate_errors(state: dict[str, Any]) -> list[str]:
    latest = latest_validations(state)
    errors: list[str] = []
    for requirement in state["required_validations"]:
        validation = latest.get((requirement["name"], requirement["scope"]))
        if not validation:
            errors.append(f"missing {requirement['scope']} validation {requirement['name']}")
            continue
        if validation["status"] != "pass":
            errors.append(
                f"{requirement['scope']} validation {requirement['name']} is {validation['status']}"
            )
        else:
            if validation.get("workspace_head") != workspace_head(state):
                errors.append(f"validation {requirement['name']} is stale for the current branch HEAD")
            required_tasks = set(requirement.get("tasks", []))
            if not required_tasks.issubset(set(validation.get("tasks", []))):
                errors.append(f"validation {requirement['name']} does not cover every required task")
        if requirement["evidence_required"] and not validation.get("evidence"):
            errors.append(f"validation {requirement['name']} has no evidence")
    return errors


def apply_transition(feature: str, state: dict[str, Any], target: str, note: str) -> None:
    current = state["state"]
    if target not in STATES:
        raise ForgeError(f"unknown state: {target}")
    if target not in TRANSITIONS[current]:
        raise ForgeError(f"invalid transition: {current} -> {target}")
    if target == "COMPLETE":
        raise ForgeError("COMPLETE is reachable only through finalize")
    if target == "ESCALATION" and current != "ESCALATION":
        state["escalation_epoch"] = state.get("escalation_epoch", 0) + 1
    if current == "ESCALATION" and target in {"IMPLEMENTATION", "VERIFICATION"}:
        diagnosis = next(
            (
                item
                for item in reversed(state.get("advice", []))
                if item.get("purpose") == "diagnosis"
            ),
            None,
        )
        if not diagnosis or diagnosis.get("escalation_epoch") != state.get("escalation_epoch"):
            raise ForgeError("run a Sol diagnosis for the current escalation before resuming")
    spec = state["specification"]
    if current == "SPECIFICATION" and target == "TECHNICAL_REVIEW":
        if spec["version"] < 1 or not spec.get("path"):
            raise ForgeError("technical review requires a recorded specification")
    if current == "TECHNICAL_REVIEW" and target == "AWAITING_APPROVAL":
        if not state["tasks"]:
            raise ForgeError("approval requires at least one task packet")
        if not state["required_validations"]:
            raise ForgeError("approval requires explicit validation requirements")
    if target == "IMPLEMENTATION" and not state["specification"]["approved"]:
        raise ForgeError("implementation requires an explicitly approved specification")
    if current == "IMPLEMENTATION" and target == "VERIFICATION":
        incomplete = [
            task_id
            for task_id, task in state["tasks"].items()
            if task["status"] != "completed" or not task.get("report")
        ]
        if incomplete:
            raise ForgeError(f"implementation tasks are incomplete: {', '.join(incomplete)}")
        path = workspace_path(state)
        if git_output(["status", "--porcelain"], cwd=path):
            raise ForgeError(
                "commit the completed implementation on the invocation branch before starting verification"
            )
        head = workspace_head(state)
        if head == state["workspace"]["baseline_head"]:
            raise ForgeError("implementation did not create a commit after the Forge baseline")
        state["implementation_head"] = head
    state["state"] = target
    save_state(feature, state, f"transition {current} -> {target}: {note}")


def command_transition(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    apply_transition(args.feature, state, args.target, args.note or "")
    print(args.target)


def command_spec_set(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] not in {"SPECIFICATION", "TECHNICAL_REVIEW"}:
        raise ForgeError("specifications may be recorded only in SPECIFICATION or TECHNICAL_REVIEW")
    if state["state"] == "TECHNICAL_REVIEW" and (state["tasks"] or state["required_validations"]):
        raise ForgeError(
            "record the reconciled specification before defining technical-review tasks or validations"
        )
    if args.version < 1:
        raise ForgeError("specification version must be at least 1")
    if args.version <= state["specification"]["version"]:
        raise ForgeError("specification version must increase")
    destination = feature_dir(args.feature) / f"spec-v{args.version}.md"
    copy_record(Path(args.file), destination)
    state["specification"] = {
        "version": args.version,
        "path": str(destination),
        "approved": False,
        "approved_at": None,
        "approved_by": None,
    }
    state["workers"]["fable"]["awaiting_user_reply"] = False
    save_state(args.feature, state, f"specification v{args.version} recorded")
    print(destination)


def command_approve(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "AWAITING_APPROVAL":
        raise ForgeError("approval is accepted only in AWAITING_APPROVAL")
    if state["specification"]["version"] != args.version:
        raise ForgeError("approval version does not match the current specification")
    if args.version < 1 or not state["specification"].get("path"):
        raise ForgeError("an empty specification cannot be approved")
    state["specification"].update({"approved": True, "approved_at": now(), "approved_by": args.approved_by})
    apply_transition(args.feature, state, "IMPLEMENTATION", f"specification v{args.version} approved by {args.approved_by}")
    print("IMPLEMENTATION")


def command_task_add(args: argparse.Namespace) -> None:
    validate_task(args.task)
    state = load_state(args.feature)
    if state["state"] != "TECHNICAL_REVIEW":
        raise ForgeError("tasks may be added only during technical review before approval")
    if args.task in state["tasks"]:
        raise ForgeError(f"task already exists: {args.task}")
    workspace = state_workspace(state)
    dependencies = list(dict.fromkeys(args.depends_on or []))
    unknown_dependencies = [task_id for task_id in dependencies if task_id not in state["tasks"]]
    if unknown_dependencies:
        raise ForgeError(f"dependencies must already exist: {', '.join(unknown_dependencies)}")
    if args.task in dependencies:
        raise ForgeError("a task cannot depend on itself")
    packet = feature_dir(args.feature) / "tasks" / f"{args.task}.md"
    copy_record(Path(args.packet_file), packet)
    state["tasks"][args.task] = {
        "owner": args.owner,
        "worktree": workspace["letter"],
        "depends_on": dependencies,
        "claim_baseline": None,
        "runner_report": None,
        "runtime_worker": None,
        "packet": str(packet),
        "status": "pending",
        "claimed_at": None,
        "released_at": None,
        "report": None,
        "commit": None,
    }
    save_state(args.feature, state, f"task {args.task} assigned to {args.owner} in workspace {workspace['letter']}")
    print(packet)


def ensure_claimable(feature: str, state: dict[str, Any], task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if task_id not in state["tasks"]:
        raise ForgeError(f"unknown task: {task_id}")
    task = state["tasks"][task_id]
    if task["status"] == "completed":
        raise ForgeError(f"task is already completed: {task_id}")
    incomplete_dependencies = [
        dependency
        for dependency in task.get("depends_on", [])
        if state["tasks"][dependency]["status"] != "completed"
    ]
    if incomplete_dependencies:
        raise ForgeError(f"task dependencies are incomplete: {', '.join(incomplete_dependencies)}")
    workspace = state_workspace(state)
    letter = workspace["letter"]
    status = worktree_status(letter)
    if not status["exists"] or not status["branch_matches"]:
        raise ForgeError(f"worktree {letter} is not safe: {json.dumps(status, sort_keys=True)}")
    global_claim = read_lock(letter)
    if global_claim and not (
        global_claim.get("feature") == feature and global_claim.get("task") == task_id
    ):
        raise ForgeError(
            f"worktree {letter} is globally claimed by {global_claim.get('feature')}/{global_claim.get('task')}"
        )
    return task, status


def ensure_dependencies_synced(state: dict[str, Any], task_id: str) -> None:
    task = state["tasks"][task_id]
    incomplete = [
        dependency
        for dependency in task.get("depends_on", [])
        if state["tasks"][dependency]["status"] != "completed"
    ]
    if incomplete:
        raise ForgeError(f"task dependencies are incomplete: {', '.join(incomplete)}")


def command_claim(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "IMPLEMENTATION":
        raise ForgeError("implementation tasks may be claimed only during implementation")
    task, _ = ensure_claimable(args.feature, state, args.task)
    letter = task["worktree"]
    acquire_lock(args.feature, args.task, letter)
    baseline = workspace_head(state)
    task.update(
        {
            "status": "in_progress",
            "claimed_at": now(),
            "claim_baseline": baseline,
        }
    )
    save_state(args.feature, state, f"task {args.task} claimed workspace {letter}")
    print(workspace_path(state))


def command_release(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if args.task not in state["tasks"]:
        raise ForgeError(f"unknown task: {args.task}")
    task = state["tasks"][args.task]
    letter = task["worktree"]
    global_claim = read_lock(letter)
    if not global_claim or global_claim.get("feature") != args.feature or global_claim.get("task") != args.task:
        raise ForgeError(f"task {args.task} does not own the global worktree {letter} lock")
    report = None
    if args.report_file:
        report = feature_dir(args.feature) / "reports" / f"{args.task}-{args.status}.md"
        copy_record(Path(args.report_file), report)
    commit = args.commit
    if args.status == "completed":
        ensure_dependencies_synced(state, args.task)
        if not args.report_file:
            raise ForgeError("completed tasks require a worker completion report")
        if task.get("runner_report") and Path(args.report_file).expanduser().resolve() != Path(task["runner_report"]).resolve():
            raise ForgeError("external worker completion must use the report generated by worker-run")
    task.update(
        {
            "status": args.status,
            "released_at": now(),
            "report": str(report) if report else task.get("report"),
            "commit": commit or task.get("commit"),
        }
    )
    release_lock(args.feature, args.task, letter)
    if args.status in {"failed", "blocked"}:
        state["state"] = "ESCALATION"
        state["escalation_epoch"] = state.get("escalation_epoch", 0) + 1
    save_state(args.feature, state, f"task {args.task} released as {args.status}")
    print(args.status)


def command_task_reopen(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "IMPLEMENTATION":
        raise ForgeError("tasks may be reopened only during implementation rework")
    if args.task not in state["tasks"]:
        raise ForgeError(f"unknown task: {args.task}")
    task = state["tasks"][args.task]
    if task["status"] not in {"completed", "failed", "blocked"}:
        raise ForgeError("only a completed, failed, or blocked task may be reopened")
    workspace = state_workspace(state)
    if read_lock(workspace["letter"]):
        raise ForgeError(f"worktree {workspace['letter']} is globally claimed")
    task.update(
        {
            "status": "pending",
            "claimed_at": None,
            "released_at": None,
            "report": None,
            "commit": None,
            "claim_baseline": None,
            "runner_report": None,
        }
    )
    save_state(args.feature, state, f"task {args.task} reopened for rework: {args.reason}")
    print("pending")


def fable_base_prompt(purpose: str) -> str:
    return (
        "You are Fable, the product and specification partner in Forge. Stay read-only and do not implement. "
        "Address the user directly; never address or describe a coordinator. "
        "Challenge assumptions, define the user outcome, scope, explicit exclusions, flows, states, edge cases, decisions, "
        "observable acceptance criteria, suggested task breakdown, and open questions. Ask the user when a product decision "
        "is material; do not silently choose on their behalf. Your complete response will be relayed to the user verbatim."
    )


def invoke_fable(
    feature: str,
    prompt_file: Path,
    purpose: str,
    resume: bool,
    input_kind: str | None = None,
) -> Path:
    state = load_state(feature)
    if purpose == "specification" and state["state"] not in {"SPECIFICATION", "TECHNICAL_REVIEW"}:
        raise ForgeError("Fable specification may run only during specification or technical review")
    if not prompt_file.is_file():
        raise ForgeError(f"Fable prompt file not found: {prompt_file}")
    config = load_config()
    model = os.environ.get("FORGE_FABLE_MODEL") or config.get("fable_model", "fable")
    session_id = state["workers"]["fable"].get("session_id")
    if resume and not session_id:
        raise ForgeError("Fable session has not been started")
    if (
        resume
        and input_kind != "forge_evidence"
        and not state["workers"]["fable"].get("awaiting_user_reply")
    ):
        raise ForgeError("Fable is not awaiting a user reply")
    if not resume:
        if session_id:
            raise ForgeError("Fable session already exists; use fable-resume")
        session_id = str(uuid.uuid4())
    prompt_text = prompt_file.read_text(encoding="utf-8")
    if resume and not prompt_text.strip():
        raise ForgeError("Fable user reply file is empty")
    packet_tag = input_kind or ("user_reply_verbatim" if resume else "forge_packet")
    memory = memory_snapshot_text(state)
    prompt = (
        f"{fable_base_prompt(purpose)}\n\n"
        "The durable Forge memory below is authoritative after any context compaction. Preserve the exact "
        "specification and visible user/Fable dialogue; do not repeat background exploration.\n\n"
        f"<forge_memory>\n{memory}\n</forge_memory>\n\n"
        f"<{packet_tag}>\n{prompt_text}\n</{packet_tag}>"
    )
    effort = state_effort(state)
    command = [
        "claude",
        "--setting-sources",
        "",
        "--settings",
        str(fable_settings_path()),
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--model",
        str(model),
        "--effort",
        effort,
        "--permission-mode",
        "plan",
        "--tools",
        "Read,Glob,Grep",
    ]
    if resume:
        command += ["--resume", str(session_id)]
    else:
        command += ["--session-id", str(session_id), "--name", f"forge-{feature}-fable"]
    command += ["--print", "--output-format", "text", prompt]
    worktree = workspace_path(state)
    letter = state["workspace"]["letter"]
    lock_task = f"__fable__{purpose}"
    model_actor = f"fable:{purpose}"
    acquire_model_run_lock(feature, model_actor)
    worktree_locked = False
    try:
        acquire_lock(feature, lock_task, letter)
        worktree_locked = True
        environment = forge_worker_environment(feature)
        environment["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(COMPACTION_PERCENT)
        result = run(command, cwd=worktree, env=environment, check=False)
        if result.returncode != 0:
            raise ForgeError(f"Fable worker failed:\n{result.stderr.strip() or result.stdout.strip()}")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sequence = len(state["workers"]["fable"]["reports"]) + 1
        report = feature_dir(feature) / "reports" / f"fable-{purpose}-{sequence:03d}-{stamp}.md"
        report.write_text(result.stdout, encoding="utf-8")
        user_reply: Path | None = None
        if resume and packet_tag == "user_reply_verbatim":
            user_reply = feature_dir(feature) / "reports" / f"user-reply-{sequence:03d}-{stamp}.md"
            user_reply.write_text(prompt_text, encoding="utf-8")
            state["workers"]["fable"]["user_replies"].append(str(user_reply))
            state.setdefault("spec_dialogue", []).append(
                {"role": "user", "path": str(user_reply), "kind": "user-reply"}
            )
        state.setdefault("spec_dialogue", []).append(
            {"role": "assistant", "path": str(report), "kind": "fable-response"}
        )
        state["workers"]["fable"]["session_id"] = session_id
        state["workers"]["fable"]["reports"].append(str(report))
        state["workers"]["fable"]["awaiting_user_reply"] = True
        state["fable_runs"].append(
            {
                "purpose": purpose,
                "report": str(report),
                "user_reply": str(user_reply) if user_reply else None,
                "specification_version": state["specification"]["version"],
                "workspace_head": workspace_head(state),
                "at": now(),
            }
        )
        save_state(feature, state, f"Fable {purpose} response recorded")
    finally:
        if worktree_locked:
            release_lock(feature, lock_task, letter)
        release_model_run_lock(feature, model_actor)
    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    return report


def command_fable_start(args: argparse.Namespace) -> None:
    invoke_fable(args.feature, Path(args.prompt_file), args.purpose, False)


def command_fable_resume(args: argparse.Namespace) -> None:
    if args.user_reply_file:
        invoke_fable(
            args.feature,
            Path(args.user_reply_file),
            args.purpose,
            True,
            "user_reply_verbatim",
        )
        return
    invoke_fable(args.feature, Path(args.evidence_file), args.purpose, True, "forge_evidence")


def terra_worker_command(
    worktree: Path,
    report: Path,
    model: str = "gpt-5.6-terra",
    effort: str = DEFAULT_REASONING_EFFORT,
) -> list[str]:
    effort = validate_effort(effort)
    return [
        "codex",
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        *codex_compaction_arguments(OPENAI_CONTEXT_WINDOW, OPENAI_COMPACT_TOKEN_LIMIT),
        "-C",
        str(worktree),
        "-s",
        "workspace-write",
        "-o",
        str(report),
        "-",
    ]


def worker_command(
    worker: str,
    worktree: Path,
    report: Path,
    effort: str = DEFAULT_REASONING_EFFORT,
) -> tuple[list[str], str, str | None]:
    effort = validate_effort(effort)
    if worker == "terra":
        return terra_worker_command(worktree, report, effort=effort), "terra", None
    if worker == "sol-advisor":
        command = [
            "codex",
            "exec",
            "-m",
            "gpt-5.6-sol",
            "-c",
            f'model_reasoning_effort="{effort}"',
            *codex_compaction_arguments(OPENAI_CONTEXT_WINDOW, OPENAI_COMPACT_TOKEN_LIMIT),
            "-C",
            str(worktree),
            "-s",
            "read-only",
            "-o",
            str(report),
            "-",
        ]
        return command, "sol-advisor", None
    if worker == "kimi":
        config = load_config()
        status = kimi_profile_status(kimi_home(config))
        if status["configured"]:
            return [*kimi_command(), str(worktree), str(report)], "kimi", None
        reason = str(status["error"] or "Kimi runtime is unavailable")
        raise ForgeError(f"Kimi is required for frontend work but is unavailable: {reason}")
    raise ForgeError(f"unknown worker: {worker}")


def command_worker_run(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "IMPLEMENTATION":
        raise ForgeError("Terra and Kimi may write only during implementation")
    task, _ = ensure_claimable(args.feature, state, args.task)
    ensure_dependencies_synced(state, args.task)
    if task["owner"] != args.worker:
        raise ForgeError(f"task {args.task} is assigned to {task['owner']}, not {args.worker}")
    attempt_key = f"{args.task}:{args.worker}"
    attempts = state["attempts"].get(attempt_key, 0)
    if attempts >= args.max_attempts:
        raise ForgeError(f"bounded attempt limit reached for {attempt_key}: {attempts}/{args.max_attempts}")
    packet = Path(task["packet"]).read_text(encoding="utf-8")
    worktree = workspace_path(state)
    report = feature_dir(args.feature) / "reports" / f"{args.task}-{args.worker}-attempt-{attempts + 1}.md"
    command, effective_worker, fallback_reason = worker_command(
        args.worker, worktree, report, state_effort(state)
    )
    diagnosis_text = ""
    latest_diagnosis = next(
        (
            item
            for item in reversed(state.get("advice", []))
            if item.get("purpose") == "diagnosis"
            and item.get("escalation_epoch") == state.get("escalation_epoch")
            and not item.get("applied_at")
        ),
        None,
    )
    if latest_diagnosis:
        diagnosis_path = Path(latest_diagnosis["report"])
        if not diagnosis_path.is_file():
            raise ForgeError(f"recorded Sol diagnosis is missing: {diagnosis_path}")
        diagnosis_text = (
            "\n\nApply the bounded read-only Sol diagnosis below. Verify the recommendation against the "
            "current checkout; Sol does not authorize scope expansion.\n\n"
            f"<sol_diagnosis>\n{diagnosis_path.read_text(encoding='utf-8')}\n</sol_diagnosis>"
        )
    memory = memory_snapshot_text(state)
    prompt = (
        "Execute only the bounded Forge task below in the current Forge worktree. Respect must-not-change boundaries. "
        "Read applicable AGENTS.md guidance. Do not expand scope, push, deploy, or perform billable/external actions. "
        "Run required validation and finish with the worker completion report format. If the approved specification "
        "cannot be followed exactly, stop and report the proposed deviation and reason; the coordinator must record it "
        "before work continues. The durable memory is authoritative after compaction.\n\n"
        + f"<forge_memory>\n{memory}\n</forge_memory>\n\n"
        + f"<forge_task>\n{packet}\n</forge_task>"
        + diagnosis_text
    )
    if args.dry_run:
        print(shlex.join(command))
        return
    model_actor = f"{args.worker}:{args.task}"
    acquire_model_run_lock(args.feature, model_actor)
    worktree_locked = False
    try:
        acquire_lock(args.feature, args.task, task["worktree"])
        worktree_locked = True
        if not task.get("claim_baseline"):
            baseline = git_output(["rev-parse", "HEAD"], cwd=worktree)
            task["claim_baseline"] = baseline
        task["runner_report"] = str(report)
        task["runtime_worker"] = effective_worker
        task["status"] = "in_progress"
        task["claimed_at"] = now()
        if latest_diagnosis:
            latest_diagnosis["applied_to"] = args.task
            latest_diagnosis["applied_at"] = now()
            state.setdefault("decision_log", []).append(
                {
                    "kind": "sol-diagnosis",
                    "task": args.task,
                    "summary": "The active implementer was instructed to apply the current Sol diagnosis.",
                    "reason": f"Implementation escalation epoch {state.get('escalation_epoch')} required read-only Sol guidance.",
                    "source": latest_diagnosis["report"],
                    "at": now(),
                }
            )
        state["attempts"][attempt_key] = attempts + 1
        runtime_note = f" via {effective_worker}" + (" fallback" if fallback_reason else "")
        save_state(args.feature, state, f"starting {attempt_key} attempt {attempts + 1}{runtime_note}")
        result = run(
            command,
            cwd=worktree,
            input_text=prompt,
            env=forge_worker_environment(args.feature),
            check=False,
        )
    except BaseException:
        if worktree_locked:
            release_lock(args.feature, args.task, task["worktree"])
        raise
    finally:
        release_model_run_lock(args.feature, model_actor)
    state = load_state(args.feature)
    task = state["tasks"][args.task]
    task["status"] = "awaiting_report" if result.returncode == 0 else "failed"
    if result.returncode != 0:
        release_lock(args.feature, args.task, task["worktree"])
        state["state"] = "ESCALATION"
        state["escalation_epoch"] = state.get("escalation_epoch", 0) + 1
    state["workers"].setdefault(effective_worker.split("-")[0], {"reports": []})["reports"].append(str(report))
    save_state(args.feature, state, f"{attempt_key} via {effective_worker} exited {result.returncode}")
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        raise ForgeError(result.stderr.strip() or f"worker exited {result.returncode}")
    print(report)


def command_validation_record(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "VERIFICATION":
        raise ForgeError("validations may be recorded only during verification")
    path = workspace_path(state)
    if git_output(["status", "--porcelain"], cwd=path):
        raise ForgeError("validation evidence must be recorded against a committed workspace HEAD")
    unknown = [task_id for task_id in args.task if task_id not in state["tasks"]]
    if unknown:
        raise ForgeError(f"unknown validation tasks: {', '.join(unknown)}")
    evidence = None
    if args.evidence:
        source = Path(args.evidence)
        destination = feature_dir(args.feature) / "evidence" / f"{len(state['validations']) + 1:03d}-{source.name}"
        copy_record(source, destination)
        evidence = str(destination)
    state["validations"].append(
        {
            "name": args.name,
            "command": args.command,
            "status": args.status,
            "scope": "feature",
            "evidence": evidence,
            "owner": args.owner,
            "tasks": list(dict.fromkeys(args.task or [])),
            "workspace": state["workspace"]["letter"],
            "workspace_head": workspace_head(state),
            "at": now(),
        }
    )
    if args.status in {"fail", "blocked"}:
        state["state"] = "ESCALATION"
        state["escalation_epoch"] = state.get("escalation_epoch", 0) + 1
    save_state(args.feature, state, f"validation {args.name}: {args.status}")
    print(args.status)


def command_validation_require(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "TECHNICAL_REVIEW":
        raise ForgeError("validation requirements may be defined only during technical review")
    key = (args.name, "feature")
    existing = {
        (item["name"], "feature")
        for item in state["required_validations"]
    }
    if key in existing:
        raise ForgeError(f"validation requirement already exists: feature/{args.name}")
    tasks = list(dict.fromkeys(args.task or []))
    unknown = [task_id for task_id in tasks if task_id not in state["tasks"]]
    if unknown:
        raise ForgeError(f"unknown validation tasks: {', '.join(unknown)}")
    state["required_validations"].append(
        {
            "name": args.name,
            "scope": "feature",
            "evidence_required": not args.allow_no_evidence,
            "tasks": tasks,
            "created_at": now(),
        }
    )
    save_state(args.feature, state, f"required validation feature/{args.name} added")
    print(args.name)


def command_validation_run(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "VERIFICATION":
        raise ForgeError("validation commands may run only during verification")
    if not args.command:
        raise ForgeError("validation-run requires a command after --")
    unknown = [task_id for task_id in args.task if task_id not in state["tasks"]]
    if unknown:
        raise ForgeError(f"unknown validation tasks: {', '.join(unknown)}")
    path = workspace_path(state)
    if git_output(["status", "--porcelain"], cwd=path):
        raise ForgeError("commit implementation or repair changes before running verification")
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    lock_task = f"__validation__{args.name}"
    letter = state["workspace"]["letter"]
    acquire_lock(args.feature, lock_task, letter)
    try:
        validation_head = workspace_head(state)
        result = run(command, cwd=path, check=False)
        if workspace_head(state) != validation_head:
            raise ForgeError("workspace HEAD changed while validation was running")
    finally:
        release_lock(args.feature, lock_task, letter)
    sequence = len(state["validations"]) + 1
    evidence = feature_dir(args.feature) / "evidence" / f"{sequence:03d}-{args.name}.txt"
    evidence.write_text(
        f"command: {shlex.join(command)}\nexit_code: {result.returncode}\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
        encoding="utf-8",
    )
    validation = {
        "name": args.name,
        "command": shlex.join(command),
        "status": "pass" if result.returncode == 0 else "fail",
        "scope": "feature",
        "evidence": str(evidence),
        "owner": args.owner,
        "tasks": list(dict.fromkeys(args.task or [])),
        "workspace": letter,
        "workspace_head": validation_head,
        "at": now(),
    }
    state["validations"].append(validation)
    if result.returncode != 0:
        state["state"] = "ESCALATION"
        state["escalation_epoch"] = state.get("escalation_epoch", 0) + 1
    save_state(args.feature, state, f"validation feature/{args.name}: {validation['status']}")
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.returncode != 0:
        raise ForgeError(f"validation failed with exit {result.returncode}; evidence: {evidence}")
    print(evidence)


def run_sol_readonly(feature: str, *, purpose: str, prompt_file: Path) -> Path:
    state = load_state(feature)
    if not prompt_file.is_file():
        raise ForgeError(f"Sol prompt file not found: {prompt_file}")
    worktree = workspace_path(state)
    letter = state["workspace"]["letter"]
    if purpose == "technical-review":
        base = (
            "Act as a read-only Forge technical advisor for exactly one bounded specification-strengthening pass. "
            "Review feasibility, architecture, provider capability, security, migrations, and correctness only where "
            "the supplied specification makes them relevant. Inspect only named or directly implicated files plus at "
            "most one direct dependency hop. Use at most 12 total read/search tool calls in one focused exploration "
            "batch, then stop. Do not inventory the repository, perform a general audit, investigate tangential systems, "
            "reopen settled product choices, invent approval gates, or add speculative scope. Distinguish facts from "
            "hypotheses and return concise feedback that Fable can use to strengthen the specification. Recommend the "
            "smallest safe correction. Do not edit files or write code."
        )
    else:
        base = (
            "Act as a read-only Forge diagnosis advisor. Analyze the supplied implementation or test difficulty "
            "using the current worktree and concrete evidence. Identify the root cause and give GPT-5.6 Terra "
            "a bounded, actionable solution. Do not edit files, write code, take over implementation, expand "
            "product scope, or repeat settled product review."
        )
    prompt = (
        f"{base}\n\n<forge_memory>\n{memory_snapshot_text(state)}\n</forge_memory>\n\n"
        f"<forge_packet>\n{prompt_file.read_text(encoding='utf-8')}\n</forge_packet>"
    )
    sequence = len(state["workers"]["sol"]["reports"]) + 1
    report = feature_dir(feature) / "reports" / f"sol-{purpose}-{sequence:03d}.md"
    command, _, _ = worker_command("sol-advisor", worktree, report, state_effort(state))
    lock_task = f"__sol__{purpose}"
    model_actor = f"sol:{purpose}"
    acquire_model_run_lock(feature, model_actor)
    worktree_locked = False
    try:
        acquire_lock(feature, lock_task, letter)
        worktree_locked = True
        result = run(
            command,
            cwd=worktree,
            input_text=prompt,
            env=forge_worker_environment(feature),
            check=False,
        )
        if result.returncode != 0:
            raise ForgeError(result.stderr.strip() or f"Sol {purpose} exited {result.returncode}")
        state = load_state(feature)
        state["workers"]["sol"]["reports"].append(str(report))
        state["advice"].append(
            {
                "purpose": purpose,
                "report": str(report),
                "worktree": letter,
                "workspace_head": workspace_head(state),
                "escalation_epoch": state.get("escalation_epoch") if purpose == "diagnosis" else None,
                "at": now(),
            }
        )
        save_state(feature, state, f"Sol {purpose} report recorded")
    finally:
        if worktree_locked:
            release_lock(feature, lock_task, letter)
        release_model_run_lock(feature, model_actor)
    print(report)
    return report


def command_advice_run(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] not in {"TECHNICAL_REVIEW", "ESCALATION"}:
        raise ForgeError("Sol advice may run only during technical review or escalation")
    expected_purpose = "technical-review" if state["state"] == "TECHNICAL_REVIEW" else "diagnosis"
    if args.purpose != expected_purpose:
        raise ForgeError(f"{state['state']} requires Sol purpose {expected_purpose}")
    if (
        state["state"] == "TECHNICAL_REVIEW"
        and args.purpose == "technical-review"
        and any(item.get("purpose") == "technical-review" for item in state["advice"])
    ):
        raise ForgeError(
            "pre-implementation Sol technical review already completed; reconcile it with Fable and do not re-review"
        )
    if args.dry_run:
        print(
            shlex.join(
                worker_command(
                    "sol-advisor",
                    workspace_path(state),
                    Path("REPORT.md"),
                    state_effort(state),
                )[0]
            )
        )
        return
    run_sol_readonly(args.feature, purpose=args.purpose, prompt_file=Path(args.prompt_file))


def command_finding_add(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    identifier = f"F-{len(state['findings']) + 1:03d}"
    state["findings"].append(
        {
            "id": identifier,
            "severity": args.severity,
            "owner": args.owner,
            "summary": args.summary,
            "status": "open",
            "created_at": now(),
            "resolved_at": None,
        }
    )
    save_state(args.feature, state, f"finding {identifier} opened")
    print(identifier)


def command_finding_resolve(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    finding = next((item for item in state["findings"] if item["id"] == args.finding), None)
    if not finding:
        raise ForgeError(f"unknown finding: {args.finding}")
    finding.update({"status": "resolved", "resolved_at": now(), "resolution": args.resolution})
    save_state(args.feature, state, f"finding {args.finding} resolved")
    print("resolved")


def command_finalize(args: argparse.Namespace) -> None:
    state = load_state(args.feature)
    if state["state"] != "VERIFICATION":
        raise ForgeError("finalization requires VERIFICATION state")
    if not state["specification"].get("approved") or state["specification"]["version"] < 1:
        raise ForgeError("finalization requires the current recorded specification to be approved")
    if not state["tasks"]:
        raise ForgeError("finalization requires at least one task")
    incomplete = [
        task_id
        for task_id, task in state["tasks"].items()
        if task["status"] != "completed"
        or not task.get("report")
    ]
    if incomplete:
        raise ForgeError(f"tasks incomplete: {', '.join(incomplete)}")
    if not state["required_validations"]:
        raise ForgeError("finalization requires explicit validation requirements")
    validation_errors = validation_gate_errors(state)
    if validation_errors:
        raise ForgeError("validation gates failed: " + "; ".join(validation_errors))
    blocking = [item["id"] for item in state["findings"] if item["severity"] == "blocking" and item["status"] == "open"]
    if blocking:
        raise ForgeError(f"blocking findings remain: {', '.join(blocking)}")
    path = workspace_path(state)
    if git_output(["status", "--porcelain"], cwd=path):
        raise ForgeError("finalization requires a clean invocation worktree")
    final_commit = workspace_head(state)
    if final_commit == state["workspace"]["baseline_head"]:
        raise ForgeError("finalization requires a committed implementation")
    state["final_commit"] = final_commit
    state["state"] = "COMPLETE"
    save_state(args.feature, state, "transition VERIFICATION -> COMPLETE: all tests passed")
    print("COMPLETE")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(function=command_doctor)

    init = commands.add_parser("init")
    init.add_argument("feature")
    init.add_argument("--title", required=True)
    init.add_argument("--effort", choices=COMMON_REASONING_EFFORTS)
    request_group = init.add_mutually_exclusive_group(required=True)
    request_group.add_argument("--request-file")
    request_group.add_argument("--request")
    init.set_defaults(function=command_init)

    show = commands.add_parser("show")
    show.add_argument("feature")
    show.add_argument("--json", action="store_true")
    show.set_defaults(function=command_show)

    session_bind = commands.add_parser("session-bind")
    session_bind.add_argument("feature")
    session_bind.add_argument("--session-id")
    session_bind.set_defaults(function=command_session_bind)

    decision = commands.add_parser("decision-add")
    decision.add_argument("feature")
    decision.add_argument(
        "--kind",
        required=True,
        choices=("implementation-decision", "spec-deviation"),
    )
    decision.add_argument("--task")
    decision.add_argument("--summary", required=True)
    decision.add_argument("--reason", required=True)
    decision.add_argument("--source-file")
    decision.set_defaults(function=command_decision_add)

    transition = commands.add_parser("transition")
    transition.add_argument("feature")
    transition.add_argument("target", choices=STATES)
    transition.add_argument("--note")
    transition.set_defaults(function=command_transition)

    spec_set = commands.add_parser("spec-set")
    spec_set.add_argument("feature")
    spec_set.add_argument("--file", required=True)
    spec_set.add_argument("--version", required=True, type=int)
    spec_set.set_defaults(function=command_spec_set)

    approve = commands.add_parser("approve")
    approve.add_argument("feature")
    approve.add_argument("--version", required=True, type=int)
    approve.add_argument("--approved-by", required=True)
    approve.set_defaults(function=command_approve)

    task_add = commands.add_parser("task-add")
    task_add.add_argument("feature")
    task_add.add_argument("--task", required=True)
    task_add.add_argument("--owner", required=True, choices=TASK_OWNERS)
    task_add.add_argument("--packet-file", required=True)
    task_add.add_argument("--depends-on", action="append", default=[])
    task_add.set_defaults(function=command_task_add)

    claim = commands.add_parser("claim")
    claim.add_argument("feature")
    claim.add_argument("--task", required=True)
    claim.set_defaults(function=command_claim)

    release = commands.add_parser("release")
    release.add_argument("feature")
    release.add_argument("--task", required=True)
    release.add_argument("--status", required=True, choices=("completed", "failed", "blocked", "pending"))
    release.add_argument("--report-file")
    release.add_argument("--commit")
    release.set_defaults(function=command_release)

    task_reopen = commands.add_parser("task-reopen")
    task_reopen.add_argument("feature")
    task_reopen.add_argument("--task", required=True)
    task_reopen.add_argument("--reason", required=True)
    task_reopen.set_defaults(function=command_task_reopen)

    fable_start = commands.add_parser("fable-start")
    fable_start.add_argument("feature")
    fable_start.add_argument("--purpose", required=True, choices=("specification",))
    fable_start.add_argument("--prompt-file", required=True)
    fable_start.set_defaults(function=command_fable_start)

    fable_resume = commands.add_parser("fable-resume")
    fable_resume.add_argument("feature")
    fable_resume.add_argument("--purpose", required=True, choices=("specification",))
    fable_resume_input = fable_resume.add_mutually_exclusive_group(required=True)
    fable_resume_input.add_argument("--user-reply-file")
    fable_resume_input.add_argument("--evidence-file")
    fable_resume.set_defaults(function=command_fable_resume)

    worker = commands.add_parser("worker-run")
    worker.add_argument("feature")
    worker.add_argument("--task", required=True)
    worker.add_argument("--worker", required=True, choices=TASK_OWNERS)
    worker.add_argument("--max-attempts", type=int, default=10)
    worker.add_argument("--dry-run", action="store_true")
    worker.set_defaults(function=command_worker_run)

    validation = commands.add_parser("validation-record")
    validation.add_argument("feature")
    validation.add_argument("--name", required=True)
    validation.add_argument("--command", required=True)
    validation.add_argument("--status", required=True, choices=("pass", "fail", "blocked"))
    validation.add_argument("--evidence")
    validation.add_argument("--owner")
    validation.add_argument("--task", action="append", default=[])
    validation.set_defaults(function=command_validation_record)

    validation_require = commands.add_parser("validation-require")
    validation_require.add_argument("feature")
    validation_require.add_argument("--name", required=True)
    validation_require.add_argument("--allow-no-evidence", action="store_true")
    validation_require.add_argument("--task", action="append", default=[])
    validation_require.set_defaults(function=command_validation_require)

    validation_run = commands.add_parser("validation-run")
    validation_run.add_argument("feature")
    validation_run.add_argument("--name", required=True)
    validation_run.add_argument("--owner")
    validation_run.add_argument("--task", action="append", default=[])
    validation_run.add_argument("--command", nargs=argparse.REMAINDER, required=True)
    validation_run.set_defaults(function=command_validation_run)

    finding = commands.add_parser("finding-add")
    finding.add_argument("feature")
    finding.add_argument("--severity", required=True, choices=("blocking", "nonblocking"))
    finding.add_argument("--owner", required=True)
    finding.add_argument("--summary", required=True)
    finding.set_defaults(function=command_finding_add)

    resolve = commands.add_parser("finding-resolve")
    resolve.add_argument("feature")
    resolve.add_argument("--finding", required=True)
    resolve.add_argument("--resolution", required=True)
    resolve.set_defaults(function=command_finding_resolve)

    advice = commands.add_parser("advice-run")
    advice.add_argument("feature")
    advice.add_argument("--purpose", required=True, choices=("technical-review", "diagnosis"))
    advice.add_argument("--prompt-file", required=True)
    advice.add_argument("--dry-run", action="store_true")
    advice.set_defaults(function=command_advice_run)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("feature")
    finalize.set_defaults(function=command_finalize)
    return root


def main() -> int:
    try:
        arguments = parser().parse_args()
        arguments.function(arguments)
        return 0
    except ForgeError as error:
        print(f"forge: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
