from __future__ import annotations

import fcntl
from pathlib import Path

from .common import AgentWorktreesError, run, run_bytes
from .config import ProjectConfig
from .git_lanes import conflicted_files, operation_in_progress, validate_lane
from .models import commit_subject
from .security import contains_secret, sensitive_path


def _lock_path(root: Path) -> Path:
    result = run(["git", "rev-parse", "--git-common-dir"], cwd=root, check=True)
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    path = common / "agent-worktrees/checkpoint.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _scan_staged(root: Path) -> None:
    names_output = run(
        [
            "git",
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=ACMR",
        ],
        cwd=root,
        check=True,
    ).stdout
    names = [name for name in names_output.split("\0") if name]
    sensitive = [name for name in names if sensitive_path(name)]
    if sensitive:
        raise AgentWorktreesError(
            "refusing to commit sensitive-looking files: " + ", ".join(sensitive)
        )
    for name in names:
        blob = run_bytes(["git", "cat-file", "blob", f":{name}"], cwd=root, check=True).stdout
        if b"\x00" in blob:
            raise AgentWorktreesError(
                f"refusing to auto-commit binary file {name}; review and commit it manually"
            )
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AgentWorktreesError(
                f"refusing to auto-commit binary file {name}; review and commit it manually"
            ) from error
        if contains_secret(text):
            raise AgentWorktreesError(f"refusing to commit possible secret material in {name}")


def checkpoint(
    root: Path,
    config: ProjectConfig,
    harness: str,
    *,
    message: str | None = None,
    push: bool = True,
) -> dict[str, object]:
    lane = validate_lane(root, config)
    with _lock_path(root).open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AgentWorktreesError("another agent is checkpointing this repository") from error
        operation = operation_in_progress(root)
        if operation:
            raise AgentWorktreesError(f"cannot checkpoint while a {operation} is in progress")
        conflicts = conflicted_files(root)
        if conflicts:
            raise AgentWorktreesError("resolve conflicts before checkpointing: " + ", ".join(conflicts))
        status = run(["git", "status", "--porcelain"], cwd=root, check=True).stdout
        if not status.strip():
            return {"status": "clean", "lane": lane.lane}
        run(["git", "add", "-A"], cwd=root, check=True)
        _scan_staged(root)
        staged = run(["git", "diff", "--cached", "--stat"], cwd=root, check=True).stdout
        staged += "\n" + run(["git", "diff", "--cached"], cwd=root, check=True).stdout
        subject = message or commit_subject(
            harness,
            root,
            staged,
            config.codex if harness == "codex" else config.claude,
        )
        run(["git", "commit", "-m", subject], cwd=root, check=True)
        if push:
            upstream = lane.upstream
            command = ["git", "push", lane.remote, lane.lane]
            if not upstream:
                command = ["git", "push", "--set-upstream", lane.remote, lane.lane]
            push_result = run(command, cwd=root)
            if push_result.returncode != 0:
                raise AgentWorktreesError(
                    "checkpoint committed locally but push failed; no force-push was attempted: "
                    + (push_result.stderr or push_result.stdout).strip()
                )
        return {"status": "checkpointed", "lane": lane.lane, "subject": subject, "pushed": push}
