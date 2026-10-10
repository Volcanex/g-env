# Shared context

`shared.md` is the short, vendor-neutral standing policy. `reference.md` retains
supporting guidance for selective reading. `session-context.py` is the single
loader for Claude, Codex and direct CLI use; it requires Python's standard
library and no client installation. The disposable `agents/` kickoff briefs
are deliberately separate.

`bootstrap-base.sh` calls `scripts/install-context.py`. It installs:

- `~/.agents/host.md`: host facts, migrated from the old Claude root.
- `~/.agents/context.json`: live source paths for shared policy, gcore conventions
  and topology; independent source overrides survive repeat installation.
- `~/.agents/session-context.py`: link to this loader.
- `~/.agents/skills`: real shared library, assembled from `genv/skills` and
  installed sibling repositories; `~/.claude/skills` normally aliases it.

Legacy whole-client directory symlinks are materialized before migration so
client writes cannot modify repository sources. Host migration removes only
managed imports, expands remaining relative paths,
and copies imports inside the Claude directory into `~/.agents/imports`. It
preserves original host files and displaced aliases as `.pre-neutral` backups.
Canonical host facts stay outside git. Existing neutral facts are retained and
legacy facts appended once. Unmanaged skill directories/links survive; divergent
same-name Claude/neutral skills remain in their respective namespaces and are
reported. Only unchanged links recorded in the managed skill ledger are removed
when a managed skill disappears. Vendor settings, hooks, credentials and sessions
are not migration inputs. Independent settings/hook configs are preserved by
bootstrap; merge the tracked hook if needed.

The loader never truncates. Every section renders whole and is short by design;
reference detail sits behind one-line pointers that agents follow on demand:

- host, gcore conventions and topology: a source that marks
  `<!-- SESSION:START -->`/`<!-- SESSION:END -->` blocks contributes exactly those
  blocks (its author chose them) and is labelled "rest on demand"; a source without
  markers renders whole, so forgetting them costs length, never content. gcore's
  `render_context.py` marks the generated Hosts list.
- shared policy: `shared.md` whole. Each rule lives in one place: the operator's
  agent-interaction rules in the instance's `conventions.md`, standing policy in `shared.md`,
  detail in `reference.md`.
- Pulse: priority entries only (P0/urgent/warning/broken and similar), at most
  five whole entries plus an explicit count of the rest.
- Brain: one orientation line (count, broken count), up to four relevant note
  paths with an explicit "+N more", and housekeeping only when actionable (moved
  dependencies, or a condensation backlog of 50+ or 10+ possibly outdated). An
  orientation it cannot parse renders whole.
- Compute pace and Models render from their data files. `models.json` (roster, `effort_ladder`
  over score, cost and latency, SWE-bench Pro note) is refreshed weekly by `bin/refresh-models`;
  every number carries a source URL and `as_of` marks staleness after 45 days. Model calls go
  through `bin/g-agent-run` (`providers.json`).

A section whose builder fails renders one line naming what to read instead.
`TARGET_CHARS` (7000) is a soft guard: `scripts/test_codex_context.py` renders
the real shared/gcore sources with realistic Pulse and orientation and fails if the
total grows past it. Trim the source when it fails; do not raise the target lightly.

Local/public Brain reads run concurrently, with 2/4 second request timeouts,
32KB response ceilings and explicit unavailable messages. Project/task are
optional, properly encoded inputs, not inferred work to resume:

```sh
python3 ~/.agents/session-context.py --project gbrain --task 'orientation ranking'
python3 ~/.agents/session-context.py --no-pulse
```

`G_SYSTEM_PROJECT` and `G_SYSTEM_TASK` supply optional defaults. `--hook` reads
SessionStart event JSON and emits the shared hook envelope. Claude/Codex hook
configs invoke this directly. The old Codex helper and Claude pulse hook remain
thin compatibility entry points; Codex's `additionalContextLimit` stays 12000 as headroom.
There is no task handover store, automatic resumption, or MCP registry change.

Validation: `python3 -m unittest scripts.test_codex_context
scripts.test_install_context scripts.test_mirror_claude` (fixture homes only).
