# Claude adapter

This directory holds Claude settings, agent definitions and hook adapters.
Shared instructions and the context loader live in `../context`; shared skills
live in `../skills`. The historical `CLAUDE.md`, `AGENTS.md` and `skills/` paths
are compatibility aliases, not independent sources.

`root.md` is installed as `~/.claude/CLAUDE.md`. It explains how to read shared
context when the SessionStart hook is unavailable. Host facts live in
`~/.agents/host.md`. `settings.local.json` remains an independent host override.
`hooks/pulse-inject.sh` forwards old hook configurations to the neutral loader.
See `../context/README.md` for migration, limits and troubleshooting.

`mods/g-deck` is a Claude Code mod (a function-hook plugin). The instance loads it by
setting `CLAUDE_CODE_PLUGIN_DIRS` in its `env/claude/settings.json` to this folder. The
framework default leaves it out: the path differs per host, settings.json is linked whole,
and Claude Code reads env values literally. The Agents band above the prompt lists
every running subagent and Sol job with its grade from `context/grades.py`, its context
(blue to 100k, then 50k colour bands up to the 250k auto-compact, as in `bin/claude-statusline`) and
its Luna scout as a child row (sources found, pointers given), and posts a Luna boost digest to the main session when a boosted
worker ends. Check it with `claude plugin validate` and `claude plugin test` on the folder.
