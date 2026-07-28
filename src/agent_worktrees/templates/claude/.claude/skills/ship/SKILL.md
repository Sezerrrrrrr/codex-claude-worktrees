---
name: ship
description: Explicitly land the current persistent letter lane through a GitHub pull request, then retarget its local and remote letter branch to the merged default branch.
disable-model-invocation: true
---

# /ship

Run `agent-worktrees ship --harness claude` until it reports `done`.

- `needs_resolve`: resolve rebase conflicts as in `/pull`, then re-run.
- `poll_wait`: required GitHub checks are still running; re-run without a long sleep.
- `checks_failed`: inspect the named checks, fix the code, and re-run.
- `error`: report the cause, impact, and exact fix.

This skill means "land on GitHub". It does not deploy an application. Its explicit invocation authorizes committing, pushing the matching letter branch, creating and squash-merging its PR, and retargeting that letter branch after the merge. It never directly pushes the default branch.
