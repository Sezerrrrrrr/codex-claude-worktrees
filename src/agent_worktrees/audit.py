from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .common import AgentWorktreesError, write_json
from .config import ProjectConfig
from .models import invoke_structured
from .security import contains_secret, has_symlink_component, safe_repository_path, sensitive_path
AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "groups", "questions", "risks", "proposedSteps"],
    "properties": {
        "summary": {"type": "string"},
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "scope",
                    "classification",
                    "codexPaths",
                    "claudePaths",
                    "bootstrapSource",
                    "allowCrossProviderReferences",
                    "reason",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "scope": {"type": "string", "enum": ["project", "user"]},
                    "classification": {
                        "type": "string",
                        "enum": [
                            "adapted",
                            "codex-only",
                            "claude-only",
                            "machine-local",
                            "vendor-managed",
                            "requires-user",
                        ],
                    },
                    "codexPaths": {"type": "array", "items": {"type": "string"}},
                    "claudePaths": {"type": "array", "items": {"type": "string"}},
                    "bootstrapSource": {
                        "type": "string",
                        "enum": ["codex", "claude", "none"],
                    },
                    "allowCrossProviderReferences": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "proposedSteps": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass(frozen=True)
class InventoryEntry:
    scope: str
    side: str
    path: str
    source: Path
    content_available: bool


def _excluded(path: Path) -> bool:
    return (
        "node_modules" in path.parts
        or "__pycache__" in path.parts
        or (".codex" in path.parts and "worktrees" in path.parts)
        or path.suffix == ".pyc"
        or path.name == ".DS_Store"
    )


def _side(relative: str) -> str | None:
    if relative == "AGENTS.md" or relative.endswith("/AGENTS.md") or relative.startswith((".agents/", ".codex/")):
        return "codex"
    if relative == "CLAUDE.md" or relative.endswith("/CLAUDE.md") or relative.startswith(".claude/"):
        return "claude"
    return None


def _collapse_skill_entries(entries: list[InventoryEntry]) -> list[InventoryEntry]:
    collapsed: dict[tuple[str, str, str], InventoryEntry] = {}
    for entry in entries:
        parts = entry.source.parts
        if "skills" not in parts:
            collapsed[(entry.scope, entry.side, entry.path)] = entry
            continue
        index = parts.index("skills")
        if len(parts) <= index + 1:
            collapsed[(entry.scope, entry.side, entry.path)] = entry
            continue
        skill_root = Path(*parts[: index + 2])
        if entry.scope == "project":
            marker = "/skills/"
            prefix, remainder = entry.path.split(marker, 1)
            skill_path = prefix + marker + remainder.split("/", 1)[0]
        else:
            skill_path = "~/" + skill_root.relative_to(Path.home()).as_posix()
        collapsed[(entry.scope, entry.side, skill_path)] = InventoryEntry(
            entry.scope,
            entry.side,
            skill_path,
            skill_root,
            True,
        )
    return sorted(collapsed.values(), key=lambda entry: (entry.scope, entry.side, entry.path))


def _project_entries(root: Path) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    candidates: set[Path] = set()
    for base in (root / ".agents", root / ".codex", root / ".claude"):
        if base.exists() and not base.is_symlink():
            candidates.update(path for path in base.rglob("*") if path.is_file())
    for name in ("AGENTS.md", "CLAUDE.md"):
        candidates.update(path for path in root.rglob(name) if path.is_file())
    for path in sorted(candidates):
        if _excluded(path):
            continue
        relative = path.relative_to(root).as_posix()
        side = _side(relative)
        if side:
            entries.append(
                InventoryEntry(
                    "project",
                    side,
                    relative,
                    path,
                    path.name != "settings.local.json",
                )
            )
    return _collapse_skill_entries(entries)


def _user_entries() -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    home = Path.home()
    roots = {
        "codex": [
            home / ".codex/AGENTS.md",
            home / ".codex/AGENTS.override.md",
            home / ".codex/config.toml",
            home / ".codex/hooks.json",
            home / ".codex/hooks",
            home / ".codex/rules",
            home / ".codex/skills",
            home / ".agents/skills",
        ],
        "claude": [
            home / ".claude/CLAUDE.md",
            home / ".claude/settings.json",
            home / ".claude/settings.local.json",
            home / ".claude/hooks",
            home / ".claude/rules",
            home / ".claude/skills",
            home / ".claude/agents",
        ],
    }
    for side, candidates in roots.items():
        paths: set[Path] = set()
        for candidate in candidates:
            if candidate.is_file():
                paths.add(candidate)
            elif candidate.is_dir():
                paths.update(path for path in candidate.rglob("*") if path.is_file())
        for path in sorted(paths):
            if _excluded(path):
                continue
            relative = "~/" + path.relative_to(Path.home()).as_posix()
            entries.append(
                InventoryEntry(
                    "user",
                    side,
                    relative,
                    path,
                    path.name
                    not in {
                        "settings.json",
                        "settings.local.json",
                        "config.toml",
                        "hooks.json",
                        "auth.json",
                        "credentials.json",
                    },
                )
            )
    return _collapse_skill_entries(entries)


def inventory(root: Path, sides: tuple[str, ...] | None = None) -> list[InventoryEntry]:
    entries = _project_entries(root) + _user_entries()
    if sides is None:
        return entries
    selected = set(sides)
    if not selected or not selected <= {"codex", "claude"}:
        raise AgentWorktreesError("audit sides must contain codex, claude, or both")
    return [entry for entry in entries if entry.side in selected]


def _stage(entries: list[InventoryEntry], destination: Path) -> list[dict[str, object]]:
    index: list[dict[str, object]] = []
    for number, entry in enumerate(entries):
        available = entry.content_available
        relative_parts = Path(entry.path.removeprefix("~/")).parts
        containment_root = entry.source.parents[len(relative_parts) - 1]
        if has_symlink_component(containment_root, entry.source):
            available = False
        if sensitive_path(entry.path):
            available = False
        if available:
            descendants = list(entry.source.rglob("*")) if entry.source.is_dir() else []
            if any(path.is_symlink() for path in descendants):
                available = False
            sources = [entry.source] if entry.source.is_file() else [
                path for path in descendants if path.is_file() and not _excluded(path)
            ]
        if available:
            for source in sources:
                if source.is_symlink():
                    available = False
                    break
                source_path = entry.path
                if entry.source.is_dir():
                    source_path = (
                        Path(entry.path) / source.relative_to(entry.source)
                    ).as_posix()
                if sensitive_path(source_path):
                    available = False
                    break
                try:
                    content = source.read_bytes()
                    if b"\x00" in content:
                        available = False
                        break
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    available = False
                    break
                if contains_secret(text):
                    available = False
                    break
        staged_name = f"{number:04d}-{entry.source.name}"
        if available:
            staged = destination / "files" / staged_name
            staged.parent.mkdir(parents=True, exist_ok=True)
            if entry.source.is_dir():
                shutil.copytree(
                    entry.source,
                    staged,
                    symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
                )
            else:
                shutil.copy2(entry.source, staged)
        index.append(
            {
                "scope": entry.scope,
                "side": entry.side,
                "path": entry.path,
                "stagedFile": f"files/{staged_name}" if available else None,
                "contentWithheld": not available,
            }
        )
    write_json(destination / "inventory.json", index)
    return index


def _validate_coverage(report: dict[str, object], entries: list[InventoryEntry]) -> None:
    groups = report.get("groups")
    if not isinstance(groups, list):
        raise AgentWorktreesError("audit report did not contain groups")
    expected = {entry.path for entry in entries}
    actual: list[str] = []
    synthetic: list[str] = []
    invalid: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            raise AgentWorktreesError("audit group must be an object")
        classification = group.get("classification")
        bootstrap_source = group.get("bootstrapSource")
        scope = group.get("scope")
        for key, side in (("codexPaths", "codex"), ("claudePaths", "claude")):
            paths = group.get(key)
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise AgentWorktreesError(f"audit group {key} must contain paths")
            for path in paths:
                if path in expected:
                    actual.append(path)
                    continue
                source_side = "claude" if side == "codex" else "codex"
                if (
                    classification == "adapted"
                    and bootstrap_source == source_side
                    and scope == "project"
                    and _side(path) == side
                ):
                    synthetic.append(path)
                else:
                    invalid.append(path)
    classified_paths = actual + synthetic + invalid
    duplicates = sorted(
        {path for path in classified_paths if classified_paths.count(path) > 1}
    )
    if duplicates:
        raise AgentWorktreesError("audit classified paths more than once: " + ", ".join(duplicates))
    missing = sorted(expected - set(actual))
    unexpected = sorted(set(invalid))
    if missing or unexpected:
        raise AgentWorktreesError(
            "audit path coverage mismatch; missing: "
            + (", ".join(missing) or "none")
            + "; unexpected: "
            + (", ".join(unexpected) or "none")
        )


def run_audit(
    root: Path,
    config: ProjectConfig,
    harness: str,
    sides: tuple[str, ...] | None = None,
) -> dict[str, object]:
    audited_sides = sides or ("codex", "claude")
    entries = inventory(root, audited_sides)
    if not safe_repository_path(root, ".agent-worktrees/audit"):
        raise AgentWorktreesError("unsafe audit output path")
    audit_root = root / ".agent-worktrees/audit"
    if audit_root.exists():
        shutil.rmtree(audit_root)
    audit_root.mkdir(parents=True)
    prompt = f"""<audit_task>
Deep-audit the existing agent setup represented by inventory.json.

Only these configured providers are present and in audit scope: {", ".join(audited_sides)}.
Do not infer that the missing provider already has configuration. For an unambiguous item that
should exist in both providers, classify it as adapted, keep the existing path, propose the native
counterpart path on the missing side, and set bootstrapSource to the existing provider. Proposed
counterpart paths are allowed only for project-scoped adapted groups. Provider-specific items may
remain codex-only or claude-only.

Each inventory entry names its real project-relative or user-level path and optionally points to a
sanitized staged copy. Classify every path exactly once. Pair files only when they express the same
intent. Native parity means complete provider-native files: never propose adapters, symlinks,
fallback loaders, or instructions that make one provider read the other provider's runtime files.

Use adapted when an unambiguous native counterpart exists. Set bootstrapSource to the authoritative
side when one side should be translated into the other; use none when both are already equivalent or
the item is provider-specific. Use requires-user and ask a precise question whenever both sides
conflict or the correct authority is uncertain. User-level credentials and local settings are
machine-local. Preserve nested instruction scope. The walkthrough skills may mention both providers
for educational purposes, but operational skills must remain native. Return only structured JSON.
</audit_task>
"""
    model = config.codex if harness == "codex" else config.claude
    with tempfile.TemporaryDirectory(prefix="agent-worktrees-audit-") as temporary_directory:
        stage = Path(temporary_directory)
        index = _stage(entries, stage)
        report = invoke_structured(
            harness,
            stage,
            prompt,
            AUDIT_SCHEMA,
            model,
            model.audit_effort,
            writable=False,
        )
    _validate_coverage(report, entries)
    report["inventoryCount"] = len(index)
    report["auditedSides"] = list(audited_sides)
    write_json(
        audit_root / "inventory.json",
        [{**entry, "stagedFile": None} for entry in index],
    )
    write_json(audit_root / "report.json", report)
    return report


def manifest_from_report(root: Path, extra_groups: list[dict[str, object]]) -> dict[str, object]:
    raw = json.loads((root / ".agent-worktrees/audit/report.json").read_text(encoding="utf-8"))
    groups = raw.get("groups", [])
    manifest_groups: list[dict[str, object]] = []
    for group in groups:
        if not isinstance(group, dict) or group.get("scope") != "project":
            continue
        classification = str(group.get("classification"))
        if classification == "machine-local":
            continue
        item: dict[str, object] = {
            "name": str(group.get("name")),
            "classification": classification,
            "codex": group.get("codexPaths", []),
            "claude": group.get("claudePaths", []),
        }
        source = group.get("bootstrapSource")
        if source in {"codex", "claude"}:
            item["bootstrapSource"] = source
        if group.get("allowCrossProviderReferences") is True:
            item["allowCrossProviderReferences"] = True
        manifest_groups.append(item)
    path_owner: dict[str, int] = {}
    for index, group in enumerate(manifest_groups):
        for side in ("codex", "claude"):
            for path in group.get(side, []):
                if isinstance(path, str):
                    path_owner[path] = index
    for group in extra_groups:
        owners = {
            path_owner[path]
            for side in ("codex", "claude")
            for path in group.get(side, [])
            if isinstance(path, str) and path in path_owner
        }
        if len(owners) == 1:
            owner_index = next(iter(owners))
            owner = manifest_groups[owner_index]
            for side in ("codex", "claude"):
                current = owner.setdefault(side, [])
                if not isinstance(current, list):
                    raise AgentWorktreesError("manifest group paths must be arrays")
                for path in group.get(side, []):
                    if isinstance(path, str) and path not in path_owner:
                        current.append(path)
                        path_owner[path] = owner_index
            if owner.get("codex") and owner.get("claude"):
                owner["classification"] = "adapted"
            if group.get("allowCrossProviderReferences") is True:
                owner["allowCrossProviderReferences"] = True
            continue
        remaining: dict[str, object] = {key: value for key, value in group.items() if key not in {"codex", "claude"}}
        remaining["codex"] = [
            path for path in group.get("codex", []) if isinstance(path, str) and path not in path_owner
        ]
        remaining["claude"] = [
            path for path in group.get("claude", []) if isinstance(path, str) and path not in path_owner
        ]
        if remaining["codex"] or remaining["claude"]:
            if not remaining["codex"] or not remaining["claude"]:
                remaining["classification"] = "codex-only" if remaining["codex"] else "claude-only"
            manifest_groups.append(remaining)
            new_index = len(manifest_groups) - 1
            for side in ("codex", "claude"):
                for path in remaining[side]:
                    path_owner[path] = new_index
    return {"version": 1, "groups": manifest_groups}
