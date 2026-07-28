from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .common import AgentWorktreesError, run
from .config import ModelConfig


COMMIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject"],
    "properties": {"subject": {"type": "string", "minLength": 12, "maxLength": 72}},
}

CHILD_ENVIRONMENT_ALLOWLIST = {
    "PATH",
    "HOME",
    "TMPDIR",
    "SHELL",
    "LANG",
    "LC_ALL",
    "TERM",
    "COLORTERM",
    "USER",
    "LOGNAME",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
}


def _child_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name in CHILD_ENVIRONMENT_ALLOWLIST
    }
    environment["AGENT_WORKTREES_CHILD"] = "1"
    return environment


def _structured_value(stdout: str) -> dict[str, object]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise AgentWorktreesError(f"model returned invalid JSON: {error}") from error
    if isinstance(value, dict) and isinstance(value.get("structured_output"), dict):
        value = value["structured_output"]
    elif isinstance(value, dict) and "result" in value:
        nested = value["result"]
        if isinstance(nested, str):
            try:
                value = json.loads(nested)
            except json.JSONDecodeError as error:
                raise AgentWorktreesError(f"model result contained invalid JSON: {error}") from error
        elif isinstance(nested, dict):
            value = nested
    if not isinstance(value, dict):
        raise AgentWorktreesError("model output must be a JSON object")
    return value


def invoke_structured(
    harness: str,
    root: Path,
    prompt: str,
    schema: dict[str, object],
    model_config: ModelConfig,
    effort: str,
    *,
    writable: bool = False,
    timeout: int = 900,
) -> dict[str, object]:
    test_output = os.environ.get("AGENT_WORKTREES_TEST_MODEL_OUTPUT")
    if test_output is not None:
        return _structured_value(test_output)
    environment = _child_environment()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as schema_file:
        json.dump(schema, schema_file)
        schema_file.flush()
        if harness == "codex":
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--disable",
                "hooks",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write" if writable else "read-only",
                "-C",
                str(root),
                "-m",
                model_config.model,
                "-c",
                f'model_reasoning_effort="{effort}"',
                "--output-schema",
                schema_file.name,
                "-",
            ]
        else:
            tools = "Read,Write,Edit,Glob,Grep" if writable else "Read,Glob,Grep"
            command = [
                "claude",
                "-p",
                "--safe-mode",
                "--no-session-persistence",
                "--model",
                model_config.model,
                "--effort",
                effort,
                "--tools",
                tools,
                "--permission-mode",
                "acceptEdits" if writable else "dontAsk",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema),
            ]
        result = run(command, cwd=root, input_text=prompt, env=environment, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise AgentWorktreesError(
            f"{harness} model failed: {detail[-1] if detail else result.returncode}"
        )
    return _structured_value(result.stdout.strip())


def commit_subject(
    harness: str, root: Path, diff: str, model_config: ModelConfig
) -> str:
    prompt = """Write one meaningful Git commit subject for this worktree checkpoint.

Rules:
- Return only the structured JSON required by the schema.
- Use imperative mood and describe the behavior or outcome, not file names.
- Keep it between 12 and 72 characters.
- Never use generic subjects such as save progress, update files, or work in progress.

Staged changes:
""" + diff[:30000]
    value = invoke_structured(
        harness,
        root,
        prompt,
        COMMIT_SCHEMA,
        model_config,
        model_config.checkpoint_effort,
    )
    subject = value.get("subject")
    if not isinstance(subject, str):
        raise AgentWorktreesError("commit model did not return a subject")
    subject = " ".join(subject.strip().split())
    generic = {"save progress", "update files", "work in progress", "checkpoint changes"}
    if not 12 <= len(subject) <= 72 or subject.lower().rstrip(".") in generic:
        raise AgentWorktreesError(f"commit model returned an unacceptable subject: {subject!r}")
    return subject
