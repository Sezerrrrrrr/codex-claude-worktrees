# Recovery

Run commands from the affected letter worktree.

## Check what the toolkit sees

```bash
git status --short --branch
agent-worktrees parity status
git worktree list
```

## Resolve a pull conflict

1. Open every file listed by the pull skill.
2. Remove the Git conflict markers after choosing the correct combined content.
3. Run `git add -A`.
4. Invoke the native pull skill again. Its state machine continues the rebase.

To abandon the rebase, ask the user first, then run `git rebase --abort`. Aborting discards only the
in-progress rebase operation; the pre-pull checkpoint remains on the letter branch and GitHub.

## Recover the pre-ship lane

The ship report includes a backup ref such as:

```text
refs/agent-worktrees/backups/a/20260728T120000Z
```

Inspect it without modifying the lane:

```bash
git log --oneline refs/agent-worktrees/backups/a/20260728T120000Z
git diff HEAD..refs/agent-worktrees/backups/a/20260728T120000Z
```

Ask an agent to restore specific commits. Do not reset the lane blindly.

## A parity conflict

If both native sides changed, choose the authoritative side explicitly:

```bash
agent-worktrees parity status
```

Review both files, preserve any provider-specific syntax, make one side authoritative, then use the
walkthrough/audit resolution flow or re-baseline only after both complete native files have been
reviewed. Never baseline merely to silence a conflict.

## Shortcut conflicts

The installer will not replace an existing `za`, `zac`, and similar command outside its marked
block. Rename or remove the old definition, then run:

```bash
agent-worktrees install-shortcuts
```
