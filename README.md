# Codex + Claude Worktrees

Use Codex and Claude Code on the same code without copying files or maintaining compatibility
adapters. The toolkit creates five permanent Git worktree lanes, gives both agents complete native
configuration, and keeps equivalent instructions, skills, hooks, and settings aligned.

> Alpha: version 0.2 targets macOS, iTerm2, zsh, GitHub, Python 3.11+, Codex CLI, and Claude Code.

## The 60-second mental model

| Lane | Shared folder | Local branch | GitHub branch | Claude Code | Codex |
|---|---|---|---|---|---|
| A | `.codex/worktrees/a` | `a` | `origin/a` | `za` | `zac` |
| B | `.codex/worktrees/b` | `b` | `origin/b` | `zb` | `zbc` |
| C | `.codex/worktrees/c` | `c` | `origin/c` | `zc` | `zcc` |
| D | `.codex/worktrees/d` | `d` | `origin/d` | `zd` | `zdc` |
| E | `.codex/worktrees/e` | `e` | `origin/e` | `ze` | `zec` |

Open two iTerm tabs. Type `za` in one to use Claude Code and `zac` in the other to use Codex.
Both commands enter the exact same folder, so a file changed by one agent is immediately visible to
the other. No pull or synchronization is needed when switching agents.

The `.codex/worktrees` folder name is only a location. It does not change Claude Code's behavior:
Claude still loads `CLAUDE.md`, `.claude/settings.json`, `.claude/skills`, and `.claude/hooks`.

Use only one writing agent in a lane at a time. For simultaneous work, use different letters.

## Quick start

Prerequisites:

- A GitHub repository with an `origin` remote
- `git`, `gh`, Python 3.11+, and at least one of Codex CLI or Claude Code
- `gh auth status` succeeds

Clone this toolkit, then seed the walkthrough into the project you want to configure:

```bash
python3 -m pip install -e /path/to/codex-claude-worktrees
agent-worktrees install /path/to/your-project
```

Start either agent in that project and invoke its native walkthrough:

```text
Codex:       $walkthrough
Claude Code: /walkthrough
```

It is fine if only one coding agent is installed or configured. Start in the one you already use.
The walkthrough audits only that working setup, builds the missing provider's complete native files,
then gives you one install or login action at a time for the other tool. It verifies the second tool
before enabling shared worktrees and parity. Credentials stay in each provider's normal browser
login flow; the toolkit never asks you to paste them.

The walkthrough audits before it changes the existing agent setup. It pauses for permission before
creating GitHub branches, changing `~/.zshrc`, configuring notifications, or enabling hooks. Setup
files must be reviewed and landed on the repository's default branch before the permanent worktrees
are created.

## What the walkthrough does

1. Checks Git, GitHub, Python, and whichever coding agent launched the walkthrough.
2. Detects which coding agents are installed and authenticated.
3. Inventories only the configured side or sides: project and user-level instructions, skills,
   hooks, and settings.
4. Runs a deep read-only semantic audit:
   - Codex invocation: GPT-5.6 Sol at high effort
   - Claude invocation: Fable 5 at high effort
5. Asks which side wins any ambiguous or conflicting group.
6. Builds complete native counterparts in staging—never adapters or symlinks.
7. If necessary, walks through installing and logging into the missing tool:
   - Codex: install the CLI, run `codex login`, then verify with `codex login status`.
   - Claude Code: install the CLI, run `claude auth login`, then verify with `claude auth status`.
8. Validates and baselines the parity ledger.
9. Helps land the setup on the GitHub default branch.
10. Creates lanes `a` through `e` and matching `origin/<letter>` branches.
11. Installs `za`/`zac` through `ze`/`zec` into a marked `~/.zshrc` block.
12. Configures and tests native Codex and Claude Code notifications.
13. Enables the parity/checkpoint Stop hooks after native trust review.

If the preferred audit model is unavailable, setup stops instead of silently using a weaker model.

## Native parity, not adapters

Codex reads only Codex-native files. Claude Code reads only Claude-native files. A neutral,
deterministic engine compares a manifest and hash ledger after a turn:

- One native side changed: an isolated model translates the semantic change into the other native format.
- Both sides changed: the hook stops and asks which side is authoritative.
- The translation is uncertain: no target file is modified.
- Nothing relevant changed: no model runs.

Ongoing parity uses GPT-5.6 Sol at medium effort from Codex and Fable 5 at medium effort from Claude
Code. Translation happens in a temporary staging directory, is path-allowlisted, secret-scanned,
syntax-checked, and applied atomically. CI uses only deterministic checks.

