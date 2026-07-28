---
name: walkthrough
description: Set up persistent shared Claude Code and Codex worktrees, native configuration parity, GitHub lane workflows, terminal shortcuts, checkpoints, and notifications.
disable-model-invocation: true
---

# /walkthrough

Run `agent-worktrees walkthrough --harness claude run` and re-run it until it reports `done`.

- `needs_prerequisites`: give the user only the first missing prerequisite and the command or link needed to install it.
- `needs_tool_install`: explain that the other coding agent is not installed yet. Show the returned install command, ask the user to run it in a normal terminal, then have them run the returned verification command and re-run the walkthrough. Do not run a remote installer without explicit approval.
- `needs_tool_auth`: show the returned login command, explain the browser sign-in in one sentence, then have the user run the verification command and re-run the walkthrough. Never request, display, or handle their credential.
- `needs_approval`: explain the named action in plain language, ask the user, and after approval run `agent-worktrees walkthrough --harness claude approve <step>`.
- `needs_user`: show the audit questions. Resolve each named group with `resolve <group> <codex|claude|provider-only>`, then re-run.
- `needs_land`: explain that the setup files must reach the repository's default branch before letter worktrees are created. Help the user land them through their normal GitHub review process, then re-run.
- `needs_hook_trust`: explain Claude Code's project-hook permission and run `agent-worktrees walkthrough --harness claude approve hooks` after the user accepts it.
- `error`: report the cause, impact, and exact fix. Do not retry unchanged.

Never silently choose which provider wins a semantic conflict. Keep explanations brief and assume the user may be unfamiliar with Git.
If only Claude Code was configured at the start, the audit intentionally covers only Claude Code. The missing Codex setup is created natively and its installation/login is handled afterward.
