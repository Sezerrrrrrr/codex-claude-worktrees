from __future__ import annotations

import unittest

from agent_worktrees.installer import TEMPLATES
from agent_worktrees.security import contains_secret


class PublicForgeBundleTest(unittest.TestCase):
    def test_bundle_contains_no_secrets_or_machine_specific_paths(self) -> None:
        forge = TEMPLATES / "codex/.agents/skills/forge"
        self.assertTrue(forge.is_dir())
        for path in forge.rglob("*"):
            self.assertFalse(path.is_symlink())
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertFalse(contains_secret(text), path.as_posix())
            self.assertNotIn("/Users/", text, path.as_posix())


if __name__ == "__main__":
    unittest.main()
