from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_worktrees.common import AgentWorktreesError, write_json
from agent_worktrees.config import ModelConfig, ProjectConfig
from agent_worktrees.parity import baseline, bootstrap, check, load_manifest, status


class ParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / ".agent-parity").mkdir()
        (self.root / "AGENTS.md").write_text("Codex instructions\n", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("Claude instructions\n", encoding="utf-8")
        write_json(
            self.root / ".agent-parity/manifest.json",
            {
                "version": 1,
                "groups": [
                    {
                        "name": "instructions",
                        "classification": "adapted",
                        "codex": ["AGENTS.md"],
                        "claude": ["CLAUDE.md"],
                    }
                ],
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_baseline_and_check(self) -> None:
        self.assertEqual(baseline(self.root)["status"], "baselined")
        self.assertEqual(check(self.root)["status"], "clean")

    def test_one_sided_change_is_reported(self) -> None:
        baseline(self.root)
        (self.root / "AGENTS.md").write_text("Changed Codex instructions\n", encoding="utf-8")
        changes = status(self.root)["changes"]
        self.assertEqual(changes["codex"], ["instructions"])
        self.assertEqual(changes["claude"], [])

    def test_symlink_and_unclassified_native_files_are_rejected(self) -> None:
        (self.root / ".codex").mkdir()
        (self.root / ".codex/extra.txt").write_text("unclassified\n", encoding="utf-8")
        with self.assertRaisesRegex(AgentWorktreesError, "unclassified"):
            baseline(self.root)

    def test_machine_local_claude_settings_are_outside_ledger(self) -> None:
        (self.root / ".claude").mkdir()
        (self.root / ".claude/settings.local.json").write_text("{}\n", encoding="utf-8")
        self.assertEqual(len(load_manifest(self.root)), 1)
        self.assertEqual(baseline(self.root)["status"], "baselined")

    def test_bootstrap_creates_a_missing_native_target_at_its_path_contract(self) -> None:
        (self.root / "CLAUDE.md").unlink()
        manifest = json.loads(
            (self.root / ".agent-parity/manifest.json").read_text(encoding="utf-8")
        )
        manifest["groups"][0]["bootstrapSource"] = "codex"
        write_json(self.root / ".agent-parity/manifest.json", manifest)
        model = ModelConfig("test", "high", "medium", "medium")
        config = ProjectConfig(
            remote="origin",
            worktree_root=".codex/worktrees",
            lanes=("a",),
            auto_checkpoint=True,
            validation_commands=(),
            codex=model,
            claude=model,
        )

        def translate(
            harness: str,
            stage: Path,
            prompt: str,
            schema: dict[str, object],
            model_config: ModelConfig,
            effort: str,
            **options: object,
        ) -> dict[str, object]:
            self.assertEqual(harness, "codex")
            self.assertIn("target=['CLAUDE.md']", prompt)
            (stage / "target").mkdir(parents=True, exist_ok=True)
            (stage / "target/CLAUDE.md").write_text(
                "Claude-native instructions\n", encoding="utf-8"
            )
            return {
                "status": "applied",
                "summary": "Created the native counterpart",
                "adaptations": ["Translated instructions"],
                "uncertainties": [],
                "questions": [],
            }

        with mock.patch("agent_worktrees.parity.invoke_structured", side_effect=translate):
            result = bootstrap(self.root, config, "codex")

        self.assertEqual(result["status"], "baselined")
        self.assertEqual(
            (self.root / "CLAUDE.md").read_text(encoding="utf-8"),
            "Claude-native instructions\n",
        )


if __name__ == "__main__":
    unittest.main()
