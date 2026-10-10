# Claude in the G-System

Read the injected `G-System session context` once. Its sources are the neutral
`~/.agents/host.md`, genv shared instructions, gcore and Brain. If absent, run
`python3 ~/.agents/session-context.py` before project work. If unavailable, report
that and read `~/.agents/context.json` and its source files directly.

Use Claude's available tools and agent registrations. Repository instructions
still apply. Shared skills live in `~/.agents/skills`; `~/.claude/skills` is a
compatibility alias. HTTP context does not establish an MCP connection.
