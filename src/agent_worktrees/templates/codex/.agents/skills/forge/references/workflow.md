# Forge Workflow Reference

## Roles

| Role | Model/harness | Responsibility |
| --- | --- | --- |
| Coordinator | Current Codex session | Durable state, verbatim relay, approval, sequential routing, evidence, final report |
| Product partner | Fable, common effort (high default) | Direct specification conversation with the user |
| Implementer | GPT-5.6 Terra, common effort (high default) | Backend, application, data, integrations, nonvisual logic, repairs, and tests |
| Frontend specialist | Kimi K3, xhigh effort | Visual frontend, interactions, responsive states, and browser-visible refinement |
| Technical advisor | GPT-5.6 Sol, common effort (high default), read-only | One bounded pre-implementation challenge plus implementation/test diagnosis |

Sol never writes code or takes over implementation.

## Activation

The user's current message must contain the literal `$forge` command. Conversational mentions,
status questions, or continuation of a feature previously planned with Forge do not activate it.
An optional `low`, `medium`, `high`, `xhigh`, or `max` parameter overrides Fable, Sol, and Terra
together. Kimi remains at its maximum supported `xhigh` effort.

## Workspace Invariant

Forge binds to the permanent worktree from which it is invoked:

| Invocation path/branch | Entire run stays in |
| --- | --- |
| worktree `a`, branch `a` | `a` |
| worktree `b`, branch `b` | `b` |
| worktree `c`, branch `c` | `c` |
| worktree `d`, branch `d` | `d` |
| worktree `e`, branch `e` | `e` |

Every model and validation uses that same checkout. Tasks never name another worktree. Forge does
not synchronize letter branches, cherry-pick task commits, or perform an integration stage.

## Model Sequence

```text
Fable <-> user
  -> one Sol technical challenge
  -> Fable <-> user reconciliation
  -> user approval
  -> Terra implementation
  -> Kimi frontend work when required
  -> committed implementation
  -> testing
  -> COMPLETE
```

At any implementation difficulty:

```text
Terra or Kimi difficulty
  -> Sol read-only diagnosis
  -> active implementer applies advice
  -> implementation continues
```

At any test failure:

```text
test failure
  -> Sol read-only diagnosis
  -> Terra repair
  -> commit repair
  -> rerun affected tests
  -> repeat until pass
```

Only one model process may run at a time.

## State Transitions

```text
INTAKE
  -> SPECIFICATION
  -> TECHNICAL_REVIEW
  -> AWAITING_APPROVAL
  -> IMPLEMENTATION
  -> VERIFICATION
  -> COMPLETE

IMPLEMENTATION or VERIFICATION
  -> ESCALATION
  -> IMPLEMENTATION or VERIFICATION
```

`COMPLETE` is reachable only through `finalize`.

## Specification Contract

Fable speaks directly to the user. After every Fable invocation, the coordinator returns Fable's
complete stdout verbatim as the entire assistant response and ends the turn. The user's next
message is saved verbatim and passed to the same Fable session.

Run one pre-implementation Sol challenge after Fable's initial draft. Send valid findings to Fable,
then let Fable and the user finish the specification. Never send the reconciled specification back
to Sol for another review.

That single review is a bounded specification-strengthening pass: only directly implicated files
and one dependency hop, no repository inventory or tangential architecture study, and no more than
12 read/search calls.

## Memory Contract

At 40% context use, Forge compacts and continues automatically. Durable state regenerates a
canonical handoff after each state change. Every continuation preserves the exact current spec,
the exact visible user/Fable exchange, completed work and evidence, and the reason/source for each
implementation decision, spec deviation, or applied Sol diagnosis. Background exploration and tool
logs are intentionally omitted.

## Implementation Routing

| Work | Owner |
| --- | --- |
| Backend/application logic, data, integrations | Terra |
| Nonvisual frontend logic | Terra |
| Visual UI, styling, responsive behavior, interactions | Kimi |
| Tests and repairs | Terra |
| Difficulty/root-cause diagnosis | Sol, read-only |

Kimi unavailability is a blocker for a Kimi-owned task. Do not substitute Terra silently.

Task dependencies express execution order only. Because every task shares one checkout, no
dependency synchronization is required.

## Commit and Validation Contract

Workers may leave sequential changes in the current worktree. Before entering `VERIFICATION`, the
coordinator:

1. inspects the full diff;
2. runs proportionate narrow checks;
3. creates a semantic commit on the invocation branch;
4. confirms the worktree is clean.

Every validation is feature-scoped and bound to that committed HEAD. A repair changes HEAD and
invalidates prior evidence; rerun the affected validation set.

## Escalation Contract

Escalate on the first real implementation or test difficulty. The packet contains:

- exact task and acceptance criteria;
- current diff and HEAD;
- failing command and complete relevant output;
- deterministic reproduction;
- environmental facts;
- changes already attempted;
- requested diagnosis.

Sol returns a bounded root cause and solution. Terra applies test/backend/application repairs. Kimi
may apply advice when the blocked task is specifically visual frontend work. Sol remains read-only.

## Completion Gates

Complete when:

- the current specification is approved;
- all tasks have completion reports;
- all required evidence-bearing tests pass against current HEAD;
- no blocking finding is open;
- the invocation worktree is clean;
- the current branch contains work after the recorded Forge baseline.

There is no final Sol code review, final Fable acceptance pass, cross-worktree integration, or Sol
write takeover.
