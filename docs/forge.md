# Forge — optional Codex-only workflow

Forge turns a feature request into a user-approved specification, sequential implementation, and
evidence-backed verification in one permanent letter worktree. It is intentionally installed only
under `.agents/skills/forge` and starts only when a Codex message contains the literal `$forge`.

Claude Code does not load this skill and has no `/forge` command. Codex invokes the authenticated
Claude Code CLI only as Fable, a read-only product/specification subprocess.

## Requirements

- Codex CLI with access to GPT-5.6 Sol and GPT-5.6 Terra.
- Claude Code CLI, authenticated, with the `fable` model alias available.
- The permanent `a`–`e` worktrees created by this toolkit.
- For visual frontend tasks: Kimi K3 through an isolated CC Switch-managed Codex home.

## Installation

```bash
agent-worktrees install-forge /path/to/your-project
```

Review the installed files before accepting them. Start a new Codex session so the project
compaction configuration loads, then open `/hooks` and trust the new project hook. If the project
already has a parity ledger, inspect the new Codex-only group and run:

```bash
agent-worktrees parity baseline
```

The installer never creates a Claude `/forge` skill. It does modify `.codex/config.toml` so Codex
automatically compacts at 40% in every new session for that project. This project-wide setting is
required because Codex selects the automatic-compaction threshold when the coordinator session
starts.

## Invocation and effort

```text
$forge <request>
$forge low <request>
$forge medium <request>
$forge high <request>
$forge xhigh <request>
$forge max <request>
```

Without a parameter, Fable, Sol, and Terra run at `high`. The parameter applies equally to all
three. Kimi always stays at `xhigh`, its highest supported effort.

## Model sequence

1. Fable and the user create the specification.
2. Sol performs exactly one bounded read-only technical strengthening pass.
3. Fable and the user reconcile and approve the final specification.
4. Terra implements application, backend, data, integration, and test work.
5. Kimi handles approved visual frontend work.
6. On a real implementation or test difficulty, Sol diagnoses read-only and the active implementer
   applies the bounded recommendation.
7. Verification repeats until every declared check passes.

Every stage stays in the invocation worktree and its matching letter branch. Forge does not push,
merge, deploy, place calls, apply production migrations, or perform cross-worktree integration.

## Durable compaction memory

Forge writes state under the main repository's ignored `.forge-state/<feature>/` directory. Every
state change regenerates `memory/handoff.md` with:

- the exact current specification;
- the exact visible messages exchanged by the user and Fable;
- task completion reports and latest validation evidence;
- implementation decisions, deliberate specification deviations, and why they occurred;
- applied Sol diagnoses and their rationale.

Codex and worker processes compact automatically at 40%. The compaction hook restores the canonical
record into the immediate continuation, so the user does not need to initiate or reconstruct a
handoff.

Do not put credentials, private customer data, or regulated information in Forge requests, task
packets, reports, or state. `.forge-state/` is ignored, but it is still plaintext on the local
machine.

## Kimi setup

Forge requires Kimi K3 for approved visual frontend tasks and will not silently replace it. Keep
the normal OpenAI Codex credentials separate from the CC Switch provider:

```text
~/.codex                 normal Codex
~/.codex-kimi-router     CC Switch-managed Kimi route
~/.codex-kimi            stable Forge Kimi snapshot
```

After selecting Kimi/Moonshot and enabling local routing in CC Switch, run:

```bash
bash .agents/skills/forge/scripts/forge-kimi-bootstrap.sh
python3 .agents/skills/forge/scripts/forge.py doctor
```

The bootstrap copies provider authentication into the private `~/.codex-kimi` directory with mode
`0600`. It never stores credentials in the repository or Forge state.
