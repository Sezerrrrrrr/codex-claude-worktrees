from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checkpoint import checkpoint
from .common import AgentWorktreesError, emit, repository_root
from .config import ensure_default_config, load_config
from .git_workflows import pull, ship
from .git_lanes import ensure_worktrees, primary_checkout_root, registered_worktrees
from .hook import parse_payload, stop_hook
from .installer import bootstrap_install, install_forge
from .notifications import configure as configure_notifications
from .notifications import send_notification
from .parity import baseline, bootstrap, check, status, synchronize
from .walkthrough import approve, resolve_group, run_walkthrough
from .shell_setup import install_shortcuts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees",
        description="Persistent Git lanes shared by native Codex and Claude Code setups.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install", help="Seed the walkthrough into a Git repository")
    install.add_argument("target", nargs="?", default=".")
    forge = subparsers.add_parser(
        "install-forge",
        help="Install the optional Codex-only Forge workflow",
    )
    forge.add_argument("target", nargs="?", default=".")

    walkthrough = subparsers.add_parser("walkthrough", help="Run or advance guided setup")
    walkthrough.add_argument("--harness", choices=("codex", "claude"), required=True)
    walkthrough_sub = walkthrough.add_subparsers(dest="action", required=True)
    walkthrough_sub.add_parser("run")
    approve_parser = walkthrough_sub.add_parser("approve")
    approve_parser.add_argument("step")
    resolve_parser = walkthrough_sub.add_parser("resolve")
    resolve_parser.add_argument("group")
    resolve_parser.add_argument("resolution", choices=("codex", "claude", "provider-only"))

    parity = subparsers.add_parser("parity", help="Inspect or synchronize native configuration")
    parity.add_argument("action", choices=("baseline", "bootstrap", "check", "status", "sync"))
    parity.add_argument("--harness", choices=("codex", "claude"), default="codex")

    checkpoint_parser = subparsers.add_parser("checkpoint", help="Commit and push the current lane")
    checkpoint_parser.add_argument("--harness", choices=("codex", "claude"), required=True)
    checkpoint_parser.add_argument("--message")
    for name in ("pull", "ship", "hook"):
        command = subparsers.add_parser(name)
        command.add_argument("--harness", choices=("codex", "claude"), required=True)
    subparsers.add_parser("ensure-lanes", help="Create any configured persistent lanes")
    subparsers.add_parser("install-shortcuts", help="Install or refresh zsh lane shortcuts")
    subparsers.add_parser("configure-notifications", help="Configure native desktop notifications")
    subparsers.add_parser("notify-test")
    notify_handler = subparsers.add_parser("notify-handler")
    notify_handler.add_argument("payload", nargs="?")
    return parser


def _exit_code(result: dict[str, object]) -> int:
    status_value = result.get("status")
    if status_value in {
        "needs_prerequisites",
        "needs_tool_install",
        "needs_tool_auth",
        "needs_approval",
        "needs_user",
        "needs_land",
        "needs_hook_trust",
        "needs_resolve",
    }:
        return 20
    if status_value == "poll_wait":
        return 80
    if status_value == "checks_failed":
        return 25
    return 0


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        if args.command == "notify-handler":
            from .notifications import notification_main

            return notification_main([args.payload] if args.payload is not None else [])
        if args.command in {"install", "install-forge"}:
            root = repository_root(Path(args.target).resolve())
            result = bootstrap_install(root) if args.command == "install" else install_forge(root)
        else:
            root = repository_root()
            ensure_default_config(root)
            config = load_config(root)
            if args.command == "walkthrough":
                if args.action == "run":
                    result = run_walkthrough(root, config, args.harness)
                elif args.action == "approve":
                    result = approve(root, args.step)
                else:
                    result = resolve_group(root, args.group, args.resolution)
            elif args.command == "parity":
                actions = {
                    "baseline": lambda: baseline(root),
                    "bootstrap": lambda: bootstrap(root, config, args.harness),
                    "check": lambda: check(root),
                    "status": lambda: status(root),
                    "sync": lambda: synchronize(root, config, args.harness),
                }
                result = actions[args.action]()
            elif args.command == "checkpoint":
                result = checkpoint(root, config, args.harness, message=args.message)
            elif args.command == "pull":
                result = pull(root, config, args.harness)
            elif args.command == "ship":
                result = ship(root, config, args.harness)
            elif args.command == "hook":
                result = stop_hook(root, config, args.harness, parse_payload(sys.stdin.read()))
                print(json.dumps(result))
                return 0
            elif args.command == "ensure-lanes":
                result = {"status": "done", "lanes": ensure_worktrees(root, config)}
            elif args.command == "install-shortcuts":
                result = install_shortcuts(root, config)
            elif args.command == "configure-notifications":
                primary = primary_checkout_root(root)
                records = registered_worktrees(primary)
                claude_roots = [primary] + sorted(
                    path
                    for path in records
                    if path != primary and path.parent == (primary / config.worktree_root).resolve()
                )
                result = configure_notifications(primary, claude_roots)
            else:
                send_notification("Agent worktrees", "Notifications are working.")
                result = {"status": "done"}
        print(json.dumps(result, indent=2, sort_keys=True))
        return _exit_code(result)
    except (AgentWorktreesError, OSError, TimeoutError) as error:
        if getattr(args, "command", None) == "hook":
            print(json.dumps({"decision": "block", "reason": f"Agent worktree finalization failed: {error}"}))
            return 0
        emit("error", summary=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
