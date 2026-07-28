from __future__ import annotations

from pathlib import Path

from .audit import manifest_from_report, run_audit
from .common import AgentWorktreesError, executable_exists, read_json, run, write_json
from .config import ProjectConfig, load_config
from .git_lanes import default_branch, ensure_worktrees, primary_checkout_root, registered_worktrees
from .installer import activate_hooks, extra_manifest_groups, full_install
from .notifications import configure as configure_notifications
from .notifications import send_notification
from .parity import bootstrap as bootstrap_parity
from .parity import check as check_parity
from .shell_setup import install_shortcuts, shortcut_conflicts
from .security import safe_repository_path


AGENT_SETUP = {
    "codex": {
        "label": "Codex",
        "installCommand": "npm install --global @openai/codex",
        "verifyCommand": "codex --version",
        "loginCommand": "codex login",
        "authCheckCommand": "codex login status",
        "accountNote": "Sign in with ChatGPT in the browser, or choose another supported Codex login method.",
        "docs": "https://developers.openai.com/codex/cli",
    },
    "claude": {
        "label": "Claude Code",
        "installCommand": "curl -fsSL https://claude.ai/install.sh | bash",
        "verifyCommand": "claude --version",
        "loginCommand": "claude auth login",
        "authCheckCommand": "claude auth status",
        "accountNote": "Complete the Anthropic browser sign-in with the account you want Claude Code to use.",
        "docs": "https://code.claude.com/docs/en/overview",
    },
}


def _state_path(root: Path) -> Path:
    result = run(["git", "rev-parse", "--git-common-dir"], cwd=root, check=True)
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    return common.resolve() / "agent-worktrees/walkthrough-state.json"


def _state(root: Path) -> dict[str, object]:
    raw = read_json(_state_path(root), {"version": 1, "stage": "preflight", "approvals": []})
    if not isinstance(raw, dict):
        raise AgentWorktreesError("walkthrough state must be an object")
    allowed_stages = {
        "preflight",
        "audit",
        "agent_setup",
        "land",
        "worktrees",
        "shortcuts",
        "notifications",
        "hooks",
        "verify",
        "done",
    }
    if raw.get("version") != 1 or raw.get("stage") not in allowed_stages:
        raise AgentWorktreesError("walkthrough state is invalid")
    approvals = raw.get("approvals")
    if not isinstance(approvals, list) or not all(isinstance(value, str) for value in approvals):
        raise AgentWorktreesError("walkthrough approvals state is invalid")
    return raw


def _save(root: Path, state: dict[str, object]) -> None:
    path = _state_path(root)
    write_json(path, state)
    path.chmod(0o600)


def _approved(state: dict[str, object], step: str) -> bool:
    approvals = state.get("approvals", [])
    return isinstance(approvals, list) and step in approvals


def approve(root: Path, step: str) -> dict[str, object]:
    allowed = {"audit", "worktrees", "shortcuts", "notifications", "hooks"}
    if step not in allowed:
        raise AgentWorktreesError("approval step must be one of: " + ", ".join(sorted(allowed)))
    state = _state(root)
    if step == "audit":
        report = read_json(root / ".agent-worktrees/audit/report.json")
        if not isinstance(report, dict):
            raise AgentWorktreesError("audit report is invalid")
        unresolved = [
            str(group.get("name"))
            for group in report.get("groups", [])
            if isinstance(group, dict) and group.get("classification") == "requires-user"
        ]
        if unresolved:
            raise AgentWorktreesError(
                "resolve these audit groups before approval: " + ", ".join(unresolved)
            )
    approvals = state.setdefault("approvals", [])
    if not isinstance(approvals, list):
        raise AgentWorktreesError("walkthrough approvals state is invalid")
    if step not in approvals:
        approvals.append(step)
    _save(root, state)
    return {"status": "approved", "step": step}


