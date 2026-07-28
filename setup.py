from pathlib import Path

from setuptools import find_packages, setup


package_root = Path(__file__).parent / "src/agent_worktrees"
package_data = [
    path.relative_to(package_root).as_posix()
    for directory in ("templates", "schemas")
    if (package_root / directory).exists()
    for path in (package_root / directory).rglob("*")
    if path.is_file()
]

setup(
    name="codex-claude-worktrees",
    version="0.1.0",
    description="Persistent Git worktree lanes shared by Codex and Claude Code.",
    long_description=(Path(__file__).parent / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    python_requires=">=3.11",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"agent_worktrees": package_data},
    include_package_data=True,
    entry_points={"console_scripts": ["agent-worktrees=agent_worktrees.cli:main"]},
    license="MIT",
)
