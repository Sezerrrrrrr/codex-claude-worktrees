from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_worktrees.audit import inventory, manifest_from_report, run_audit
from agent_worktrees.checkpoint import checkpoint
from agent_worktrees.cli import main as cli_main
from agent_worktrees.common import AgentWorktreesError, CommandResult, run as real_run
from agent_worktrees.config import ModelConfig, ProjectConfig, ensure_default_config, load_config
from agent_worktrees.git_lanes import ensure_worktrees, validate_lane
from agent_worktrees.git_workflows import _check_state, _rebase_continue, _retarget_lane, pull, ship
from agent_worktrees.installer import activate_hooks, bootstrap_install, extra_manifest_groups, full_install
from agent_worktrees.notifications import configure_claude, configure_codex
from agent_worktrees.shell_setup import install_shortcuts, shortcut_conflicts
from agent_worktrees.parity import bootstrap as bootstrap_parity
from agent_worktrees.common import write_json
from agent_worktrees.walkthrough import _agent_setup_action, agent_status, run_walkthrough


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


class RepositoryFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.remote = self.base / "origin.git"
        self.primary = self.base / "project"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(self.primary)],
            check=True,
            capture_output=True,
        )
        git(self.primary, "config", "user.email", "test@example.com")
        git(self.primary, "config", "user.name", "Test User")
        (self.primary / "README.md").write_text("initial\n", encoding="utf-8")
        git(self.primary, "add", "README.md")
        git(self.primary, "commit", "-m", "Initial commit")
        git(self.primary, "remote", "add", "origin", str(self.remote))
        git(self.primary, "push", "-u", "origin", "main")
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=self.remote,
            check=True,
            capture_output=True,
        )
        model = ModelConfig("test", "high", "medium", "medium")
        self.config = ProjectConfig(
            remote="origin",
            worktree_root=".codex/worktrees",
            lanes=("a", "b"),
            auto_checkpoint=True,
            validation_commands=(),
            codex=model,
            claude=model,
        )

    def close(self) -> None:
        self.temporary.cleanup()


class GitLaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RepositoryFixture()
        ensure_worktrees(self.fixture.primary, self.fixture.config)

    def tearDown(self) -> None:
        self.fixture.close()

    def test_creates_matching_worktrees_branches_and_upstreams(self) -> None:
        lane = self.fixture.primary / ".codex/worktrees/a"
        context = validate_lane(lane, self.fixture.config)
        self.assertEqual(context.lane, "a")
        self.assertEqual(context.upstream, "origin/a")
        self.assertEqual(git(lane, "rev-parse", "--abbrev-ref", "HEAD"), "a")

    def test_checkpoint_commits_and_pushes_only_the_letter_lane(self) -> None:
        lane = self.fixture.primary / ".codex/worktrees/a"
        (lane / "feature.txt").write_text("done\n", encoding="utf-8")

        result = checkpoint(
            lane,
            self.fixture.config,
            "codex",
            message="Add the finished feature behavior",
        )

        self.assertEqual(result["status"], "checkpointed")
        self.assertEqual(
            git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/a")
        )
        self.assertNotEqual(git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/main"))

    def test_checkpoint_refuses_primary_default_branch(self) -> None:
        (self.fixture.primary / "main-only.txt").write_text("no\n", encoding="utf-8")
        with self.assertRaises(AgentWorktreesError):
            checkpoint(
                self.fixture.primary,
                self.fixture.config,
                "codex",
                message="This must not reach the default branch",
            )

    def test_pull_rebases_lane_and_updates_origin_lane(self) -> None:
        lane = self.fixture.primary / ".codex/worktrees/a"
        (lane / "feature.txt").write_text("lane\n", encoding="utf-8")
        checkpoint(lane, self.fixture.config, "codex", message="Add independent lane work")
        (self.fixture.primary / "main.txt").write_text("main\n", encoding="utf-8")
        git(self.fixture.primary, "add", "main.txt")
        git(self.fixture.primary, "commit", "-m", "Advance main")
        git(self.fixture.primary, "push", "origin", "main")

        result = pull(lane, self.fixture.config, "codex")

        self.assertEqual(result["status"], "done")
        self.assertTrue((lane / "feature.txt").is_file())
        self.assertTrue((lane / "main.txt").is_file())
        self.assertEqual(git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/a"))

    def test_rebase_continue_scans_only_already_staged_resolution(self) -> None:
        calls: list[list[str]] = []

        def recorded_run(arguments: list[str], **kwargs: object) -> CommandResult:
            calls.append(arguments)
            return CommandResult(0, "", "")

        with (
            mock.patch(
                "agent_worktrees.git_workflows.operation_in_progress",
                side_effect=["rebase", None],
            ),
            mock.patch("agent_worktrees.git_workflows.conflicted_files", return_value=()),
            mock.patch("agent_worktrees.git_workflows._scan_staged") as scan,
            mock.patch("agent_worktrees.git_workflows.run", side_effect=recorded_run),
        ):
            result = _rebase_continue(self.fixture.primary)

        self.assertIsNone(result)
        scan.assert_called_once_with(self.fixture.primary)
        self.assertNotIn(["git", "add", "-A"], calls)

    def test_retarget_creates_recovery_ref_and_aligns_three_refs(self) -> None:
        lane = self.fixture.primary / ".codex/worktrees/a"
        (lane / "feature.txt").write_text("lane\n", encoding="utf-8")
        checkpoint(lane, self.fixture.config, "codex", message="Preserve work before retargeting")
        before = git(lane, "rev-parse", "HEAD")

        result = _retarget_lane(lane, self.fixture.config, "a", "main")

        self.assertEqual(result["status"], "done")
        self.assertEqual(git(lane, "rev-parse", result["backupRef"]), before)
        self.assertEqual(git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/main"))
        self.assertEqual(git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/a"))

    def test_ship_waits_for_pending_github_checks(self) -> None:
        lane = self.fixture.primary / ".codex/worktrees/a"
        (lane / "feature.txt").write_text("lane\n", encoding="utf-8")
        checkpoint(lane, self.fixture.config, "codex", message="Add feature awaiting CI")
        pull_request = {
            "number": 12,
            "state": "OPEN",
            "url": "https://github.example/pull/12",
            "statusCheckRollup": [{"name": "tests", "status": "IN_PROGRESS"}],
        }

        with mock.patch("agent_worktrees.git_workflows._pull_request", return_value=pull_request):
            result = ship(lane, self.fixture.config, "codex")

        self.assertEqual(result["status"], "poll_wait")
        self.assertEqual(result["checks"], ["tests"])
        self.assertNotEqual(git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/main"))

    def test_ship_merges_ready_pr_and_retargets_lane(self) -> None:
        lane = self.fixture.primary / ".codex/worktrees/a"
        (lane / "feature.txt").write_text("lane\n", encoding="utf-8")
        checkpoint(lane, self.fixture.config, "codex", message="Add feature ready to merge")
        pull_request = {
            "number": 13,
            "state": "OPEN",
            "url": "https://github.example/pull/13",
            "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}],
        }

        def run_with_github_merge(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if arguments[:3] == ["gh", "pr", "merge"]:
                git(self.fixture.primary, "merge", "--squash", "origin/a")
                git(self.fixture.primary, "commit", "-m", "Merge lane a")
                git(self.fixture.primary, "push", "origin", "main")
                return subprocess.CompletedProcess(arguments, 0, "merged\n", "")
            return real_run(arguments, **kwargs)

        with (
            mock.patch("agent_worktrees.git_workflows._pull_request", return_value=pull_request),
            mock.patch("agent_worktrees.git_workflows.run", side_effect=run_with_github_merge),
        ):
            result = ship(lane, self.fixture.config, "codex")

        self.assertEqual(result["status"], "done")
        self.assertEqual(git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/main"))
        self.assertEqual(git(lane, "rev-parse", "HEAD"), git(lane, "rev-parse", "origin/a"))


class ConfigurationTest(unittest.TestCase):
    def test_default_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ensure_default_config(root)
            config = load_config(root)
            self.assertEqual(config.lanes, ("a", "b", "c", "d", "e"))
            self.assertEqual(config.codex.model, "gpt-5.6-sol")
            self.assertEqual(config.claude.model, "fable")
            self.assertEqual(config.codex.audit_effort, "high")
            self.assertEqual(config.claude.parity_effort, "medium")

    def test_check_state_categorizes_github_checks(self) -> None:
        self.assertEqual(
            _check_state({"statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}]})[0],
            "ready",
        )
        self.assertEqual(
            _check_state({"statusCheckRollup": [{"name": "tests", "status": "IN_PROGRESS"}]})[0],
            "pending",
        )
        self.assertEqual(
            _check_state({"statusCheckRollup": [{"name": "tests", "conclusion": "FAILURE"}]})[0],
            "failed",
        )

    def test_notification_handler_does_not_require_a_git_repository(self) -> None:
        with mock.patch("agent_worktrees.notifications.send_notification") as notify:
            result = cli_main(
                [
                    "notify-handler",
                    '{"type":"agent-turn-complete","message":"Finished"}',
                ]
            )

        self.assertEqual(result, 0)
        notify.assert_called_once_with("Codex finished", "Finished")


class InstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RepositoryFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_bootstrap_installs_only_walkthrough_and_config(self) -> None:
        result = bootstrap_install(self.fixture.primary)
        self.assertEqual(result["status"], "installed")
        self.assertTrue((self.fixture.primary / ".agent-worktrees/config.json").is_file())
        self.assertTrue((self.fixture.primary / ".agents/skills/walkthrough/SKILL.md").is_file())
        self.assertTrue((self.fixture.primary / ".claude/skills/walkthrough/SKILL.md").is_file())
        self.assertFalse((self.fixture.primary / ".agents/skills/ship/SKILL.md").exists())
        self.assertFalse((self.fixture.primary / ".agent-worktrees/runtime").exists())

    def test_shortcuts_are_idempotent_and_detect_unmanaged_conflicts(self) -> None:
        ensure_worktrees(self.fixture.primary, self.fixture.config)
        zshrc = self.fixture.base / ".zshrc"
        first = install_shortcuts(self.fixture.primary, self.fixture.config, zshrc)
        second = install_shortcuts(self.fixture.primary, self.fixture.config, zshrc)
        self.assertEqual(first["status"], "installed")
        self.assertEqual(second["status"], "clean")
        self.assertIn("za()", zshrc.read_text(encoding="utf-8"))
        zshrc.write_text("alias za='something-else'\n", encoding="utf-8")
        self.assertEqual(shortcut_conflicts(zshrc, self.fixture.config), ["za"])

    def test_notification_installers_preserve_existing_configuration(self) -> None:
        codex_config = self.fixture.base / "config.toml"
        codex_config.write_text('model = "example"\n', encoding="utf-8")
        result = configure_codex(codex_config)
        self.assertEqual(result["status"], "installed")
        self.assertIn('model = "example"', codex_config.read_text(encoding="utf-8"))
        self.assertIn(
            'notify = ["agent-worktrees", "notify-handler"]',
            codex_config.read_text(encoding="utf-8"),
        )
        settings = self.fixture.primary / ".claude/settings.local.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")
        configure_claude(self.fixture.primary)
        updated = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(updated["permissions"]["allow"], ["Read"])
        self.assertIn("Notification", updated["hooks"])
        self.assertIn("Stop", updated["hooks"])
        self.assertEqual(
            updated["hooks"]["Stop"][0]["hooks"][0]["command"],
            "agent-worktrees notify-handler",
        )

    def test_audit_to_full_native_baseline_classifies_every_installed_file(self) -> None:
        bootstrap_install(self.fixture.primary)
        entries = inventory(self.fixture.primary)
        by_path = {entry.path: entry for entry in entries}
        groups = []
        paired = [
            (
                ".agents/skills/walkthrough",
                ".claude/skills/walkthrough",
                "walkthrough-skill",
                True,
            ),
        ]
        covered: set[str] = set()
        for codex_path, claude_path, name, allow_cross in paired:
            if codex_path in by_path and claude_path in by_path:
                groups.append(
                    {
                        "name": name,
                        "scope": "project",
                        "classification": "adapted",
                        "codexPaths": [codex_path],
                        "claudePaths": [claude_path],
                        "bootstrapSource": "none",
                        "allowCrossProviderReferences": allow_cross,
                        "reason": "Native counterparts",
                    }
                )
                covered.update({codex_path, claude_path})
        for number, entry in enumerate(entries):
            if entry.path in covered:
                continue
            groups.append(
                {
                    "name": f"provider-{number}",
                    "scope": entry.scope,
                    "classification": f"{entry.side}-only" if entry.scope == "project" else "machine-local",
                    "codexPaths": [entry.path] if entry.side == "codex" else [],
                    "claudePaths": [entry.path] if entry.side == "claude" else [],
                    "bootstrapSource": "none",
                    "allowCrossProviderReferences": False,
                    "reason": "Provider-specific test entry",
                }
            )
        fake_report = {
            "summary": "Reviewed",
            "groups": groups,
            "questions": [],
            "risks": [],
            "proposedSteps": ["Install native files"],
        }
        with mock.patch("agent_worktrees.audit.invoke_structured", return_value=fake_report):
            run_audit(self.fixture.primary, self.fixture.config, "codex")
        full_install(self.fixture.primary)
        activate_hooks(self.fixture.primary)
        manifest = manifest_from_report(
            self.fixture.primary, extra_manifest_groups(self.fixture.primary)
        )
        write_json(self.fixture.primary / ".agent-parity/manifest.json", manifest)

        result = bootstrap_parity(self.fixture.primary, self.fixture.config, "codex")

        self.assertEqual(result["status"], "baselined")

    def test_stop_hooks_call_the_installed_executable_directly(self) -> None:
        bootstrap_install(self.fixture.primary)
        activate_hooks(self.fixture.primary)

        codex = json.loads(
            (self.fixture.primary / ".codex/hooks.json").read_text(encoding="utf-8")
        )
        claude = json.loads(
            (self.fixture.primary / ".claude/settings.json").read_text(encoding="utf-8")
        )
        codex_command = codex["hooks"]["Stop"][0]["hooks"][0]["command"]
        claude_command = claude["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(codex_command, "agent-worktrees hook --harness codex")
        self.assertEqual(claude_command, "agent-worktrees hook --harness claude")
        self.assertNotIn(".agent-worktrees", codex_command)
        self.assertNotIn(".agent-worktrees", claude_command)

    def test_inventory_can_audit_only_the_configured_provider(self) -> None:
        bootstrap_install(self.fixture.primary)

        entries = inventory(self.fixture.primary, ("claude",))

        self.assertTrue(entries)
        self.assertEqual({entry.side for entry in entries}, {"claude"})

    def test_single_side_audit_accepts_a_proposed_native_counterpart(self) -> None:
        bootstrap_install(self.fixture.primary)
        entries = inventory(self.fixture.primary, ("codex",))
        groups = []
        for number, entry in enumerate(entries):
            adapted = entry.path == ".agents/skills/walkthrough"
            groups.append(
                {
                    "name": f"group-{number}",
                    "scope": entry.scope,
                    "classification": "adapted" if adapted else "codex-only",
                    "codexPaths": [entry.path],
                    "claudePaths": [".claude/skills/walkthrough"] if adapted else [],
                    "bootstrapSource": "codex" if adapted else "none",
                    "allowCrossProviderReferences": adapted,
                    "reason": "Create a complete native counterpart" if adapted else "Codex-specific",
                }
            )
        report = {
            "summary": "Codex-only audit",
            "groups": groups,
            "questions": [],
            "risks": [],
            "proposedSteps": ["Install Claude Code later"],
        }

        with mock.patch("agent_worktrees.audit.invoke_structured", return_value=report):
            result = run_audit(
                self.fixture.primary,
                self.fixture.config,
                "codex",
                ("codex",),
            )

        self.assertEqual(result["auditedSides"], ["codex"])


class WalkthroughOnboardingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RepositoryFixture()
        bootstrap_install(self.fixture.primary)

    def tearDown(self) -> None:
        self.fixture.close()

    def test_missing_codex_returns_one_install_action(self) -> None:
        statuses = {
            "claude": {"installed": True, "authenticated": True, "version": "2.0"},
            "codex": {"installed": False, "authenticated": False, "version": None},
        }
        with mock.patch("agent_worktrees.walkthrough.executable_exists", return_value=True):
            result = _agent_setup_action(statuses, "claude")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "needs_tool_install")
        self.assertEqual(result["tool"], "codex")
        self.assertEqual(result["verify"], "codex --version")

    def test_installed_claude_without_login_returns_auth_action(self) -> None:
        statuses = {
            "codex": {"installed": True, "authenticated": True, "version": "1.0"},
            "claude": {"installed": True, "authenticated": False, "version": "2.0"},
        }

        result = _agent_setup_action(statuses, "codex")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "needs_tool_auth")
        self.assertEqual(result["command"], "claude auth login")

    def test_agent_status_checks_native_authentication(self) -> None:
        responses = [
            subprocess.CompletedProcess(["codex", "--version"], 0, "codex 1.2\n", ""),
            subprocess.CompletedProcess(["codex", "login", "status"], 0, "Logged in\n", ""),
        ]
        with (
            mock.patch("agent_worktrees.walkthrough.executable_exists", return_value=True),
            mock.patch("agent_worktrees.walkthrough.run", side_effect=responses),
        ):
            result = agent_status("codex")

        self.assertTrue(result["installed"])
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["version"], "codex 1.2")

    def test_claude_only_start_audits_only_claude(self) -> None:
        statuses = {
            "claude": {"installed": True, "authenticated": True, "version": "2.0"},
            "codex": {"installed": False, "authenticated": False, "version": None},
        }
        report = {"summary": "Claude-only audit", "questions": []}
        with (
            mock.patch("agent_worktrees.walkthrough._preflight", return_value=[]),
            mock.patch("agent_worktrees.walkthrough.agent_statuses", return_value=statuses),
            mock.patch("agent_worktrees.walkthrough.run_audit", return_value=report) as audit,
        ):
            result = run_walkthrough(
                self.fixture.primary,
                self.fixture.config,
                "claude",
            )

        self.assertEqual(result["status"], "needs_approval")
        audit.assert_called_once_with(
            self.fixture.primary,
            self.fixture.config,
            "claude",
            ("claude",),
        )

    def test_codex_only_start_audits_only_codex(self) -> None:
        statuses = {
            "codex": {"installed": True, "authenticated": True, "version": "1.0"},
            "claude": {"installed": False, "authenticated": False, "version": None},
        }
        report = {"summary": "Codex-only audit", "questions": []}
        with (
            mock.patch("agent_worktrees.walkthrough._preflight", return_value=[]),
            mock.patch("agent_worktrees.walkthrough.agent_statuses", return_value=statuses),
            mock.patch("agent_worktrees.walkthrough.run_audit", return_value=report) as audit,
        ):
            result = run_walkthrough(
                self.fixture.primary,
                self.fixture.config,
                "codex",
            )

        self.assertEqual(result["status"], "needs_approval")
        audit.assert_called_once_with(
            self.fixture.primary,
            self.fixture.config,
            "codex",
            ("codex",),
        )


if __name__ == "__main__":
    unittest.main()
