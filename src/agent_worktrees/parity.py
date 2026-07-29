from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .common import AgentWorktreesError, read_json, write_json
from .config import ProjectConfig
from .models import invoke_structured
from .security import contains_secret, safe_repository_path


PAIRED = {"paired", "adapted"}
USER_GATED = {"requires-user", "vendor-managed"}
RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "adaptations", "uncertainties", "questions"],
    "properties": {
        "status": {"type": "string", "enum": ["applied", "needs_user"]},
        "summary": {"type": "string"},
        "adaptations": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass(frozen=True)
class Group:
    name: str
    classification: str
    codex: tuple[str, ...]
    claude: tuple[str, ...]
    allow_cross_provider_references: bool = False
    bootstrap_source: str | None = None
    bidirectional: bool = False
    references: tuple[str, ...] = ()


def _generated(path: Path, root: Path | None = None) -> bool:
    # Exclusion must be judged relative to the scan root: a lane lives *under*
    # .codex/worktrees/<letter>, so its own path parts contain ".codex" +
    # "worktrees". Checking absolute parts would flag every file inside a lane as
    # generated. Nested worktrees only exist BELOW the primary checkout, so the
    # relative path is the correct thing to test.
    parts = path.relative_to(root).parts if root is not None else path.parts
    return (
        "__pycache__" in parts
        or "node_modules" in parts
        or (".codex" in parts and "worktrees" in parts)
        or (".agent-worktrees" in parts and "audit" in parts)
        or path.suffix == ".pyc"
        or path.name in {".DS_Store", "settings.local.json"}
    )


def _files(root: Path, relatives: tuple[str, ...]) -> list[tuple[str, Path]]:
    output: list[tuple[str, Path]] = []
    for relative in relatives:
        path = root / relative
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if (child.is_file() or child.is_symlink()) and not _generated(child, root):
                    output.append((child.relative_to(root).as_posix(), child))
        else:
            output.append((relative, path))
    return output


def load_manifest(root: Path) -> list[Group]:
    if not safe_repository_path(root, ".agent-parity/manifest.json"):
        raise AgentWorktreesError(".agent-parity must be a real directory inside the repository")
    raw = read_json(root / ".agent-parity/manifest.json")
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list):
        raise AgentWorktreesError("parity manifest must contain a groups array")
    groups: list[Group] = []
    seen: set[str] = set()
    for item in raw["groups"]:
        if not isinstance(item, dict):
            raise AgentWorktreesError("every parity group must be an object")
        group = Group(
            name=str(item.get("name", "")),
            classification=str(item.get("classification", "")),
            codex=tuple(str(value) for value in item.get("codex", [])),
            claude=tuple(str(value) for value in item.get("claude", [])),
            allow_cross_provider_references=bool(
                item.get("allowCrossProviderReferences", False)
            ),
            bootstrap_source=(
                str(item["bootstrapSource"]) if item.get("bootstrapSource") in {"codex", "claude"} else None
            ),
            bidirectional=bool(item.get("bidirectional", False)),
            references=tuple(str(value) for value in item.get("references", [])),
        )
        if not group.name:
            raise AgentWorktreesError("parity group names cannot be empty")
        if group.classification in PAIRED and (not group.codex or not group.claude):
            raise AgentWorktreesError(f"paired group {group.name} needs both native sides")
        for relative in (*group.codex, *group.claude):
            if not safe_repository_path(root, relative):
                raise AgentWorktreesError(f"unsafe parity path: {relative}")
            if relative in seen:
                raise AgentWorktreesError(f"parity path appears twice: {relative}")
            seen.add(relative)
        groups.append(group)
    if not groups:
        raise AgentWorktreesError("parity manifest has no groups")
    return groups


