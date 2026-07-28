---
name: pull
description: Explicitly sync the current persistent letter lane with the GitHub default branch while preserving and pushing its work. Use only when the user invokes $pull or explicitly asks to sync the current lane.
---

# $pull

Run the installed native state machine until it reports `done`:

```bash
agent-worktrees pull --harness codex
```

- `needs_resolve`: inspect every conflict. Resolve straightforward combinations; ask the user about competing product or architecture choices. Stage resolved files and re-run.
- `error`: report the cause, impact, and exact fix. Do not bypass lane validation or use an unguarded force-push.

The workflow may commit and push the matching letter branch. It never pushes the default branch.
