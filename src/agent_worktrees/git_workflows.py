from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from .checkpoint import _scan_staged, checkpoint
from .common import AgentWorktreesError, run
from .config import ProjectConfig
from .git_lanes import (
    conflicted_files,
    default_branch,
    operation_in_progress,
    remote_branch_exists,
    validate_lane,
)


def _counts(root: Path, left: str, right: str) -> tuple[int, int]:
    result = run(["git", "rev-list", "--left-right", "--count", f"{left}...{right}"], cwd=root, check=True)
    first, second = result.stdout.strip().split()
    return int(first), int(second)


def _rebase_continue(root: Path) -> dict[str, object] | None:
    if operation_in_progress(root) != "rebase":
        return None
    conflicts = conflicted_files(root)
    if conflicts:
        return {"status": "needs_resolve", "files": list(conflicts)}
    _scan_staged(root)
    environment = os.environ.copy()
    environment["GIT_EDITOR"] = "true"
    result = run(["git", "rebase", "--continue"], cwd=root, env=environment)
    if result.returncode != 0:
        conflicts = conflicted_files(root)
        if conflicts:
            return {"status": "needs_resolve", "files": list(conflicts)}
        raise AgentWorktreesError((result.stderr or result.stdout).strip())
    if operation_in_progress(root) == "rebase":
        return _rebase_continue(root)
    return None


def _sync_ref(root: Path, target: str) -> bool:
    local_ahead, target_ahead = _counts(root, "HEAD", target)
    if target_ahead == 0:
        return False
    if local_ahead == 0:
        run(["git", "merge", "--ff-only", target], cwd=root, check=True)
        return False
    result = run(["git", "rebase", target], cwd=root)
    if result.returncode != 0:
        conflicts = conflicted_files(root)
        if conflicts:
            return True
        raise AgentWorktreesError((result.stderr or result.stdout).strip())
    return False


def pull(root: Path, config: ProjectConfig, harness: str) -> dict[str, object]:
    lane = validate_lane(root, config)
    continuation = _rebase_continue(root)
    if continuation:
        return continuation
    operation = operation_in_progress(root)
    if operation:
        raise AgentWorktreesError(f"cannot pull while a {operation} is in progress")
    checkpoint_result = checkpoint(root, config, harness)
    run(["git", "fetch", "--prune", lane.remote], cwd=root, check=True)
    original = run(["git", "rev-parse", "HEAD"], cwd=root, check=True).stdout.strip()
    if remote_branch_exists(root, lane.remote, lane.lane):
        if _sync_ref(root, f"{lane.remote}/{lane.lane}"):
            return {"status": "needs_resolve", "files": list(conflicted_files(root))}
    base = default_branch(root, lane.remote)
    if _sync_ref(root, f"{lane.remote}/{base}"):
        return {"status": "needs_resolve", "files": list(conflicted_files(root))}
    after = run(["git", "rev-parse", "HEAD"], cwd=root, check=True).stdout.strip()
    rewrote = original != after and not run(
        ["git", "merge-base", "--is-ancestor", original, after], cwd=root
    ).returncode == 0
    push_command = ["git", "push", lane.remote, lane.lane]
    if rewrote:
        push_command = ["git", "push", "--force-with-lease", lane.remote, lane.lane]
    if not lane.upstream:
        push_command = ["git", "push", "--set-upstream", lane.remote, lane.lane]
    run(push_command, cwd=root, check=True)
    behind, ahead = _counts(root, f"{lane.remote}/{base}", "HEAD")
    return {
        "status": "done",
        "lane": lane.lane,
        "base": base,
        "ahead": ahead,
        "behind": behind,
        "checkpoint": checkpoint_result,
    }


def _run_validations(root: Path, config: ProjectConfig) -> None:
    for command in config.validation_commands:
        result = run(command, cwd=root)
        if result.returncode != 0:
            raise AgentWorktreesError(
                f"validation failed ({' '.join(command)}): "
                + (result.stderr or result.stdout).strip()
            )


def _pull_request(root: Path, lane: str) -> dict[str, object] | None:
    result = run(
        [
            "gh",
            "pr",
            "view",
            lane,
            "--json",
            "number,state,url,title,statusCheckRollup",
        ],
        cwd=root,
    )
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AgentWorktreesError(f"GitHub returned invalid PR JSON: {error}") from error
    if not isinstance(value, dict):
        raise AgentWorktreesError("GitHub PR result must be an object")
    return value