def _side_hash(root: Path, relatives: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative, path in _files(root, relatives):
        if path.is_symlink():
            raise AgentWorktreesError(f"native agent files cannot be symlinks: {relative}")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def snapshot(root: Path, groups: list[Group]) -> dict[str, dict[str, str]]:
    return {
        group.name: {
            "codex": _side_hash(root, group.codex),
            "claude": _side_hash(root, group.claude),
        }
        for group in groups
    }


def _state_path(root: Path) -> Path:
    return root / ".agent-parity/state.json"


def load_state(root: Path) -> dict[str, dict[str, str]]:
    raw = read_json(_state_path(root), {"hashes": {}})
    if not isinstance(raw, dict) or not isinstance(raw.get("hashes"), dict):
        raise AgentWorktreesError("parity state is invalid")
    return raw["hashes"]


def write_state(root: Path, hashes: dict[str, dict[str, str]]) -> None:
    if not safe_repository_path(root, ".agent-parity/state.json"):
        raise AgentWorktreesError("unsafe parity state path")
    write_json(_state_path(root), {"version": 1, "hashes": hashes})


def changed_sides(
    groups: list[Group], current: dict[str, dict[str, str]], previous: dict[str, dict[str, str]]
) -> dict[str, set[str]]:
    expected = {group.name for group in groups}
    if set(previous) != expected:
        raise AgentWorktreesError("parity ledger does not match the manifest; review and baseline it")
    changes = {"codex": set(), "claude": set()}
    for group in groups:
        for side in ("codex", "claude"):
            if current[group.name][side] != previous[group.name].get(side):
                changes[side].add(group.name)
    return changes


def _validate_file(path: Path, relative: str, side: str, allow_cross: bool) -> list[str]:
    if path.is_symlink():
        return [f"{relative}: symlinks are not allowed"]
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    errors: list[str] = []
    if not allow_cross:
        forbidden = ("CLAUDE.md", ".claude/") if side == "codex" else ("AGENTS.md", ".agents/", ".codex/")
        for token in forbidden:
            if token in text:
                errors.append(f"{relative}: cross-provider runtime reference {token!r}")
    if contains_secret(text):
        errors.append(f"{relative}: possible secret material")
    try:
        if path.suffix == ".json":
            json.loads(text)
        elif path.suffix == ".toml":
            tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        errors.append(f"{relative}: invalid syntax: {error}")
    return errors


def static_check(root: Path, groups: list[Group]) -> list[str]:
    errors: list[str] = []
    classified: set[str] = set()
    for group in groups:
        for side in ("codex", "claude"):
            for relative, path in _files(root, getattr(group, side)):
                classified.add(relative)
                errors.extend(
                    _validate_file(path, relative, side, group.allow_cross_provider_references)
                )
    runtime: set[str] = set()
    for base in (".agents", ".codex", ".claude"):
        for relative, path in _files(root, (base,)):
            if path.is_file() and not _generated(path, root):
                runtime.add(relative)
    for filename in ("AGENTS.md", "CLAUDE.md"):
        runtime.update(
            path.relative_to(root).as_posix()
            for path in root.rglob(filename)
            if path.is_file() and not _generated(path, root)
        )
    unclassified = sorted(runtime - classified)
    if unclassified:
        errors.append("unclassified native agent files: " + ", ".join(unclassified))
    local_settings = root / ".claude/settings.local.json"
    if local_settings.is_file():
        errors.extend(
            _validate_file(
                local_settings,
                ".claude/settings.local.json",
                "claude",
                allow_cross=True,
            )
        )
    return errors


def _copy(root: Path, stage: Path, side: str, relatives: tuple[str, ...]) -> None:
    for relative, path in _files(root, relatives):
        if path.is_file():
            destination = stage / side / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def _copy_references(root: Path, stage: Path, group: Group) -> None:
    """Stage authoritative provider docs so the translator reads real schema instead of guessing it."""
    for relative in group.references:
        source = root / relative
        if source.is_file():
            destination = stage / "reference" / Path(relative).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def _stage_files(stage: Path, side: str) -> dict[str, bytes]:
    base = stage / side
    if not base.exists():
        return {}
    output: dict[str, bytes] = {}
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base).as_posix()
        if path.is_symlink():
            raise AgentWorktreesError(f"parity staging cannot contain symlinks: {relative}")
        if path.is_file():
            output[relative] = path.read_bytes()
    return output


def _prompt(source: str, target: str, groups: list[Group]) -> str:
    path_contracts = "\n".join(
        f"- {group.name}: source={list(getattr(group, source))}; "
        f"target={list(getattr(group, target))}"
        for group in groups
    )
    return f"""Translate changed coding-agent configuration into a complete native counterpart.

Source harness: {source}
Target harness: {target}
Groups: {', '.join(group.name for group in groups)}
Path contracts:
{path_contracts}

When reference/ contains provider documentation, READ IT FIRST — it is the authoritative schema for
event names, config keys, hook types, field names, and file conventions; never guess what you can
look up there.
Read source/ and the existing target/. Edit only target/. Preserve intent and detail, but use the
target provider's own instruction files, skill paths, hook syntax, terminology, and capabilities.
Create missing target files under exactly the listed target path contracts. A listed directory may
contain the complete native files it needs; do not write outside those paths.
Never make one provider load the other provider's runtime files. Shared deterministic executables
under .agent-worktrees are allowed; instruction adapters, symlinks, and compatibility loaders are not.
Do not add credentials or perform Git/network actions. If any semantic choice is uncertain, do not
edit target and return needs_user with precise questions. Return only the required structured JSON.
"""


