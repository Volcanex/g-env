# cli/: the `genv` terminal tool

Rendered menu tool (rich, not textual: must start in well under a second).
Launcher is `../bin/genv`, symlinked to `~/.local/bin/genv` by `bootstrap.sh`.

- Runs on `python3.12` (homebrew). `rich` was installed with
  `python3.12 -m pip install --break-system-packages rich`. Homebrew python is
  PEP 668 externally-managed, so a plain `pip install` fails.
- Modules are **flat**, not a package: the launcher puts `cli/` on `sys.path` and
  imports `main`. Import each other as top-level (`import sessions`), so don't
  name a module after a stdlib one.
- Subcommands (`genv env|sessions|keys|power|sys|config`) print one panel and exit;
  bare `genv` is the interactive dashboard loop (raw single-keypress via termios).
- Two kinds of argument, dispatched in this order in `main.main()`: **action verbs**
  (`up`, `down`, `status`, `ipad`, `term`, `tiling`, `fade`, `ws`: in `env.VERBS`, take their own arguments)
  are matched *before* the menu keys, so `genv up` can't be swallowed as a
  one-letter alias. Add new verbs to `env.VERBS` and to `main.USAGE`.
- `env.py` owns the environment's lifecycle and is the only implementation of it:
  `genv-bar` (the menu bar) shells out to these same verbs rather than reimplementing
  them, so the menu and the CLI cannot drift. It knows that "genv" is four processes
  (aerospace, ae-swipe, genv-bar, ae-fade), not one app.
  - LaunchAgents are **copied** into `~/Library/LaunchAgents`, never symlinked:
    launchd's login scan doesn't follow symlinks, which is why aerospace and
    ae-swipe silently stopped starting at login. `install_agent()` re-copies when
    the repo copy differs, so `genv up` is enough after editing a plist.
  - `ae-fade` needs Screen Recording, which macOS attributes to the *responsible*
    process, so it must be started by AeroSpace, and the tick that matters is on
    `/Applications/AeroSpace.app`, not on ae-fade (see `../bin/CLAUDE.md`).
    `genv fade` restarts it *through* AeroSpace and reports which state it got:
    `exec-and-forget` is config-only, so `restart_fade()` reaches AeroSpace's
    process context by triggering the `alt-shift-r` binding that exists for this.
    Started any other way it would test a permission it will never run under.
  - `genv tiling off` uncomments a catch-all `[[on-window-detected]] run =
    'layout floating'` in `aerospace.toml`, found by the `# TILING-OFF` marker
    (a marker, not a matched block: the rule is a few lines too generic to match
    safely). The block is `TILING_BLOCK` lines long and both functions derive
    from that constant: it grew a `check-further-callbacks = true` line so that
    windows keep falling through to the secondary-screen rule while tiling is
    off, and a hard-coded `i + 2` would have silently half-commented it. The config rule only fires on *new* windows, so `apply_tiling()`
    also re-lays out the existing ones; it skips the Ghostty tab windows, which
    ae-switch re-asserts as tiling every switch anyway.
  - `genv ipad side [left|right|toggle]` writes `~/.genv-ipad-side` **and** moves
    the screen now (via `ae-sidecar side`), because the file alone only decides
    where the *next* connect lands. A menu item that did nothing while the iPad
    sat right there would read as broken. A failed move is reported as "applies
    on next connect", not as an error: the preference is still recorded.
  - `resync_workspaces()` runs after `genv ipad on|off|toggle`. `ae-switch`
    caches AeroSpace's monitor count and only refreshes it *after* a switch, so
    connecting a screen leaves the first ⌘N running the one-monitor path and the
    second one hauling every terminal tab across. This rewrites the count and
    replays the current workspace, once, at the moment the count is known to have
    changed. It waits for `aerospace list-monitors` to agree with `monitors()`
    first: AeroSpace sees a new display a beat after the window server.
  - `monitors()` asks CoreGraphics (`CGGetOnlineDisplayList`), not AeroSpace:
    AeroSpace is often exactly the thing that's down. **Online**, not Active:
    Active returns 0 from a CLI process with no window server connection.
- `workspace.py` is the hot-workspace pool. A workspace marked `"hot": true` in
  the manifest has no host and no project; `genv ws <N>` lists the manifest's
  projects (marking the ones whose directory isn't actually on the host) and
  writes the answer to three files the shell reads: `~/.ae-workspace-<N>`,
  `~/.ae-ws-path-<N>`, `~/.ae-ws-label-<N>`. Files, not a manifest edit: which
  project you are borrowing slot 6 for today is not topology, and gcore is the
  SSOT for topology. Writing the *same* `~/.ae-workspace-<N>` that `ae-switch`
  writes for a pinned workspace is what lets `zshrc`'s auto-SSH block stay
  ignorant of hot workspaces entirely. `ae-startup` deletes all three at login,
  which is the whole of "session only".
  - `_dirs()` passes `stdin=DEVNULL` to ssh. Not tidiness: it runs immediately
    before the picker's own `input()`, and without it ssh swallows the answer
    you typed and the prompt looks broken.
- `sessions.py` fans the ssh calls out with a thread pool, 2s timeout; hosts come
  from `gcore/manifest.json → workspaces`, and unreachable hosts are reported in
  the summary line rather than blocking.
- `keys.py` parses `../aerospace/aerospace.toml` and `../ghostty/config.ghostty`
  live. Never hand-maintain a shortcut list here. Descriptions come from the
  comment above each binding block.
- `config.py` row 7 (terminal home) is the odd one out: it delegates to `env.py`
  and writes `~/.genv-term-home`, which `ae-switch` reads live on every switch:
  no file rewrite, no `reload-config`, effective on the next ⌘N. Every other row
  rewrites a config file.
- `config.py` toggles environment settings by rewriting the SSOT files themselves
  (`../ghostty/config.ghostty`, `../aerospace/aerospace.toml`, `../bin/ae-switch`):
  state is parsed live, never cached, and an edit only lands if the expected
  block matched verbatim (otherwise: warn and skip). Aerospace changes run
  `aerospace reload-config`; Ghostty ones only affect new windows.
- `gsystem.py` reads brain's L5 snapshot at `/api/status` on `BRAIN_PUBLIC`, else the
  brain-api domain in the instance manifest.
- `power.py` caches static specs in `~/.cache/genv/specs.json`; battery draw is
  measured from `ioreg` Amperage×Voltage (Amperage is an unsigned two's-complement
  64-bit int), and falls back to a load-based estimate on AC.
- Style: `theme.py` only. One accent (`#ff4fd8`) for things that want attention,
  white for values, greys for everything else. No emoji, no second colour.
  Table columns that could wrap use `trim()` against `console.width`, since a
  wrapping cell breaks the one-row-per-item density.