def _counterpart(path: str, target: str) -> str | None:
    candidate = Path(path)
    if target == "claude":
        if candidate.name == "AGENTS.md":
            return (candidate.parent / "CLAUDE.md").as_posix()
        if path.startswith(".agents/skills/"):
            return path.replace(".agents/skills/", ".claude/skills/", 1)
        if path.startswith(".codex/hooks/"):
            return path.replace(".codex/hooks/", ".claude/hooks/", 1)
        if path == ".codex/hooks.json":
            return ".claude/settings.json"
    else:
        if candidate.name == "CLAUDE.md":
            return (candidate.parent / "AGENTS.md").as_posix()
        if path.startswith(".claude/skills/"):
            return path.replace(".claude/skills/", ".agents/skills/", 1)
        if path.startswith(".claude/hooks/"):
            return path.replace(".claude/hooks/", ".codex/hooks/", 1)
        if path == ".claude/settings.json":
            return ".codex/hooks.json"
    return None


def resolve_group(root: Path, name: str, resolution: str) -> dict[str, object]:
    path = root / ".agent-worktrees/audit/report.json"
    report = read_json(path)
    if not isinstance(report, dict) or not isinstance(report.get("groups"), list):
        raise AgentWorktreesError("audit report is invalid")
    selected: dict[str, object] | None = None
    for group in report["groups"]:
        if isinstance(group, dict) and group.get("name") == name:
            selected = group
            break
    if selected is None:
        raise AgentWorktreesError(f"audit group not found: {name}")
    if resolution == "provider-only":
        codex_paths = selected.get("codexPaths", [])
        claude_paths = selected.get("claudePaths", [])
        selected["classification"] = "codex-only" if codex_paths else "claude-only"
        if codex_paths and claude_paths:
            raise AgentWorktreesError("provider-only requires a group with exactly one native side")
        selected["bootstrapSource"] = "none"
    elif resolution in {"codex", "claude"}:
        source_key = "codexPaths" if resolution == "codex" else "claudePaths"
        target_key = "claudePaths" if resolution == "codex" else "codexPaths"
        source_paths = selected.get(source_key, [])
        target_paths = selected.get(target_key, [])
        if not isinstance(source_paths, list) or not source_paths:
            raise AgentWorktreesError(f"group {name} has no {resolution} source paths")
        if not isinstance(target_paths, list):
            raise AgentWorktreesError(f"group {name} target paths are invalid")
        if not target_paths:
            derived = [_counterpart(str(source), "claude" if resolution == "codex" else "codex") for source in source_paths]
            if any(value is None for value in derived):
                raise AgentWorktreesError(
                    "the target path is not mechanically derivable; edit report.json with the intended native path"
                )
            selected[target_key] = [value for value in derived if value is not None]
        selected["classification"] = "adapted"
        selected["bootstrapSource"] = resolution
    else:
        raise AgentWorktreesError("resolution must be codex, claude, or provider-only")
    write_json(path, report)
    return {"status": "resolved", "group": name, "resolution": resolution}


def _preflight(root: Path, config: ProjectConfig) -> list[str]:
    required = ("git", "gh", "python3")
    missing = [name for name in required if not executable_exists(name)]
    if run(["git", "remote", "get-url", config.remote], cwd=root).returncode != 0:
        missing.append(f"Git remote {config.remote}")
    return missing


def agent_status(name: str) -> dict[str, object]:
    if name not in AGENT_SETUP:
        raise AgentWorktreesError(f"unknown agent tool: {name}")
    installed = executable_exists(name)
    if not installed:
        return {"installed": False, "authenticated": False, "version": None}
    version = run([name, "--version"], timeout=20)
    auth_command = [name, "login", "status"] if name == "codex" else [name, "auth", "status"]
    authentication = run(auth_command, timeout=30)
    return {
        "installed": True,
        "authenticated": authentication.returncode == 0,
        "version": version.stdout.strip() if version.returncode == 0 else None,
    }


def agent_statuses() -> dict[str, dict[str, object]]:
    return {name: agent_status(name) for name in ("codex", "claude")}


