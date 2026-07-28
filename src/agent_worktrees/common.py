from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


class AgentWorktreesError(RuntimeError):
    """A recoverable configuration or workflow error."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CommandBytesResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    check: bool = False,
) -> CommandResult:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        input=input_text,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise AgentWorktreesError(f"command failed ({' '.join(command)}): {detail}")
    return result


def run_bytes(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    check: bool = False,
) -> CommandBytesResult:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
    )
    result = CommandBytesResult(completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise AgentWorktreesError(f"command failed ({' '.join(command)}): {detail}")
    return result


def repository_root(start: Path | None = None) -> Path:
    result = run(["git", "rev-parse", "--show-toplevel"], cwd=start)
    if result.returncode != 0:
        raise AgentWorktreesError("run this command inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def read_json(path: Path, default: object | None = None) -> object:
    if not path.is_file():
        if default is not None:
            return default
        raise AgentWorktreesError(f"missing JSON file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AgentWorktreesError(f"invalid JSON in {path}: {error}") from error


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def emit(status: str, **values: object) -> None:
    print(json.dumps({"status": status, **values}, indent=2, sort_keys=True))


def executable_exists(name: str) -> bool:
    result = run(["/usr/bin/env", "sh", "-c", f"command -v {name}"])
    return result.returncode == 0


def ensure_lines(path: Path, lines: Iterable[str]) -> bool:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    normalized = existing.rstrip("\n")
    changed = False
    for line in lines:
        if line not in existing.splitlines():
            normalized += ("\n" if normalized else "") + line
            changed = True
    if changed:
        atomic_write(path, normalized + "\n")
    return changed
