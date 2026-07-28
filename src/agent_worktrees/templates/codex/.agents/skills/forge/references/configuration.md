# Forge Configuration

## Durable State

Default state location:

```text
<main-repository>/.forge-state/<feature-id>/
```

Override for tests:

```bash
export FORGE_STATE_HOME=/path/to/state
```

State is shared across the repository's permanent worktrees, but each feature records exactly one
invocation worktree and branch. Resume that feature only from the recorded checkout.

State contains no credentials and `.forge-state/` must remain gitignored.

## Current-Worktree Binding

Forge may start only from permanent worktree/branch `a`, `b`, `c`, `d`, or `e`, with directory and
branch matching. It records:

- worktree letter;
- absolute checkout path;
- branch;
- baseline HEAD.

Every Fable, Sol, Terra, Kimi, and validation command runs from that path. No other permanent
worktree needs to be clean, synchronized, available, or related to the current feature.

The current checkout may be configured through the existing `worktrees` map when installation paths
differ:

```json
{
  "worktrees": {
    "a": { "path": "/absolute/path/to/codex-a", "branch": "a" },
    "b": { "path": "/absolute/path/to/codex-b", "branch": "b" }
  }
}
```

Forge never resets, stashes, switches, cleans, merges, or synchronizes worktrees.

`doctor` distinguishes general resume readiness from `ready_for_new_feature`. A dirty invocation
worktree may be valid while resuming recorded implementation, but `init` always requires a clean
checkout.

## Fable

Forge invokes Claude Code with an isolated settings source containing only the native
post-compaction memory hook, plus an empty strict MCP configuration. It uses:

- model alias `fable` by default;
- the Forge run's common reasoning effort (`high` by default) on every start and resume;
- plan permission mode;
- read/search tools only;
- one persistent UUID session;
- no project/global settings, skills, plugins, MCP, or commands.

Fable addresses the user directly. Forge records and prints each complete response unchanged. The
coordinator relays it verbatim and stops until the user replies.

Override only the model alias:

```bash
export FORGE_FABLE_MODEL=fable
```

## Reasoning Effort

`$forge` defaults to `high` for Fable, GPT-5.6 Sol, and GPT-5.6 Terra. The optional invocation
parameter `low`, `medium`, `high`, `xhigh`, or `max` sets the same effort for all three models and is
stored with the durable feature state. It may also be configured with `FORGE_REASONING_EFFORT` or
the `reasoning_effort` key in the Forge JSON config. Invocation parameters take precedence.

Kimi K3 is intentionally excluded from the common override and always uses its highest supported
effort, `xhigh`.

## Terra and Sol

Forge routes implementation and repair work to GPT-5.6 Terra at the common run effort.

Forge routes Sol as GPT-5.6 Sol in read-only mode for:

- exactly one pre-implementation technical challenge;
- implementation difficulty diagnosis;
- test-failure diagnosis.

Sol never receives workspace-write permission and has no takeover route.

## Automatic Compaction and Durable Memory

Forge configures automatic compaction at 40% of the active model's context. GPT-5.6 workers use a
108,800-token threshold for the 272,000-token context, and Kimi uses 104,858 for 262,144. Fable is
started with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40`.

Every Forge state mutation regenerates `.forge-state/<feature>/memory/handoff.md`. It contains the
exact current specification, exact visible user/Fable messages, completed-task reports, task and
validation status, and the rationale/source for every recorded decision, deviation, and applied Sol
diagnosis. Background tool calls and exploration are not copied into the dialogue record.

The native Codex coordinator hook and Forge's isolated Fable hook load this file only for the active
Forge feature bound to that process. Bind each resumed Codex coordinator session with
`forge.py session-bind`.
The project Codex threshold is loaded when a Codex session starts; after installing or updating
these files, start a new Forge coordinator session and approve the changed project hook in `/hooks`.

Forge itself is available only in Codex. Claude Code is required because Codex invokes it as Fable,
but Claude Code does not receive a `/forge` skill or become a Forge coordinator.

## Kimi and CC Switch

Kimi K3 owns approved visual frontend tasks and always uses
`model_reasoning_effort="xhigh"`, its highest supported effort.

Use separate Codex homes so CC Switch does not replace primary OpenAI credentials:

- `~/.codex`: normal Terra/Sol Codex;
- `~/.codex-kimi-router`: CC Switch-managed provider state;
- `~/.codex-kimi`: stable Forge Kimi profile.

Configure once:

1. Configure CC Switch to manage `~/.codex-kimi-router`, not `~/.codex`.
2. Select the user's Kimi/Moonshot provider and enable local routing.
3. Run:

   ```bash
   bash "$SKILL_DIR/scripts/forge-kimi-bootstrap.sh"
   ```

4. Run `python3 "$SKILL_DIR/scripts/forge.py" doctor`.

Default Kimi home:

```text
~/.codex-kimi
```

Override with:

```bash
export FORGE_KIMI_CODEX_HOME=/path/to/kimi-codex-home
```

If Kimi, CC Switch, or the local proxy is unavailable, a Kimi-owned frontend task stops as blocked.
Forge does not silently substitute Terra.

Never put provider credentials in Forge state, task packets, reports, or chat.

## Browser Evidence

- Use Playwright MCP only for interactive investigation.
- Use repository Playwright tests for reproducible verification.
- Capture screenshots, viewports, tested interactions, console errors, and failed requests for
  visual tasks.
- Run browser work from the same invocation worktree.

## Locks

Forge uses:

- a per-feature model lock to prevent concurrent Fable/Sol/Terra/Kimi processes;
- a short-lived lock for the invocation worktree while a model or validation command runs.

Locks coordinate Forge processes but do not claim other permanent worktrees.

If a process crashes, inspect its state and checkout before releasing a stale lock. Never delete a
lock blindly.

## Safety

- Activate only from a literal `$forge` command.
- Run one model at a time.
- Do not implement before explicit specification approval.
- Do not run a second pre-implementation Sol review.
- Keep Sol read-only.
- Do not push, merge, deploy, apply production migrations, place calls, or trigger billable actions.
- Do not store credentials, PHI, or customer identifiers in prompts, logs, reports, or state.
- Preserve repository confirmation gates and current-worktree checkpoint rules.