def _agent_setup_action(
    statuses: dict[str, dict[str, object]], harness: str
) -> dict[str, object] | None:
    order = (harness, "claude" if harness == "codex" else "codex")
    for name in order:
        status = statuses[name]
        setup = AGENT_SETUP[name]
        if status.get("installed") is not True:
            if name == "codex" and not executable_exists("npm"):
                return {
                    "status": "needs_prerequisites",
                    "missing": ["npm (install Node.js first so Codex CLI can be installed)"],
                    "tool": name,
                    "docs": "https://nodejs.org/en/download",
                    "summary": "Install Node.js, confirm `npm --version` works, then re-run the walkthrough.",
                }
            if name == "claude" and not executable_exists("curl"):
                return {
                    "status": "needs_prerequisites",
                    "missing": ["curl (required by the Claude Code native installer)"],
                    "tool": name,
                    "summary": "Install curl, confirm `curl --version` works, then re-run the walkthrough.",
                }
            return {
                "status": "needs_tool_install",
                "tool": name,
                "label": setup["label"],
                "command": setup["installCommand"],
                "verify": setup["verifyCommand"],
                "docs": setup["docs"],
                "summary": f"Install {setup['label']}, verify it, then re-run the walkthrough.",
            }
        if status.get("authenticated") is not True:
            return {
                "status": "needs_tool_auth",
                "tool": name,
                "label": setup["label"],
                "command": setup["loginCommand"],
                "verify": setup["authCheckCommand"],
                "accountNote": setup["accountNote"],
                "docs": setup["docs"],
                "summary": f"Sign in to {setup['label']}, verify the login, then re-run the walkthrough.",
            }
    return None


def _setup_landed(root: Path, config: ProjectConfig) -> bool:
    result = run(["git", "status", "--porcelain"], cwd=root, check=True)
    if result.stdout.strip():
        return False
    run(["git", "fetch", config.remote], cwd=root, check=True)
    base = default_branch(root, config.remote)
    paths = [
        ".agent-worktrees",
        ".agent-parity",
        ".agents/skills/walkthrough",
        ".agents/skills/pull",
        ".agents/skills/ship",
        ".codex",
        ".claude",
        "AGENTS.md",
        "CLAUDE.md",
    ]
    return run(["git", "diff", "--quiet", f"{config.remote}/{base}", "--", *paths], cwd=root).returncode == 0


