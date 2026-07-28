---
name: forge
description: Run a durable, strictly sequential product-to-code workflow in the current permanent worktree. Activate only when the user's current message contains the literal `$forge` command. Mentioning Forge, asking about prior Forge work or saved Forge state, or continuing a feature that previously used Forge does not activate this skill unless `$forge` is present. Fable develops the specification directly with the user, GPT-5.6 Sol performs one read-only technical challenge, Fable and the user finalize the specification, GPT-5.6 Terra implements, Kimi K3 handles frontend work, Sol advises read-only whenever implementation or tests encounter difficulty, Terra applies the solution, and testing repeats until it passes.
---

# Forge

Forge is a Codex-only coordinator skill. Start it from Codex with `$forge`; Claude Code does not
load or expose `/forge`. Forge may launch the authenticated Claude Code CLI as its isolated Fable
specification subprocess, but the durable state machine, routing, approval gates, implementation,
and recovery remain controlled by the originating Codex session.

## Activation

Run Forge only when the user's current message explicitly contains `$forge`.

Accepted invocation forms are `$forge <request>` and `$forge <effort> <request>`, where effort is
`low`, `medium`, `high`, `xhigh`, or `max`. The default is `high`. A supplied effort applies equally
to Fable, Sol, and Terra for the entire run. Kimi K3 always uses its own highest supported effort,
`xhigh`, including when the common override is lower or `max`.

Do not activate Forge because the user:

- mentions Forge without the `$forge` command;
- asks about saved Forge work or its status;
- continues ordinary implementation of a feature that previously used Forge.

Those requests use the normal repository workflow.

## Non-Negotiable Architecture

Run one model at a time, always in the worktree where `$forge` was invoked.

- Worktree `a` stays on branch `a`; `b` stays on `b`; the same rule applies through `e`.
- Record the invocation worktree and branch when the feature starts.
- Resume only from that same worktree and matching branch.
- Fable, Sol, Terra, Kimi, and tests all read or write that same checkout.
- Never distribute Forge work across worktrees.
- Never synchronize, cherry-pick, or integrate Forge tasks between letter branches.
- Terra and Kimi write sequentially. Sol and Fable are read-only.
- Commit the completed implementation and every repair to the invocation branch before testing.
- Forge never pushes, merges, deploys, places calls, applies production migrations, or triggers billable actions.

The repository checkpoint hook may push a committed `a`–`e` branch only to its matching
`origin/<letter>`. That is separate from Forge.

## Model Order

Use this exact lifecycle:

1. Fable and the user build the product specification through a visible conversation.
2. GPT-5.6 Sol performs one bounded read-only technical challenge.
3. Send Sol's relevant findings to the same Fable session.
4. Relay Fable's complete response to the user verbatim.
5. Continue Fable/user discussion until the user approves the reconciled specification.
6. GPT-5.6 Terra implements backend, application, data, integration, and nonvisual frontend work.
7. Kimi K3 implements frontend visual and interaction work when the approved plan reaches it.
8. If implementation encounters any difficulty, Sol diagnoses read-only and gives the active implementer a bounded solution.
9. After implementation is committed, run the approved test suite.
10. If any test fails, Sol diagnoses read-only, Terra implements the repair, the repair is committed, and the complete affected tests run again.
11. Finish when all required tests pass against the current committed branch HEAD.

Never run these model stages concurrently.

## Bootstrap and Durable State

Resolve this skill directory and set:

```bash
FORGE="$SKILL_DIR/scripts/forge.py"
```

Before starting or resuming:

```bash
python3 "$FORGE" doctor
```

Doctor validates only the current invocation worktree, not `a` through `e` collectively.

Create a feature from the current worktree. Omit `--effort` for the `high` default, or pass the
explicit `$forge` effort:

```bash
python3 "$FORGE" init FEATURE_ID --title "Feature title" --effort high --request-file /path/to/request.md
```

Resume after a new turn or context compaction:

```bash
python3 "$FORGE" session-bind FEATURE_ID
python3 "$FORGE" show FEATURE_ID
```

