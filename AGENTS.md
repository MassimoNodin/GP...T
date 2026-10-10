# Repository contribution conventions

These conventions apply to all contributors and coding agents in this repository.
Follow any additional instructions in nested `AGENTS.md` files for their directories.

## Commit messages

Use `<type>: <short description>` for every new commit. Do not add scopes.

Allowed types:

- `feat`: Add a feature or new behavior.
- `fix`: Correct a bug.
- `refactor`: Restructure code without changing behavior.
- `docs`: Change documentation only.
- `test`: Add or update tests only.
- `chore`: Maintain dependencies, tooling, configuration, or repository housekeeping.

Use a lowercase type and an imperative description, such as "add" rather than
"added". Keep the subject concise and omit a trailing period. An optional body
may explain the motivation, validation, or breaking changes.

Examples:

```text
feat: add tyre degradation estimates
fix: handle missing telemetry packets
docs: define repository contribution conventions
```

## Branch names

Use `<type>/<short-kebab-case-description>` for new contribution branches.
Use the same allowed types as commit messages. Descriptions must use lowercase
letters, numbers, and hyphens; do not add scopes or agent-specific prefixes.

Examples:

```text
feat/tyre-degradation
fix/missing-telemetry-packets
docs/contribution-conventions
```

Existing branches, including `main`, do not need to be renamed. Keep one task
per contribution branch and one logical change per commit.

## Agent workflow

- Inspect the working tree and applicable instructions before making changes.
- Keep changes focused on the requested task; preserve unrelated work.
- Run relevant validation when available and report what ran and what did not.
- Before committing, inspect the staged diff and include only task-related files.
- Create branches, commit, amend commits, push, or open pull requests only when
  explicitly requested. Do not rewrite existing history without permission.
- In the final handoff, summarize changes, validation, and any remaining risks.

## Luna subagent preference

When Luna subagents are requested or authorized, use `gpt-6-luna`. Do not
silently fall back to an older Luna model. Keep the primary agent's model
unchanged unless the user requests otherwise.