def run_walkthrough(root: Path, config: ProjectConfig, harness: str) -> dict[str, object]:
    state = _state(root)
    stage = str(state.get("stage", "preflight"))
    if stage == "preflight":
        missing = _preflight(root, config)
        if missing:
            return {"status": "needs_prerequisites", "missing": missing}
        statuses = agent_statuses()
        current = statuses[harness]
        if current.get("installed") is not True or current.get("authenticated") is not True:
            other = "claude" if harness == "codex" else "codex"
            current_only_statuses = {
                **statuses,
                other: {"installed": True, "authenticated": True, "version": None},
            }
            setup_action = _agent_setup_action(current_only_statuses, harness)
            if setup_action:
                return setup_action
        audited_sides = tuple(
            name
            for name in ("codex", "claude")
            if statuses[name].get("installed") is True
            and statuses[name].get("authenticated") is True
        )
        report = run_audit(root, config, harness, audited_sides)
        state["stage"] = "audit"
        state["agentStatusAtAudit"] = statuses
        state["auditedSides"] = list(audited_sides)
        _save(root, state)
        return {
            "status": "needs_user" if report.get("questions") else "needs_approval",
            "step": "audit",
            "report": str(root / ".agent-worktrees/audit/report.json"),
            "questions": report.get("questions", []),
            "summary": report.get("summary"),
        }
    if stage == "audit":
        report = read_json(root / ".agent-worktrees/audit/report.json")
        if not isinstance(report, dict):
            raise AgentWorktreesError("audit report is invalid")
        unresolved = [
            str(group.get("name"))
            for group in report.get("groups", [])
            if isinstance(group, dict) and group.get("classification") == "requires-user"
        ]
        if unresolved:
            return {
                "status": "needs_user",
                "step": "audit",
                "groups": unresolved,
                "questions": report.get("questions", []),
            }
        if not _approved(state, "audit"):
            return {
                "status": "needs_approval",
                "step": "audit",
                "report": str(root / ".agent-worktrees/audit/report.json"),
            }
        full_install(root)
        activate_hooks(root)
        manifest = manifest_from_report(root, extra_manifest_groups(root))
        if not safe_repository_path(root, ".agent-parity/manifest.json"):
            raise AgentWorktreesError("unsafe parity manifest path")
        write_json(root / ".agent-parity/manifest.json", manifest)
        parity_result = bootstrap_parity(root, config, harness)
        if parity_result.get("status") != "baselined":
            return parity_result
        state["stage"] = "agent_setup"
        _save(root, state)
        statuses = agent_statuses()
        setup_action = _agent_setup_action(statuses, harness)
        if setup_action:
            return {
                **setup_action,
                "auditedSides": state.get("auditedSides", []),
                "summary": setup_action["summary"]
                + " Your existing configuration audit and generated native counterpart are preserved.",
            }
        state["stage"] = "land"
        _save(root, state)
        return {
            "status": "needs_land",
            "summary": "Land the reviewed setup files on the GitHub default branch, then re-run the walkthrough.",
        }
    if stage == "agent_setup":
        statuses = agent_statuses()
        setup_action = _agent_setup_action(statuses, harness)
        if setup_action:
            return {
                **setup_action,
                "auditedSides": state.get("auditedSides", []),
                "summary": setup_action["summary"]
                + " Your existing configuration audit and generated native counterpart are preserved.",
            }
        state["stage"] = "land"
        state["agentStatusAfterSetup"] = statuses
        _save(root, state)
        return {
            "status": "needs_land",
            "summary": "Both agent tools are installed and authenticated. Land the reviewed setup files on the GitHub default branch, then re-run the walkthrough.",
        }
    if stage == "land":
        if not _setup_landed(root, config):
            return {
                "status": "needs_land",
                "summary": "The setup is not yet clean and present on the GitHub default branch.",
            }
        state["stage"] = "worktrees"
        _save(root, state)
        return {
            "status": "needs_approval",
            "step": "worktrees",
            "summary": "Create persistent a-e worktrees and matching origin branches.",
        }
    if stage == "worktrees":
        if not _approved(state, "worktrees"):
            return {"status": "needs_approval", "step": "worktrees"}
        if not _setup_landed(root, config):
            state["stage"] = "land"
            _save(root, state)
            return {
                "status": "needs_land",
                "summary": "The reviewed setup changed after approval; land it again before creating worktrees.",
            }
        summaries = ensure_worktrees(root, config)
        state["stage"] = "shortcuts"
        state["worktrees"] = summaries
        _save(root, state)
        return {
            "status": "needs_approval",
            "step": "shortcuts",
            "summary": "Add za/zac through ze/zec functions to ~/.zshrc.",
            "conflicts": shortcut_conflicts(Path.home() / ".zshrc", config),
        }
    if stage == "shortcuts":
        if not _approved(state, "shortcuts"):
            return {"status": "needs_approval", "step": "shortcuts"}
        state["shortcuts"] = install_shortcuts(root, config)
        state["stage"] = "notifications"
        _save(root, state)
        return {
            "status": "needs_approval",
            "step": "notifications",
            "summary": "Configure machine-local Codex and Claude Code desktop notifications and send a test.",
        }
    if stage == "notifications":
        if not _approved(state, "notifications"):
            return {"status": "needs_approval", "step": "notifications"}
        primary = primary_checkout_root(root)
        records = registered_worktrees(primary)
        claude_roots = [primary] + sorted(
            path
            for path in records
            if path != primary and path.parent == (primary / config.worktree_root).resolve()
        )
        state["notifications"] = configure_notifications(primary, claude_roots)
        send_notification("Agent worktrees", "Notifications are configured.")
        state["stage"] = "hooks"
        _save(root, state)
        return {
            "status": "needs_hook_trust",
            "summary": "Start a new agent session, review the project hook, and trust it before continuing.",
        }
    if stage == "hooks":
        if not _approved(state, "hooks"):
            return {"status": "needs_hook_trust"}
        state["stage"] = "verify"
        _save(root, state)
    if str(state.get("stage")) == "verify":
        parity_result = check_parity(root)
        primary = primary_checkout_root(root)
        records = registered_worktrees(primary)
        expected = {(primary / config.worktree_root / lane).resolve() for lane in config.lanes}
        missing = sorted(str(path) for path in expected - set(records))
        if missing:
            raise AgentWorktreesError("missing configured worktrees: " + ", ".join(missing))
        state["stage"] = "done"
        _save(root, state)
        return {
            "status": "done",
            "parity": parity_result,
            "lanes": list(config.lanes),
            "commands": [value for lane in config.lanes for value in (f"z{lane}", f"z{lane}c")],
        }
    return {"status": "done", "lanes": list(config.lanes)}