State lives at `<main-repository>/.forge-state/<feature-id>/`, outside individual worktrees. It
stores the invocation worktree, specification, task packets, reports, advice, attempts, evidence,
current phase, effort, exact visible specification dialogue, and a generated canonical memory
record. Never rely on chat history alone.

Codex, Fable, Terra, Sol, and Kimi compact automatically at 40% of their configured context. The
current Forge specification and visible user/Fable messages remain verbatim. Completed task
reports, validation state, and implementation decisions or deviations remain in the durable audit
trail. The native `SessionStart(source=compact)` hook restores that canonical record into the
immediate continuation, so compaction does not require a user handoff or interrupt the run.

The user can reactivate a compacted run with:

```text
$forge resume FEATURE_ID
```

Load `show`, the approved specification, current task packet, latest report/advice, and applicable
repository guidance before continuing.

## Stage 1: Intake

Record the requested outcome, constraints, existing work, affected systems, risks, and explicit
exclusions. Do not implement.

```bash
python3 "$FORGE" transition FEATURE_ID SPECIFICATION --note "Intake complete"
```

## Stage 2: Fable Specification Conversation

Prepare the bounded product packet described in `references/records.md`:

```bash
python3 "$FORGE" fable-start FEATURE_ID --purpose specification --prompt-file /path/to/fable-packet.md
```

Forge invokes Fable at the run's common effort (`high` by default).

Return Fable's entire stdout verbatim as the complete assistant response and stop. Do not summarize,
annotate, translate, answer Fable's questions, or continue another stage in that turn.

Save the user's next message verbatim and resume the same Fable session:

```bash
python3 "$FORGE" fable-resume FEATURE_ID --purpose specification --user-reply-file /path/to/verbatim-user-reply.md
```

Repeat until Fable produces a complete draft and the user is ready for technical challenge. Record
the draft:

```bash
python3 "$FORGE" spec-set FEATURE_ID --file /path/to/spec.md --version 1
python3 "$FORGE" transition FEATURE_ID TECHNICAL_REVIEW --note "Specification draft ready"
```

## Stage 3: One Sol Technical Challenge, Then Back to Fable

Run exactly one bounded pre-implementation Sol review:

```bash
python3 "$FORGE" advice-run FEATURE_ID --purpose technical-review --prompt-file /path/to/review-packet.md
```

Sol is read-only and gets exactly one technical-review pass. It focuses only on feasibility,
architecture, provider capability, security, migrations, and correctness directly implicated by
the specification. It may inspect named or directly implicated files plus one dependency hop, with
at most 12 read/search calls in one focused exploration batch. It must not inventory the codebase,
follow tangential systems, reopen settled product choices, invent policy work, add speculative
scope, or write code.

Send valid Sol findings to the same Fable session:

```bash
python3 "$FORGE" fable-resume FEATURE_ID --purpose specification --evidence-file /path/to/sol-findings-for-fable.md
```

Relay Fable verbatim and continue the Fable/user conversation until the user accepts the final
specification. Do not run Sol over the specification again.

Record the reconciled version while still in `TECHNICAL_REVIEW`:

```bash
python3 "$FORGE" spec-set FEATURE_ID --file /path/to/reconciled-spec.md --version 2
```

## Stage 4: Sequential Task and Test Plan

Create tasks in execution order. Use Terra for backend/application/data/integration/testing work and
Kimi for substantial visual frontend work. Every task automatically belongs to the invocation
worktree; task packets must not assign another worktree.

```bash
python3 "$FORGE" task-add FEATURE_ID --task T01 --owner terra --packet-file /path/to/task.md
python3 "$FORGE" task-add FEATURE_ID --task T02 --owner kimi --depends-on T01 --packet-file /path/to/frontend.md
python3 "$FORGE" validation-require FEATURE_ID --name "feature-suite" --task T01 --task T02
```

Define every required validation before approval. Then:

```bash
python3 "$FORGE" transition FEATURE_ID AWAITING_APPROVAL --note "Technical challenge reconciled and execution plan recorded"
```

Show the user the final scope, exclusions, acceptance criteria, implementation order, tests, and
material risks. Do not infer approval.

