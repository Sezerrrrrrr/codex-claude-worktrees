# Contributing

Keep the project provider-native and narrow:

- Never make Codex load Claude Code runtime files or Claude Code load Codex runtime files.
- Keep user-facing skills short; put fragile sequences in deterministic, idempotent state machines.
- Preserve approval gates around dotfile edits, branch creation, GitHub writes, merges, and resets.
- Add temporary-repository tests for every Git behavior change.
- Do not add application deployment, database, cloud-provider, or customer-specific behavior.
- Never include credentials, transcripts, private repository content, or machine-specific absolute paths.

Run the test suite with:

```bash
python3 -m unittest discover -s tests -v
```