The separately installed `agent-worktrees` Python package is the neutral execution engine, not an
instruction adapter. The target repository does not receive a mutable executable runtime. Each
provider retains its own full skill, trusted hook definition, settings, and instruction files; the
hook calls the installed executable directly.

## Automatic checkpoints

When enabled, the serialized Stop hook runs parity first, then creates a meaningful commit and pushes
only the matching workspace branch. It verifies all three identities before touching Git:

```text
folder a → local branch a → upstream origin/a
```

It refuses detached HEAD, merge/rebase conflicts, sensitive-looking files, `main`, `master`, branch
name mismatches, binary files, and ordinary force-pushes. Binary changes require a deliberate manual
commit after review; the automatic hook will not stage or approve them. These commits are workspace
backups and may represent intermediate work; `$ship`/`/ship` squash-merges them into clean
default-branch history.

## Pull and ship

`$pull` in Codex or `/pull` in Claude Code:

- checkpoints the lane;
- reconciles its GitHub letter branch;
- rebases it onto the GitHub default branch;
- asks the active agent to resolve semantic conflicts;
- updates only `origin/<letter>` using `--force-with-lease` when the rebase rewrote history.

`$ship` or `/ship` means **land on GitHub**, not deploy:

- runs pull and configured validation commands;
- creates or reuses the lane's GitHub PR;
- waits for required checks;
- squash-merges it;
- creates a local recovery ref;
- resets the local and remote letter lane to the merged default branch.

After a successful ship:

```text
local a == origin/a == origin/<default-branch>
```

An ordinary Stop hook never creates or merges a PR and never pushes the default branch.

## Optional Forge workflow — Codex only

Forge is an optional, durable product-to-code workflow included with this toolkit. It is available
only as the Codex `$forge` skill; there is intentionally no Claude Code `/forge` counterpart.
Forge still requires an installed and authenticated Claude Code CLI because Codex launches Fable as
an isolated read-only specification partner.

Install it into a configured project:

```bash
agent-worktrees install-forge /path/to/your-project
```

Then start a new Codex session in one permanent lane, review and trust the changed project hook with
`/hooks`, and invoke:

```text
$forge Build the requested feature
$forge max Build the requested feature at maximum common effort
```

The default effort is `high` for Fable, GPT-5.6 Sol, and GPT-5.6 Terra. An optional `low`, `medium`,
`high`, `xhigh`, or `max` parameter applies to all three. Kimi K3 always uses its own highest
supported effort, `xhigh`.

Forge compacts automatically at 40% context use and restores a durable record containing the exact
current specification, exact visible user/Fable conversation, completed-task reports, validations,
and implementation deviations or Sol escalation rationale. Sol receives exactly one bounded
technical review during specification; Fable and the user finish the specification afterward.

Installation adds the Codex-only skill, a Codex `SessionStart(compact)` hook, `.forge-state/` to the
project ignore file, and the project-level Codex compaction threshold. The 40% Codex threshold
therefore applies to every new Codex session in that project, not only Forge. If parity is already
baselined, review the added Codex-only group and run `agent-worktrees parity baseline` afterward.

Kimi frontend routing requires the separate isolated CC Switch profile described in
[docs/forge.md](docs/forge.md). Forge stops rather than silently substituting a different model.

## Git words without the jargon

- **Worktree:** another folder containing the same repository. Each lane can have different code.
- **Local branch:** the lane's history on your computer.
- **Remote branch:** the lane's GitHub backup and synchronization point.
- **Default branch:** the integrated version, usually `main`.
- **Topic branch:** a disposable feature-named branch. This system uses persistent letter lanes instead.

Do not rename or delete `a`–`e` when the feature changes. Name the Codex or Claude conversation after
the feature; keep the worktree and branch names stable for months.

To add lane F, add `"f"` to `.agent-worktrees/config.json`, then explicitly run:

```bash
agent-worktrees ensure-lanes
agent-worktrees install-shortcuts
```

This creates `.codex/worktrees/f`, local branch `f`, `origin/f`, `zf`, and `zfc` using the same rules.

## Recovery

- A failed checkpoint leaves the commit locally and never retries with force.
- A pull conflict leaves the Git rebase open so the active agent can resolve it and re-run the skill.
- A ship creates `refs/agent-worktrees/backups/<letter>/<timestamp>` before resetting a lane.
- The shortcut and notification installers back up modified user configuration files.
- Walkthrough approvals are private machine state stored in the repository's Git metadata, never in
  a tracked working-tree file.
- Re-running installation and walkthrough steps is idempotent; existing conflicting native files are
  preserved and reported instead of overwritten.

See [docs/recovery.md](docs/recovery.md) for exact commands.

## Project status

This is an unofficial community project and is not affiliated with or endorsed by OpenAI or
Anthropic. Codex and Claude are trademarks of their respective owners.
