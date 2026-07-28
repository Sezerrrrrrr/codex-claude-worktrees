from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .common import AgentWorktreesError, run
from .config import ProjectConfig
from .security import safe_repository_path


@dataclass(frozen=True)
class LaneContext:
    root: Path
    primary_root: Path
    lane: str
    branch: str
    remote: str
    upstream: str


def primary_checkout_root(root: Path) -> Path:
    result = run(["git", "worktree", "list", "--porcelain"], cwd=root, check=True)
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line.removeprefix("worktree ")).resolve()
    raise AgentWorktreesError("Git did not report a primary checkout")


def default_branch(root: Path, remote: str) -> str:
    remote_result = run(["git", "ls-remote", "--symref", remote, "HEAD"], cwd=root)
    if remote_result.returncode == 0:
        for line in remote_result.stdout.splitlines():
            match = re.match(r"ref: refs/heads/([^\s]+)\s+HEAD$", line)
            if match:
                return match.group(1)
    local_result = run(
        ["git", "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], cwd=root
    )
    if local_result.returncode == 0 and "/" in local_result.stdout.strip():
        return local_result.stdout.strip().split("/", 1)[1]
    for candidate in ("main", "master"):
        if run(["git", "show-ref", "--verify", f"refs/remotes/{remote}/{candidate}"], cwd=root).returncode == 0:
            return candidate
    raise AgentWorktreesError(f"could not determine {remote}'s default branch")


def current_branch(root: Path) -> str:
    result = run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root)
    if result.returncode != 0:
        raise AgentWorktreesError("the current worktree is in detached HEAD state")
    return result.stdout.strip()


def upstream(root: Path) -> str:
    result = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=root
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def validate_lane(root: Path, config: ProjectConfig) -> LaneContext:
    resolved = root.resolve()
    primary = primary_checkout_root(root)
    branch = current_branch(root)
    lane = resolved.name
    expected_root = (primary / config.worktree_root / lane).resolve()
    if lane not in config.lanes:
        raise AgentWorktreesError(
            f"{resolved} is not a configured lane; expected one of {', '.join(config.lanes)}"
        )
    if resolved != expected_root:
        raise AgentWorktreesError(f"lane {lane} must live at {expected_root}")
    if branch != lane:
        raise AgentWorktreesError(f"lane {lane} must check out local branch {lane}, not {branch}")
    tracked = upstream(root)
    expected_upstream = f"{config.remote}/{lane}"
    if tracked and tracked != expected_upstream:
        raise AgentWorktreesError(
            f"lane {lane} must track {expected_upstream}, not {tracked}"
        )
    return LaneContext(root, primary, lane, branch, config.remote, tracked)


def operation_in_progress(root: Path) -> str | None:
    git_dir_result = run(["git", "rev-parse", "--git-dir"], cwd=root, check=True)
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    markers = {
        "rebase-merge": "rebase",
        "rebase-apply": "rebase",
        "MERGE_HEAD": "merge",
        "CHERRY_PICK_HEAD": "cherry-pick",
        "REVERT_HEAD": "revert",
    }
    for marker, name in markers.items():
        if (git_dir / marker).exists():
            return name
    return None


def conflicted_files(root: Path) -> tuple[str, ...]:
    result = run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=root, check=True)
    return tuple(line for line in result.stdout.splitlines() if line)


def remote_branch_exists(root: Path, remote: str, branch: str) -> bool:
    return (
        run(["git", "show-ref", "--verify", f"refs/remotes/{remote}/{branch}"], cwd=root).returncode
        == 0
    )


def local_branch_exists(root: Path, branch: str) -> bool:
    return run(["git", "show-ref", "--verify", f"refs/heads/{branch}"], cwd=root).returncode == 0


def registered_worktrees(root: Path) -> dict[Path, str | None]:
    result = run(["git", "worktree", "list", "--porcelain"], cwd=root, check=True)
    records: dict[Path, str | None] = {}
    path: Path | None = None
    branch: str | None = None
    for line in [*result.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            if path is not None:
                records[path] = branch
            path = Path(line.removeprefix("worktree ")).resolve()
            branch = None
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/")
        elif not line and path is not None:
            records[path] = branch
            path = None
            branch = None
    return records


def ensure_worktrees(root: Path, config: ProjectConfig) -> list[str]:
    primary = primary_checkout_root(root)
    run(["git", "fetch", "--prune", config.remote], cwd=primary, check=True)
    base = default_branch(primary, config.remote)
    if not safe_repository_path(primary, config.worktree_root):
        raise AgentWorktreesError("worktree root must be a real directory inside the repository")
    worktree_root = primary / config.worktree_root
    worktree_root.mkdir(parents=True, exist_ok=True)
    records = registered_worktrees(primary)
    summaries: list[str] = []
    for lane in config.lanes:
        if not safe_repository_path(primary, f"{config.worktree_root}/{lane}"):
            raise AgentWorktreesError(f"lane {lane} resolves outside the repository")
        path = (worktree_root / lane).resolve()
        if path in records:
            if records[path] != lane:
                raise AgentWorktreesError(
                    f"{path} is already a worktree for {records[path] or 'detached HEAD'}, not {lane}"
                )
            summaries.append(f"{lane}: existing worktree")
            continue
        if path.exists() and any(path.iterdir()):
            raise AgentWorktreesError(f"refusing to replace non-empty directory {path}")
        if local_branch_exists(primary, lane):
            run(["git", "worktree", "add", str(path), lane], cwd=primary, check=True)
        elif remote_branch_exists(primary, config.remote, lane):
            run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    lane,
                    str(path),
                    f"{config.remote}/{lane}",
                ],
                cwd=primary,
                check=True,
            )
        else:
            run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    lane,
                    str(path),
                    f"{config.remote}/{base}",
                ],
                cwd=primary,
                check=True,
            )
            run(
                ["git", "push", "--set-upstream", config.remote, lane], cwd=path, check=True
            )
        if not remote_branch_exists(primary, config.remote, lane):
            run(
                ["git", "push", "--set-upstream", config.remote, lane], cwd=path, check=True
            )
        summaries.append(f"{lane}: created {path}")
    return summaries
