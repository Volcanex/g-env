# g-env

The framework layer of the G-System: shared Claude Code and Codex wiring, a
session context loader, skills, and the Mac desktop environment (AeroSpace,
Ghostty, the `genv` menu tool). The files here are the source, and the live
locations are symlinks back to them.

g-env holds nothing about a particular operator. Hosts, domains, accounts, keys
and secrets live in the operator's private instance repo (gp-core), which g-env
finds beside its own checkout. See "Instance contract" in [CLAUDE.md](CLAUDE.md)
for the order it searches and what it reads.

## Layout

| Repo file | Live location |
|---|---|
| `ghostty/config.ghostty` | `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty` |
| `zsh/zshrc` | `~/.zshrc` |
| `git/gitconfig`, or the instance's `env/git/gitconfig` | `~/.gitconfig` |
| the instance's `env/ssh/config.macos` | `~/.ssh/config` |
| the instance's `env/ssh/*` keys | `~/.ssh/*` (macOS) |
| `claude/settings.json`, or the instance's copy | `~/.claude/settings.json` |
| `context/session-context.py` | `~/.agents/session-context.py` (shared context loader) |
| `skills/` and sibling repo skills | `~/.agents/skills/` (canonical library) |
| Host data (not in git) | `~/.agents/host.md`, `~/.agents/context.json` |
| `codex/AGENTS.md` | `~/.codex/AGENTS.md` |
| `codex/rules/default.rules`, or the instance's copy | `~/.codex/rules/default.rules` |
| the instance's `.env` (passport) | sourced by zshrc through `g-instance env` |
| `cli/` (via `bin/genv`) | `~/.local/bin/genv`, the menu tool for env, sessions, keys, power and g-system (see `cli/CLAUDE.md`) |
| `bin/*.plist` | `~/Library/LaunchAgents/`, **copied, not symlinked** (see `bin/CLAUDE.md`) |

`g-env-linux` and `g-env-mac` add the platform pieces on top of this base.

## Starting and stopping the environment

On the Mac the window manager is four processes, not one app. `genv` owns all of them:

```sh
genv up        # start aerospace + ae-swipe + the menu bar
genv down      # stop them all
genv status    # what's alive, iPad, terminal home
```

They also start at login via LaunchAgents. To keep it off across reboots:
`launchctl disable gui/$(id -u)/com.gabriel.aerospace` (re-enable with `enable`).

The menu bar item (`genv-bar`) is the same thing with a mouse. Its icon shows whether
the WM is up, and it toggles the environment, the iPad, and terminal home.

```sh
genv ipad on|off|toggle        # connect the iPad as a Sidecar display
genv term main|secondary       # which screen the terminal tab lives on
```

## What's not committed, and why

- `~/.claude/.credentials.json`, `~/.codex/auth.json`: auth tokens. On a new machine, run `claude` or `codex` once to sign in again.
- `~/.claude/projects/`, `~/.claude/plans/`, `~/.codex/sessions/`: transcripts and plans. Large, private and short-lived.
- Caches, logs, shell snapshots and telemetry, which rebuild themselves.
- `~/.claude.json`: the per-project trust list, specific to each machine.
- Anything that names the operator: keys, the passport, the host list. Those are the instance's.

## Fresh machine

```sh
git clone <your instance repo> ~/Desktop/gp-core
git clone <this repo> ~/Desktop/g-env
cd ~/Desktop/g-env
./bootstrap.sh
```

`bootstrap.sh` is idempotent. Existing files get backed up to `*.backup.<ts>` before symlinking.
Without an instance it still runs, on framework defaults.

## Adding a new config

1. Move the file into the repo under a sensible folder.
2. Symlink the original location back to the repo file.
3. Add a `link` line to `bootstrap.sh`.
4. Commit.

If the file names your hosts, accounts or secrets, it belongs in the instance's
`env/` instead, as an overlay row (see `bootstrap-base.sh`).

Shared context, migration and bounded retrieval are documented in [context/README.md](context/README.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Vendored code keeps its own license: see
[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