def _create_pull_request(root: Path, remote: str, lane: str, base: str) -> dict[str, object]:
    subject = run(["git", "log", "-1", "--pretty=%s"], cwd=root, check=True).stdout.strip()
    commits = run(
        ["git", "log", "--reverse", "--pretty=- %s", f"{remote}/{base}..HEAD"],
        cwd=root,
        check=True,
    ).stdout.strip()
    body = "## Summary\n\n" + (commits or f"- {subject}") + "\n\n## Validation\n\n- Managed by codex-claude-worktrees\n"
    result = run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            lane,
            "--title",
            subject[:70],
            "--body",
            body,
        ],
        cwd=root,
        check=True,
    )
    created = _pull_request(root, lane)
    if created is None:
        raise AgentWorktreesError(f"created a PR but could not rediscover it: {result.stdout.strip()}")
    return created


def _check_state(pull_request: dict[str, object]) -> tuple[str, list[str]]:
    rollup = pull_request.get("statusCheckRollup", [])
    if not isinstance(rollup, list):
        return "failed", ["GitHub returned an invalid statusCheckRollup"]
    pending: list[str] = []
    failed: list[str] = []
    for check in rollup:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or check.get("context") or "unnamed check")
        state = str(check.get("conclusion") or check.get("state") or check.get("status") or "").upper()
        if state in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            continue
        if state in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
            failed.append(name)
        else:
            pending.append(name)
    if failed:
        return "failed", failed
    if pending:
        return "pending", pending
    return "ready", []


def _retarget_lane(root: Path, config: ProjectConfig, lane_name: str, base: str) -> dict[str, object]:
    remote = config.remote
    run(["git", "fetch", remote, base], cwd=root, check=True)
    merged = run(["git", "rev-parse", f"{remote}/{base}"], cwd=root, check=True).stdout.strip()
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = f"refs/agent-worktrees/backups/{lane_name}/{timestamp}"
    run(["git", "update-ref", backup, "HEAD"], cwd=root, check=True)
    run(["git", "reset", "--hard", merged], cwd=root, check=True)
    run(
        ["git", "push", "--force-with-lease", remote, f"HEAD:refs/heads/{lane_name}"],
        cwd=root,
        check=True,
    )
    run(["git", "branch", "--set-upstream-to", f"{remote}/{lane_name}", lane_name], cwd=root, check=True)
    local = run(["git", "rev-parse", "HEAD"], cwd=root, check=True).stdout.strip()
    remote_lane = run(["git", "rev-parse", f"{remote}/{lane_name}"], cwd=root, check=True).stdout.strip()
    if not local == remote_lane == merged:
        raise AgentWorktreesError("post-ship lane retarget verification failed")
    return {"status": "done", "lane": lane_name, "sha": merged, "backupRef": backup}


def ship(root: Path, config: ProjectConfig, harness: str) -> dict[str, object]:
    lane = validate_lane(root, config)
    pull_result = pull(root, config, harness)
    if pull_result.get("status") != "done":
        return pull_result
    _run_validations(root, config)
    base = default_branch(root, lane.remote)
    _, ahead = _counts(root, f"{lane.remote}/{base}", "HEAD")
    if ahead == 0:
        return _retarget_lane(root, config, lane.lane, base)
    pull_request = _pull_request(root, lane.lane)
    if pull_request is None:
        pull_request = _create_pull_request(root, lane.remote, lane.lane, base)
    state = str(pull_request.get("state", "")).upper()
    if state == "MERGED":
        return _retarget_lane(root, config, lane.lane, base)
    if state != "OPEN":
        raise AgentWorktreesError(f"PR #{pull_request.get('number')} is {state or 'not open'}")
    check_state, names = _check_state(pull_request)
    if check_state == "pending":
        return {
            "status": "poll_wait",
            "pr": pull_request.get("url"),
            "checks": names,
        }
    if check_state == "failed":
        return {
            "status": "checks_failed",
            "pr": pull_request.get("url"),
            "checks": names,
        }
    run(
        ["gh", "pr", "merge", str(pull_request["number"]), "--squash", "--delete-branch=false"],
        cwd=root,
        check=True,
    )
    return _retarget_lane(root, config, lane.lane, base)
