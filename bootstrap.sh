#!/usr/bin/env bash
# Bootstrap a fresh Mac: symlink configs from this repo to their live locations.
# Idempotent: re-run anytime.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

link() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [[ -L "$dst" ]]; then
    rm "$dst"
  elif [[ -e "$dst" ]]; then
    mv "$dst" "$dst.backup.$(date +%s)"
    echo "backed up existing $dst"
  fi
  ln -s "$src" "$dst"
  echo "linked $dst -> $src"
}

# LaunchAgents are COPIED, never symlinked. launchd's login-time scan of
# ~/Library/LaunchAgents does not follow symlinks: a symlinked agent loads only
# for the session that ran `launchctl bootstrap` here and is silently missing
# after the next reboot. That is exactly what stopped AeroSpace and ae-swipe
# from starting at login, while ae-dash (a plain file) kept working. The repo
# is still the source, the copy is the install step. Re-run this after editing
# a plist. `genv up` performs the same copy, so the two can't diverge.
agent() {
  local src="$1" label="$2"
  local dst="$HOME/Library/LaunchAgents/$(basename "$src")"
  mkdir -p "$HOME/Library/LaunchAgents"
  [[ -L "$dst" ]] && rm "$dst"
  cp -f "$src" "$dst"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dst"
  echo "agent $label installed"
}

link "$REPO/zsh/zshrc"             "$HOME/.zshrc"
link "$REPO/git/gitconfig"         "$HOME/.gitconfig"
link "$REPO/ghostty/config.ghostty" "$HOME/Library/Application Support/com.mitchellh.ghostty/config.ghostty"

# Aerospace (tiling WM).
link "$REPO/aerospace/aerospace.toml" "$HOME/.aerospace.toml"
link "$REPO/bin/ae-switch"            "$HOME/.local/bin/ae-switch"
link "$REPO/bin/ae-recover"           "$HOME/.local/bin/ae-recover"
link "$REPO/bin/ae-chrome"            "$HOME/.local/bin/ae-chrome"
# Both of these are named directly by aerospace.toml: an unlinked one fails
# silently at startup or on every cmd-arrow.
link "$REPO/bin/ae-startup"           "$HOME/.local/bin/ae-startup"
link "$REPO/bin/ae-cycle-ws"          "$HOME/.local/bin/ae-cycle-ws"
bash "$REPO/bin/ae-presence-install"
link "$REPO/bin/genv"                 "$HOME/.local/bin/genv"
link "$REPO/bin/gsync"                "$HOME/.local/bin/gsync"
link "$REPO/bin/claude-statusline"    "$HOME/.local/bin/claude-statusline"
link "$REPO/tmux/tmux.conf"           "$HOME/.tmux.conf"

# Screenshots into a folder, not scattered on the desktop.
mkdir -p "$HOME/Pictures/screenshots"
defaults write com.apple.screencapture location "$HOME/Pictures/screenshots"

# ae-veil: compiled binary, not a symlink (masks workspace-switch flashing).
if command -v swiftc &>/dev/null && [[ "$REPO/bin/ae-veil.swift" -nt "$HOME/.local/bin/ae-veil" ]]; then
  swiftc -O "$REPO/bin/ae-veil.swift" -o "$HOME/.local/bin/ae-veil"
fi

# ae-fade: crossfade mask for workspace switches. Resident daemon (ae-switch
# talks to its socket) because a cold process cost 70-130ms per switch.
# Started by ae-startup (i.e. by AeroSpace), NOT by launchd. See the comment
# there: Screen Recording is granted to the responsible process, so it must
# inherit AeroSpace's grant. Grant it to AeroSpace.app, once; it survives
# rebuilds of this binary. /tmp/ae-fade.err reports which state it's in.
if command -v swiftc &>/dev/null && [[ "$REPO/bin/ae-fade.swift" -nt "$HOME/.local/bin/ae-fade" ]]; then
  swiftc -O "$REPO/bin/ae-fade.swift" -o "$HOME/.local/bin/ae-fade"
fi
rm -f "$HOME/Library/LaunchAgents/com.gabriel.ae-fade.plist"

# ae-swipe: three-finger swipe → ae-cycle-ws, same as cmd+← / cmd+→.
# Compiled binary + LaunchAgent (reads the trackpad, so it must always run).
if command -v swiftc &>/dev/null && [[ "$REPO/bin/ae-swipe.swift" -nt "$HOME/.local/bin/ae-swipe" ]]; then
  swiftc -O "$REPO/bin/ae-swipe.swift" -o "$HOME/.local/bin/ae-swipe"
fi
agent "$REPO/bin/com.gabriel.ae-swipe.plist" com.gabriel.ae-swipe