def synchronize(root: Path, config: ProjectConfig, harness: str) -> dict[str, object]:
    groups = load_manifest(root)
    errors = static_check(root, groups)
    if errors:
        raise AgentWorktreesError("; ".join(errors))
    current = snapshot(root, groups)
    previous = load_state(root)
    if not previous:
        raise AgentWorktreesError("parity is not baselined; complete the walkthrough first")
    changes = changed_sides(groups, current, previous)
    changed_names = changes["codex"] | changes["claude"]
    if not changed_names:
        return {"status": "clean", "summary": "Native configurations are already aligned."}
    changed_groups = [group for group in groups if group.name in changed_names]
    gated = [group.name for group in changed_groups if group.classification in USER_GATED]
    if gated:
        return {
            "status": "needs_user",
            "summary": "Provider-specific configuration changed and needs a decision.",
            "groups": sorted(gated),
        }
    # Decide, per changed paired group, which side to regenerate FROM. The counterpart is
    # rebuilt from scratch (editing an existing target in place proved unreliable — the
    # model no-ops when the target already looks complete).
    #   - bidirectional: whichever side changed drives; if BOTH changed -> conflict.
    #   - bootstrapSource: that side is always the source of truth.
    #   - neither (hand-maintained both sides, e.g. pull/ship/walkthrough) or provider-only:
    #     accepted as-is and re-baselined without translation.
    regen: list[tuple[Group, str]] = []
    accepted: list[str] = []
    conflicts: list[str] = []
    for group in changed_groups:
        if group.classification not in PAIRED:
            accepted.append(group.name)
        elif group.bidirectional:
            in_claude = group.name in changes["claude"]
            in_codex = group.name in changes["codex"]
            if in_claude and in_codex:
                conflicts.append(group.name)
            else:
                regen.append((group, "claude" if in_claude else "codex"))
        elif group.bootstrap_source:
            regen.append((group, group.bootstrap_source))
        else:
            accepted.append(group.name)
    if conflicts:
        return {
            "status": "conflict",
            "summary": "Both native sides of a synced group changed since the last parity run.",
            "groups": sorted(conflicts),
        }
    if not regen:
        write_state(root, current)
        return {
            "status": "provider_only",
            "summary": "No counterpart regeneration was required.",
            "groups": sorted(accepted),
        }
    model_config = config.codex if harness == "codex" else config.claude
    applied: list[str] = []
    for group, source in regen:
        target = "claude" if source == "codex" else "codex"
        with tempfile.TemporaryDirectory(prefix="agent-parity-") as temporary_directory:
            stage = Path(temporary_directory)
            _copy(root, stage, "source", getattr(group, source))
            _copy_references(root, stage, group)
            # Deliberately do NOT stage the existing target: force full regeneration.
            source_before = _stage_files(stage, "source")
            result = invoke_structured(
                harness,
                stage,
                _prompt(source, target, [group]),
                RESULT_SCHEMA,
                model_config,
                model_config.parity_effort,
                writable=True,
            )
            if _stage_files(stage, "source") != source_before:
                raise AgentWorktreesError("parity translator modified its source")
            if result.get("status") == "needs_user" or result.get("questions"):
                return {**result, "groups": [group.name]}
            allowed: set[str] = set()
            for specification in getattr(group, target):
                allowed.update(rel for rel, path in _files(root, (specification,)) if path.is_file())
                allowed.update(
                    rel for rel, path in _files(stage / "target", (specification,)) if path.is_file()
                )
            staged = _stage_files(stage, "target")
            unexpected = set(staged) - allowed
            if unexpected:
                raise AgentWorktreesError(
                    "parity translator wrote unapproved files: " + ", ".join(sorted(unexpected))
                )
            backup = {rel: (root / rel).read_bytes() for rel in allowed if (root / rel).is_file()}
            try:
                for rel in allowed:
                    source_path = stage / "target" / rel
                    destination = root / rel
                    if source_path.is_file():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        temporary = destination.with_name(destination.name + ".parity-tmp")
                        shutil.copy2(source_path, temporary)
                        temporary.replace(destination)
                    elif destination.is_file():
                        destination.unlink()
                step_errors = static_check(root, groups)
                if step_errors:
                    raise AgentWorktreesError("; ".join(step_errors))
            except Exception:
                for rel in allowed:
                    destination = root / rel
                    if rel in backup:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(backup[rel])
                    else:
                        destination.unlink(missing_ok=True)
                raise
            applied.append(group.name)
    write_state(root, snapshot(root, groups))
    return {
        "status": "applied",
        "summary": f"Regenerated {len(applied)} native counterpart(s) from source.",
        "groups": applied,
        "accepted": sorted(accepted),
    }


