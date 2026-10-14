# g-env

Platform-agnostic Claude Code + Codex environment wiring.

**Role in g-system:** the framework layer. It reads the operator's instance
(gp-core, see below) and provides the base that `g-env-mac` and `g-env-linux`
call before adding their platform-specific configs.

## What lives here

- `bootstrap-base.sh`: idempotent client and git wiring. Resolves the instance, calls
  `scripts/install-context.py --instance` to migrate host facts and skills safely,
  then applies the overlay table (below).
- `bin/g-instance` (linked into `~/.local/bin`) and `context/instance.py`: the
  instance resolver, one per language with the same order. Shell tools call
  `g-instance [dir|env|manifest|framework]`. Python tools import or copy `instance()`,
  and `service_url()` reads a service's domain from the manifest.
- `context/shared.md`: short vendor-neutral policy. `context/reference.md`
  keeps the supporting guidance. `context/session-context.py` reads live source
  files and Brain context without any Claude dependency. Nothing is truncated:
  sources pick their session excerpt. See `context/README.md` for that design and migration.
- `skills/`: the canonical host-agnostic library, with `claude/skills` as a compatibility
  alias. Vendor upstream updates through `scripts/vendor-skills.sh`. Host-specific
  skills live in their owning repo at `skills/<name>/SKILL.md` and are installed
  only where that repo exists. Never commit host-specific absolute skill links.
- `~/.agents/host.md`, `context.json`, `skills/`: neutral host data, source paths
  and the real skill library. Preserve unmanaged skills and independent config.
- `claude/`: client settings, agent definitions and thin root and hook adapters.
  Historical instruction and skill paths alias neutral sources.
- `bin/g-scout` and `claude/hooks/luna-scout.py` give substantial Claude subagents
  checked research pointers from the gather runner. `G_SCOUT=0` disables the boost.
  `G_SCOUT_DEBUG=1` records hook decisions under `~/.cache/genv/scout/<agent_id>/`.
- `bin/g-agent-run` falls back when a provider returns a non-answer. Short fragments,
  blocked sandbox or permission reports, and refusals without a deliverable fail.
  Short verdicts pass. Failures are logged with reason `non-answer`.
  Failed uid map, newuidmap or setgroups setup is logged with reason `sandbox`.
- `codex/AGENTS.md`, `codex/rules/`: Codex-specific adaptations. Its hooks invoke
  the neutral loader, and the old session-context.py remains a compatibility entry.
  Review hook trust in `/hooks`. The root adapter provides a manual fallback.
  `codex/configure.py` fills missing defaults while preserving local overrides.
  Its TUI defaults show context, model, project, branch, allowance and title.
  Native Codex cannot display Claude's custom pace and Brain-health widgets.
  Local config does not configure hosted ChatGPT Work.
- `bin/g-eburn`: spends Codex down to the EBURN curve on queued work and read-only ticket/repo reviews (live repos only); state and reports in `~/.cache/genv/eburn/`.
- `bin/compute-usage`: on-demand subscription quota snapshots. `--record`
  appends sanitized observations to the user's private cache, with no model turn.
  Claude data comes from its statusline cache and may be stale. Codex data comes
  from the authenticated local app-server. Missing windows remain unavailable.
- `bin/g-reset`: records known quota resets, allowance ends and reset risk windows for compute pace.
- `claude/settings.json`, `git/gitconfig`, `codex/config.toml`, `codex/rules/default.rules`:
  generic framework defaults. Hooks and the statusLine, but no MCP servers, tokens,
  plugin dirs or standing command approvals, no git identity or credential helper,
  and configure.py's Codex defaults. Real ones live in the instance's `env/`.
