---
name: pull
description: Explicitly sync the current persistent letter lane with the GitHub default branch while preserving and pushing its work.
disable-model-invocation: true
---

# /pull

Run `agent-worktrees pull --harness claude` until it reports `done`.

- `needs_resolve`: inspect every conflict. Resolve straightforward combinations; ask the user about competing product or architecture choices. Stage resolved files and re-run.
- `error`: report the cause, impact, and exact fix. Do not bypass lane validation or use an unguarded force-push.

The workflow may commit and push the matching letter branch. It never pushes the default branch.
