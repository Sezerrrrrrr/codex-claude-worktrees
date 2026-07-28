from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_worktrees.audit import InventoryEntry, _stage, _user_entries
from agent_worktrees.checkpoint import _scan_staged
from agent_worktrees.common import AgentWorktreesError
from agent_worktrees.config import ensure_default_config, load_config
from agent_worktrees.installer import bootstrap_install
from agent_worktrees.models import _child_environment
from agent_worktrees.parity import load_manifest
from agent_worktrees.security import contains_secret, safe_repository_path, sensitive_path
from agent_worktrees.walkthrough import _save, _state_path


class SecurityBoundaryTest(unittest.TestCase):
    def test_common_credentials_and_sensitive_paths_are_detected(self) -> None:
        self.assertTrue(contains_secret("access_token='abcdefghijklmnopqrstuvwx'"))
        self.assertTrue(contains_secret("AWS_SESSION_TOKEN='abcdefghijklmnopqrstuvwx'"))
        self.assertTrue(contains_secret("BILLING_SECRET_KEY='abcdefghijklmnopqrstuvwx'"))
        self.assertTrue(contains_secret("AWS_SESSION_TOKEN=abcdefghijklmnopqrstuvwx"))
        self.assertTrue(
            contains_secret("BILLING_SECRET_KEY=abcdefghijklmnopqrstuvwx")
        )
        self.assertTrue(contains_secret("postgres://operator:private-value@database.example/app"))
        self.assertTrue(sensitive_path("config/service-account.json"))
        self.assertTrue(sensitive_path(".npmrc"))
        self.assertFalse(contains_secret("access_token='your-placeholder'"))
        self.assertFalse(sensitive_path(".env.example"))

    def test_audit_never_follows_native_configuration_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private.txt"
            private.write_text("confidential instructions\n", encoding="utf-8")
            linked = root / "AGENTS.md"
            linked.symlink_to(private)
            stage = root / "stage"
            entry = InventoryEntry("project", "codex", "AGENTS.md", linked, True)

            result = _stage([entry], stage)

            self.assertTrue(result[0]["contentWithheld"])
            self.assertFalse((stage / "files").exists())

    def test_audit_withholds_an_entire_skill_with_nested_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / ".agents/skills/example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("safe instructions\n", encoding="utf-8")
            (skill / ".env").write_text("OPAQUE_VALUE\n", encoding="utf-8")
            entry = InventoryEntry(
                "project",
                "codex",
                ".agents/skills/example",
                skill,
                True,
            )

            result = _stage([entry], root / "stage")

            self.assertTrue(result[0]["contentWithheld"])

    def test_audit_withholds_an_entire_skill_with_binary_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / ".agents/skills/example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("safe instructions\n", encoding="utf-8")
            (skill / "payload.bin").write_bytes(b"\x00opaque-private-bytes")
            entry = InventoryEntry(
                "project",
                "codex",
                ".agents/skills/example",
                skill,
                True,
            )

            result = _stage([entry], root / "stage")

            self.assertTrue(result[0]["contentWithheld"])

    def test_audit_withholds_a_skill_containing_a_symlinked_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / ".agents/skills/example"
            outside = root / "private"
            skill.mkdir(parents=True)
            outside.mkdir()
            (outside / "notes.md").write_text("private notes\n", encoding="utf-8")
            (skill / "linked").symlink_to(outside, target_is_directory=True)
            stage = root / "stage"
            entry = InventoryEntry(
                "project",
                "codex",
                ".agents/skills/example",
                skill,
                True,
            )

            result = _stage([entry], stage)

            self.assertTrue(result[0]["contentWithheld"])
            self.assertFalse((stage / "files").exists())

    def test_user_level_settings_are_metadata_only_during_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = home / ".claude/settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text('{"env":{"TOKEN":"private"}}\n', encoding="utf-8")

            with mock.patch("agent_worktrees.audit.Path.home", return_value=home):
                entries = _user_entries()

            selected = next(entry for entry in entries if entry.path == "~/.claude/settings.json")
            self.assertFalse(selected.content_available)

    def test_config_rejects_git_option_injection_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = ensure_default_config(root)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["remote"] = "--upload-pack=malicious"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(AgentWorktreesError, "plain Git remote"):
                load_config(root)

            raw["remote"] = "origin"
            raw["worktreeRoot"] = "../../outside"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(AgentWorktreesError, "must remain"):
                load_config(root)

    def test_repository_paths_reject_symlinked_native_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".agents").symlink_to(outside, target_is_directory=True)

            self.assertFalse(safe_repository_path(root, ".agents/skills/walkthrough"))
            with self.assertRaisesRegex(AgentWorktreesError, "unsafe native install path"):
                bootstrap_install(root)
            self.assertFalse((outside / "skills/walkthrough/SKILL.md").exists())

    def test_state_directories_cannot_redirect_writes_outside_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".agent-worktrees").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(AgentWorktreesError, "real directory"):
                ensure_default_config(root)
            self.assertFalse((outside / "config.json").exists())

            (root / ".agent-worktrees").unlink()
            (root / ".agent-parity").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(AgentWorktreesError, "real directory"):
                load_manifest(root)

    def test_walkthrough_state_lives_in_git_metadata_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)

            _save(root, {"version": 1, "stage": "preflight", "approvals": []})

            path = _state_path(root)
            self.assertTrue(path.is_file())
            self.assertIn(".git/agent-worktrees", path.as_posix())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / ".agent-worktrees/walkthrough-state.json").exists())

    def test_bootstrap_rejects_repository_local_walkthrough_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            legacy = root / ".agent-worktrees/walkthrough-state.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                '{"version":1,"stage":"worktrees","approvals":["worktrees"]}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(AgentWorktreesError, "repository-local"):
                bootstrap_install(root)

    def test_child_model_environment_does_not_inherit_credentials(self) -> None:
        source = {
            "PATH": "/usr/bin",
            "HOME": "/tmp/home",
            "OPENAI_API_KEY": "private-openai-key-value",
            "ANTHROPIC_API_KEY": "private-anthropic-key-value",
            "AWS_SESSION_TOKEN": "private-aws-token-value",
        }
        with mock.patch.dict("agent_worktrees.models.os.environ", source, clear=True):
            environment = _child_environment()

        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["HOME"], "/tmp/home")
        self.assertEqual(environment["AGENT_WORKTREES_CHILD"], "1")
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("AWS_SESSION_TOKEN", environment)

    def test_staged_blob_scan_rejects_binary_and_composite_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            binary = root / "artifact.bin"
            binary.write_bytes(b"\x00private")
            subprocess.run(["git", "add", "artifact.bin"], cwd=root, check=True)
            with self.assertRaisesRegex(AgentWorktreesError, "binary file"):
                _scan_staged(root)

            subprocess.run(["git", "reset"], cwd=root, check=True, capture_output=True)
            binary.unlink()
            secret = root / "config.txt"
            secret.write_text(
                "AWS_SESSION_TOKEN='abcdefghijklmnopqrstuvwx'\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "config.txt"], cwd=root, check=True)
            with self.assertRaisesRegex(AgentWorktreesError, "secret material"):
                _scan_staged(root)


if __name__ == "__main__":
    unittest.main()