- `scripts/mirror-claude.py`: links tracked and untracked nonignored `CLAUDE.md` to `AGENTS.md` in
  supplied repositories. `--discover` scans sibling and home repositories (depth 3)
  and their registered worktrees. Relative symlinks reflect edits immediately, and
  differing agent instructions are preserved. Links
  repository `.agents/skills` to `.claude/skills` where needed. `--user-skills`
  exposes the canonical neutral library to Claude without reversing ownership. Bootstrap runs discovery and user wiring on every host. Independent instruction
  files are reported as conflicts and preserved. Indexed knowledge notes (`type: note`
  and `permalink` in frontmatter) are excluded: Brain imports instructions as data,
  and mirroring those creates duplicate indexed notes. Existing notes are never changed.
  Ignored files and dependency trees are excluded. Extra roots can be supplied
  with `--discover-root`.
- `scripts/vendor-skills.sh`: re-vendor the pinned upstream skills into `skills/`. Run manually to update.
- `LICENSE` (GNU AGPL v3.0 only) and `THIRD_PARTY_NOTICES` for the
  vendored code that keeps its own license.

## Instance contract

The instance is the operator's private repo (gp-core). The resolver finds it in
this order, explicit settings first:

1. A file-specific legacy variable: `GCORE_ENV` (env), `GCORE_MANIFEST` (manifest),
   then `GCORE_DIR`, `GSITE_GCORE_DIR` for the directory.
2. `G_INSTANCE`.
3. `gp-core` beside the g-env checkout, then legacy `gcore` there. The checkout is
   `G_ENV`, else `GENV`, else `GENV_DIR`, else the script's own repo (resolved).
4. None: a hint on stderr, framework defaults, never a failure. Bootstrap clones
   `G_INSTANCE_REMOTE` into a sibling `gp-core` only when that variable is set.

What g-env reads from it: `conventions.md` and `CLAUDE.md` as context sources
(context.json moves an existing source only while it still holds the old sibling
`gcore` default and the new file exists), `.env` (passport), `manifest.json`
(hosts, workspaces, service domains), `skills/*` (the sibling glob, framework names
win), `push-secrets`, and the overlay rows in `env/`. Nothing in the framework
should name an operator's hosts, domains or accounts: read them from the instance.

**Overlay table.** Whole files, never merged: link `$INSTANCE/env/<row>` when it
exists, else the framework default.

| Row | Target | Rule |
|---|---|---|
| `claude/settings.json` | `~/.claude/settings.json` | guarded: replaces only an absent file or a link to either source |
| `git/gitconfig` | `~/.gitconfig` | always |
| `codex/config.toml` | `~/.codex/config.toml` | macOS, guarded, instance copy only (Codex writes into it) |
| `codex/rules/default.rules` | `~/.codex/rules/default.rules` | always |
| `ssh/*` (not `config*`), `claude/daemon/control.key` | `~/.ssh/*`, `~/.claude/daemon/control.key` | macOS, guarded, instance copy only, private keys 0600 |
| `ssh/config.macos` | `~/.ssh/config` | `bootstrap.sh`, macOS, instance copy only |
| `ssh/config` | `~/.ssh/config` | g-env-linux `bootstrap.sh` |

Instance settings reach hooks only through `~/.claude/hooks/<name>`. When a hook is
added here, add it to the framework `claude/settings.json` and tell instance owners
to add it to theirs. `claude/settings.json` and `git/gitconfig` here are framework
defaults, which an instance's `env/` copies replace whole.

Personal skills live in the instance's `skills/`, and the sibling glob installs them
beside the framework's own.

## Adding platform-specific config

Don't add Mac or Linux paths here. Use:
- `g-env-mac` for Ghostty, Aerospace, Mac SSH keys, zshrc.
- `g-env-linux` for Ubuntu zshrc, server SSH config, systemd self-sync.

## Machine-specific overrides

Copy `claude/settings.local.example.json` to `~/.claude/settings.local.json` and fill
in machine-specific values (MCP credentials, permissions mode, model). This file is
never tracked. genv owns the base, and settings.local.json owns the diff.

## Maintenance

To update vendored skills: `bash scripts/vendor-skills.sh`
On Linux servers: the `genv-sync.timer` (from g-env-linux, and the unit keeps its old
name) pulls g-env and the instance, then re-runs bootstrap-base.sh daily. Push here and
the servers update overnight. `bin/gsync` syncs the instance, this checkout and its
platform siblings, all found through `g-instance`.
