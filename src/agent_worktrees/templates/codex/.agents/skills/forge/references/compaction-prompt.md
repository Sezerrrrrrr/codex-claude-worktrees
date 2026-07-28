# Forge compaction contract

If no active Forge run is identified in the conversation, compact normally and retain the current objective, user constraints, repository state, edits, validation evidence, and next action.

For an active Forge run:

1. Preserve the current Forge specification verbatim. Never paraphrase or shorten it.
2. Preserve every visible user/Fable message from the specification phase verbatim. Exclude Fable's tool calls, searches, private reasoning, and background exploration.
3. Preserve the current phase, worktree, branch, reasoning-effort override, task statuses, completed-task reports, required validations, and latest evidence.
4. Preserve every recorded implementation decision, specification deviation, Sol escalation decision, and its rationale. Do not silently normalize implementation back to the original specification after a recorded deviation.
5. Drop redundant tool logs and superseded exploration details.
6. Continue automatically from the recorded next action. Do not ask the user to repeat context solely because compaction occurred.

The generated `.forge-state/<feature>/memory/handoff.md` record is authoritative. A post-compaction session hook injects it into the continuation; if its full contents are spilled to a file, read that file before continuing.
