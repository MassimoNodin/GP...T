# Agent guidance

- Use a `gpt-6.1-sol` subagent as the decision maker for architecturally important decisions and for architecture/code audits. Record accepted architectural decisions in `docs/` with their rationale.
- Keep the primary agent model unchanged. Use the specialist subagent for those decision and audit tasks, not routine implementation work.