After explicit approval:

```bash
python3 "$FORGE" approve FEATURE_ID --version 2 --approved-by user
```

## Stage 5: Sequential Implementation in the Invocation Worktree

Run one task at a time:

```bash
python3 "$FORGE" worker-run FEATURE_ID --task T01 --worker terra
python3 "$FORGE" release FEATURE_ID --task T01 --status completed --report-file /path/printed/by/worker-run
```

For an approved frontend task:

```bash
python3 "$FORGE" worker-run FEATURE_ID --task T02 --worker kimi
python3 "$FORGE" release FEATURE_ID --task T02 --status completed --report-file /path/printed/by/worker-run
```

Kimi always runs at `xhigh` reasoning effort. If Kimi or its isolated provider is unavailable, stop
and report the blocker; do not silently replace Kimi with Terra.

Workers inspect the whole current checkout but change only their bounded packet. They do not push,
deploy, call live services, or trigger billable actions.

If implementation must differ from the approved specification, stop and record the decision before
continuing:

```bash
python3 "$FORGE" decision-add FEATURE_ID --kind spec-deviation --task T01 \
  --summary "What changed" --reason "Why the approved approach could not be followed" \
  --source-file /path/to/supporting-report.md
```

This audit trail is included in every later compaction so a deliberate deviation is not mistaken
for unfinished work.

After every approved implementation task is complete, inspect the complete diff, run narrow checks,
and create a semantic commit on the current matching letter branch. Then enter testing:

```bash
python3 "$FORGE" transition FEATURE_ID VERIFICATION --note "Implementation committed; begin testing"
```

## Implementation Difficulty Loop

At any implementation difficulty—failed command, unclear failure, blocked dependency, unexpected
behavior, or a worker report marked failed/blocked—stop implementation and use Sol immediately:

1. Save an escalation packet using `references/records.md`.
2. Move to `ESCALATION` if the failure did not do so automatically.
3. Run Sol read-only:

   ```bash
   python3 "$FORGE" advice-run FEATURE_ID --purpose diagnosis --prompt-file /path/to/escalation.md
   ```

4. Return to implementation:

   ```bash
   python3 "$FORGE" transition FEATURE_ID IMPLEMENTATION --note "Apply Sol diagnosis"
   ```

5. Give Sol's report to Terra, or to Kimi when the unresolved task is specifically visual frontend
   work.
6. The implementer applies the bounded solution and reruns the failing check.

Sol never writes code and never takes over implementation.

## Stage 6: Testing and Repair Loop

Run every declared validation in the invocation worktree:

```bash
python3 "$FORGE" validation-run FEATURE_ID --name "feature-suite" --task T01 --task T02 --command -- npm test
```

Evidence is bound to the current committed branch HEAD.

If a test fails, Forge records the failure and enters `ESCALATION`. Then:

1. Give Sol the failing command, complete relevant output, reproduction, diff, and environmental facts.
2. Run `advice-run --purpose diagnosis`.
3. Transition to `IMPLEMENTATION`.
4. Reopen the affected task when needed.
5. Terra implements Sol's recommendation.
6. Commit the repair on the same invocation branch.
7. Transition back to `VERIFICATION`.
8. Rerun the failed test and every affected broader validation.

Repeat this Sol-diagnosis → Terra-repair → test loop until every required validation passes.

## Complete

Finalize only when:

- the current specification is approved;
- every task has a completion report;
- every required validation passes with required evidence against current committed HEAD;
- no blocking finding remains;
- the invocation worktree is clean;
- the current branch contains the implementation commit.

```bash
python3 "$FORGE" finalize FEATURE_ID
```

There is no cross-worktree integration stage, post-implementation Sol review, Sol implementation
takeover, or final Fable acceptance stage. Once the approved tests pass, Forge is complete.

Report delivered scope, the final commit, validation evidence, and any nonblocking residual risks.

## References

- `references/workflow.md`: concise state machine and routing rules.
- `references/records.md`: specification, task, escalation, validation, and completion formats.
- `references/configuration.md`: Fable, Kimi, state storage, current-worktree binding, and safety.
