# Security

Report suspected vulnerabilities through the repository's GitHub private vulnerability-reporting
page. Do not open a public issue containing exploit details or credentials.

The toolkit can edit shell configuration, install agent hooks, create Git worktrees, commit changes,
and push persistent letter branches. The walkthrough previews these mutations and requires explicit
approval before enabling them. Ordinary Stop hooks never push `main` or force-push a lane.

The trusted Stop-hook definition calls the separately installed `agent-worktrees` executable. It
does not execute a runtime copied into the target repository, so checking out a branch cannot replace
the hook implementation without changing the reviewed hook definition or the installed package.

Do not include credentials in agent configuration. Machine-local settings, audit output, and locks
stay outside the committed parity ledger. Walkthrough approvals are stored with mode `0600` inside
Git's private metadata, not in a repository-controlled working-tree file. Review hook changes through
the native Codex and Claude Code trust interfaces after installation.

The deep audit sends eligible instruction, skill, and hook content to the coding-model provider that
the user selected. User-level settings and known credential files are metadata-only; detected secret
material and symlink targets are withheld. Do not run the audit on configuration you are not allowed
to send to that provider. Child model processes receive an allowlisted environment rather than the
parent shell's API keys, cloud tokens, or unrelated credentials.

Automatic checkpoints inspect complete staged blobs, reject known credential paths and detected
secret material, and refuse binary files. Review and commit an intentional binary change manually;
the toolkit will not auto-approve it.

`validationCommands` are executable commands chosen by the target repository. Review changes to
`.agent-worktrees/config.json` before invoking ship on untrusted code.