def bootstrap(root: Path, config: ProjectConfig, harness: str) -> dict[str, object]:
    groups = load_manifest(root)
    gated = [group.name for group in groups if group.classification == "requires-user"]
    if gated:
        return {
            "status": "needs_user",
            "summary": "Resolve provider-specific audit groups before initial parity.",
            "groups": gated,
        }
    applied: list[str] = []
    for group in groups:
        if group.classification not in PAIRED or group.bootstrap_source is None:
            continue
        source = group.bootstrap_source
        target = "claude" if source == "codex" else "codex"
        with tempfile.TemporaryDirectory(prefix="agent-parity-bootstrap-") as temporary_directory:
            stage = Path(temporary_directory)
            _copy(root, stage, "source", getattr(group, source))
            _copy(root, stage, "target", getattr(group, target))
            _copy_references(root, stage, group)
            source_before = _stage_files(stage, "source")
            model_config = config.codex if harness == "codex" else config.claude
            result = invoke_structured(
                harness,
                stage,
                _prompt(source, target, [group]),
                RESULT_SCHEMA,
                model_config,
                model_config.audit_effort,
                writable=True,
            )
            if _stage_files(stage, "source") != source_before:
                raise AgentWorktreesError("initial parity translator modified its source")
            if result.get("status") != "applied" or result.get("questions") or result.get("uncertainties"):
                return {**result, "groups": [group.name]}
            specifications = getattr(group, target)
            allowed: set[str] = set()
            for specification in specifications:
                allowed.update(
                    relative for relative, path in _files(root, (specification,)) if path.is_file()
                )
                allowed.update(
                    relative
                    for relative, path in _files(stage / "target", (specification,))
                    if path.is_file()
                )
            staged = _stage_files(stage, "target")
            unexpected = set(staged) - allowed
            if unexpected:
                raise AgentWorktreesError(
                    "initial parity translator wrote unapproved files: "
                    + ", ".join(sorted(unexpected))
                )
            for relative in allowed:
                source_path = stage / "target" / relative
                destination = root / relative
                if source_path.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(destination.name + ".parity-tmp")
                    shutil.copy2(source_path, temporary)
                    temporary.replace(destination)
                elif destination.is_file():
                    destination.unlink()
            applied.append(group.name)
    errors = static_check(root, groups)
    if errors:
        raise AgentWorktreesError("; ".join(errors))
    write_state(root, snapshot(root, groups))
    return {"status": "baselined", "groups": len(groups), "translated": applied}


def baseline(root: Path) -> dict[str, object]:
    groups = load_manifest(root)
    errors = static_check(root, groups)
    if errors:
        raise AgentWorktreesError("; ".join(errors))
    write_state(root, snapshot(root, groups))
    return {"status": "baselined", "groups": len(groups)}


def check(root: Path) -> dict[str, object]:
    groups = load_manifest(root)
    errors = static_check(root, groups)
    if errors:
        raise AgentWorktreesError("; ".join(errors))
    changes = changed_sides(groups, snapshot(root, groups), load_state(root))
    if changes["codex"] or changes["claude"]:
        raise AgentWorktreesError("parity ledger is stale; run parity sync")
    return {"status": "clean", "groups": len(groups)}


def status(root: Path) -> dict[str, object]:
    groups = load_manifest(root)
    return {
        "status": "scanned",
        "changes": {
            side: sorted(names)
            for side, names in changed_sides(groups, snapshot(root, groups), load_state(root)).items()
        },
    }