# ae-sidecar: connect/disconnect the iPad as a display (private SidecarCore
# framework; see the header comment). No agent: it's a one-shot CLI called by
# `genv ipad` and by the menu bar.
if command -v swiftc &>/dev/null && [[ "$REPO/bin/ae-sidecar.swift" -nt "$HOME/.local/bin/ae-sidecar" ]]; then
  swiftc -O "$REPO/bin/ae-sidecar.swift" -o "$HOME/.local/bin/ae-sidecar"
fi

# genv-bar: menu bar item. A bare binary with activation policy .accessory,
# not a .app: same pattern as ae-swipe, and it avoids the Gatekeeper/codesigning
# trouble that makes bundled login items unreliable here. Its agent is installed
# further down, after AeroSpace, so the menu comes up over a live environment.
if command -v swiftc &>/dev/null && [[ "$REPO/bin/genv-bar.swift" -nt "$HOME/.local/bin/genv-bar" ]]; then
  swiftc -O "$REPO/bin/genv-bar.swift" -o "$HOME/.local/bin/genv-bar"
fi

# AeroSpace fork: hides workspaces by occlusion instead of parking their
# windows off-screen, which macOS never treats as occluded (so every app on
# every hidden workspace kept rendering). See aerospace-fork/CLAUDE.md.
#
# Two prerequisites that fail with errors naming neither cause: Swift 6.2+
# (Command Line Tools ship 6.1, which can't compile SE-0458 'unsafe' syntax)
# and bash 5 (generate.sh rejects the 3.2 macOS ships). Skipped, not fatal,
# when either is absent; a machine without them keeps stock AeroSpace.
AERO_SWIFT="/Library/Developer/Toolchains/swift-6.2.1-RELEASE.xctoolchain/usr/bin"
AERO_APP="/Applications/AeroSpace.app/Contents/MacOS/AeroSpace"
if [[ -x "$AERO_SWIFT/swift" && -x /opt/homebrew/bin/bash && -e "$AERO_APP" ]]; then
  if [[ -n "$(find "$REPO/aerospace-fork/Sources" -name '*.swift' -newer "$AERO_APP" -print -quit 2>/dev/null)" ]]; then
    ( cd "$REPO/aerospace-fork" &&
      PATH="/opt/homebrew/bin:$AERO_SWIFT:$PATH" ./generate.sh --ignore-xcodeproj --ignore-cmd-help &&
      PATH="/opt/homebrew/bin:$AERO_SWIFT:$PATH" "$AERO_SWIFT/swift" build )
    # The CLI must match the server: the wire protocol differs from the
    # homebrew 0.20.3-Beta client, which silently reports "server not running".
    # ~/.local/bin precedes /opt/homebrew/bin on PATH, so this shadows it.
    cp "$REPO/aerospace-fork/.build/debug/aerospace"    "$HOME/.local/bin/aerospace"
    cp "$REPO/aerospace-fork/.build/debug/AeroSpaceApp" "$AERO_APP"
    codesign --force --sign - /Applications/AeroSpace.app
    echo "AeroSpace fork rebuilt. Re-grant Accessibility (System Settings >"
    echo "Privacy & Security > Accessibility). TCC keys on the signature, so"
    echo "every rebuild invalidates it."
  fi
else
  echo "skipping AeroSpace fork build (needs Swift 6.2 toolchain + brew bash)"
fi

# AeroSpace's own start-at-login never registers (missing from `sfltool
# dumpbtm`): the ad-hoc-signed fork build fails Gatekeeper's spctl check, so
# launch it via the same LaunchAgent pattern as ae-dash/ae-swipe instead.
agent "$REPO/bin/com.gabriel.aerospace.plist" com.gabriel.aerospace
agent "$REPO/bin/com.gabriel.genv-bar.plist" com.gabriel.genv-bar

# mosh: required for persistent server connections.
command -v mosh &>/dev/null || brew install mosh

# Shared context and client adapters; preserve independent client state.
bash "$REPO/bootstrap-base.sh"

# SSH config: the instance's env/ssh/config.macos, found the way bootstrap-base.sh
# finds it for the keys and known_hosts. Its env/ssh/config is the servers' file,
# which g-env-linux links. ~/.ssh/ must be 0700.
INSTANCE="$("$REPO/bin/g-instance" 2>/dev/null)"
if [[ -n "$INSTANCE" && -f "$INSTANCE/env/ssh/config.macos" ]]; then
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  link "$INSTANCE/env/ssh/config.macos" "$HOME/.ssh/config"
else
  echo "ssh: no instance env/ssh/config.macos; ~/.ssh/config left as it is"
fi

# Environment dashboard.
agent "$REPO/dash/com.gabriel.ae-dash.plist" com.gabriel.ae-dash
echo "ae-dash started on http://localhost:5555"

# .env (gitignored, may not exist on a fresh clone; copy template if missing).
if [[ ! -f "$REPO/.env" ]]; then
  cp "$REPO/.env.example" "$REPO/.env"
  echo "created $REPO/.env from template: fill in your secrets"
fi

echo
echo "done. open a new shell / restart Ghostty."
