#!/usr/bin/env bash
# Bootstrap platform-agnostic config: claude, codex, git.
# Idempotent and safe to re-run. Called by g-env-mac/bootstrap.sh and
# g-env-linux/bootstrap.sh before they add their platform-specific layers.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

link() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [[ -L "$dst" ]]; then
    rm "$dst"
  elif [[ -e "$dst" ]]; then
    mv "$dst" "$dst.backup.$(date +%s)"
    echo "backed up $dst"
  fi
  ln -s "$src" "$dst"
  echo "linked $dst -> $src"
}

real() {  # readlink -f, which macOS bash 3.2 lacks
  local path="$1" target dir n=0
  while [[ -L "$path" && $n -lt 40 ]]; do
    target="$(readlink "$path")"
    if [[ "$target" != /* ]]; then target="$(dirname "$path")/$target"; fi
    path="$target"; n=$((n + 1))
  done
  dir="$(cd -P "$(dirname "$path")" 2>/dev/null && pwd)" || dir="$(dirname "$path")"
  echo "$dir/$(basename "$path")"
}

# The operator's instance (gp-core, or legacy gcore) beside this checkout; see
# bin/g-instance. Without one the framework defaults below still install.
INSTANCE="$("$REPO/bin/g-instance" 2>/dev/null)"
if [[ -z "$INSTANCE" && -n "${G_INSTANCE_REMOTE:-}" ]]; then
  PARENT="$(cd -P "$REPO/.." && pwd)"
  echo "cloning instance from G_INSTANCE_REMOTE into $PARENT/gp-core..."
  if git clone "$G_INSTANCE_REMOTE" "$PARENT/gp-core"; then
    git -C "$PARENT/gp-core" config core.hooksPath .githooks
    INSTANCE="$("$REPO/bin/g-instance" 2>/dev/null)"
  else
    echo "instance: clone failed; continuing with framework defaults"
  fi
fi

# One canonical host context and skill library, independent of either client.
# Preserve legacy host facts and unmanaged skills before installing adapters.
python3 "$REPO/scripts/install-context.py" --repo "$REPO" ${INSTANCE:+--instance "$INSTANCE"}

# Overlay table: each row links the instance's whole file from $INSTANCE/env/<row>
# when it has one, else the framework default. Never merged.
#   claude/settings.json      -> ~/.claude/settings.json      guarded
#   git/gitconfig             -> ~/.gitconfig                 always
#   codex/rules/default.rules -> ~/.codex/rules/default.rules always
#   codex/config.toml         -> ~/.codex/config.toml         guarded, macOS, instance copy only
#   ssh/* except config*      -> ~/.ssh/*                     guarded, macOS, instance copy only
#   claude/daemon/control.key -> ~/.claude/daemon/control.key guarded, macOS, instance copy only
#   ssh/config.macos          -> ~/.ssh/config                bootstrap.sh, macOS, instance copy only
#   ssh/config                -> ~/.ssh/config                g-env-linux/bootstrap.sh
overlay() {
  if [[ -n "$INSTANCE" && -e "$INSTANCE/env/$1" ]]; then echo "$INSTANCE/env/$1"; else echo "$REPO/$1"; fi
}

# Replace only an absent target or a link to either source of the row, so an
# independent local file (h's settings.json) survives.
overlay_guarded() {
  local row="$1" dst="$2" current=""
  if [[ -L "$dst" ]]; then current="$(real "$dst")"; fi
  if [[ ! -e "$dst" && ! -L "$dst" ]] || [[ -n "$current" && "$current" == "$(real "$REPO/$row")" ]] ||
     [[ -n "$current" && -n "$INSTANCE" && "$current" == "$(real "$INSTANCE/env/$row")" ]]; then
    link "$(overlay "$row")" "$dst"
  else
    echo "$3"
  fi
}

# Claude Code
mkdir -p "$HOME/.claude"
overlay_guarded claude/settings.json "$HOME/.claude/settings.json" \
  "claude: preserving independent settings.json; merge the shared SessionStart hook if needed"

link "$REPO/claude/agents"        "$HOME/.claude/agents"
link "$REPO/claude/hooks"         "$HOME/.claude/hooks"

# Statusline command: must be on PATH for settings.json's statusLine entry
link "$REPO/bin/claude-statusline" "$HOME/.local/bin/claude-statusline"
link "$REPO/bin/compute-usage" "$HOME/.local/bin/compute-usage"
link "$REPO/bin/g-agent-run" "$HOME/.local/bin/g-agent-run"
link "$REPO/bin/g-agent-call" "$HOME/.local/bin/g-agent-call"
link "$REPO/bin/g-scout" "$HOME/.local/bin/g-scout"
link "$REPO/bin/g-pick" "$HOME/.local/bin/g-pick"
link "$REPO/bin/g-temp" "$HOME/.local/bin/g-temp"
link "$REPO/bin/g-reset" "$HOME/.local/bin/g-reset"
link "$REPO/bin/g-eburn" "$HOME/.local/bin/g-eburn"
link "$REPO/bin/refresh-models" "$HOME/.local/bin/refresh-models"
link "$REPO/bin/g-instance" "$HOME/.local/bin/g-instance"

# Codex
mkdir -p "$HOME/.codex/rules"
link "$(overlay codex/rules/default.rules)" "$HOME/.codex/rules/default.rules"
# Codex writes its own state into config.toml, so the tracked framework copy is
# never linked; without an instance copy configure.py keeps a local file.
if [[ "$(uname -s)" == Darwin && -n "$INSTANCE" && -e "$INSTANCE/env/codex/config.toml" ]]; then
  overlay_guarded codex/config.toml "$HOME/.codex/config.toml" "codex: preserving independent config.toml"
fi
# The operator's SSH keys, known_hosts and Claude daemon key, on macOS only: the
# framework ships none, and servers keep their own. ssh refuses a private key
# that others can read, and git checks files out 0644.
if [[ "$(uname -s)" == Darwin && -n "$INSTANCE" ]]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  for path in "$INSTANCE"/env/ssh/* "$INSTANCE/env/claude/daemon/control.key"; do
    row="${path#"$INSTANCE/env/"}"
    [[ -f "$path" && "$row" != ssh/config* ]] || continue
    [[ "$row" == *.pub || "$row" == *known_hosts ]] || chmod 600 "$path"
    overlay_guarded "$row" "$HOME/.$row" "$row: preserving independent ~/.$row"
  done
fi
# Fill missing defaults; keep independent user model and routing overrides.
python3 "$REPO/codex/configure.py" "$HOME/.codex/config.toml"
# Hooks are additive; never replace an independently managed hooks file.
if [[ ! -e "$HOME/.codex/hooks.json" && ! -L "$HOME/.codex/hooks.json" ]] ||
   [[ -L "$HOME/.codex/hooks.json" && "$(readlink "$HOME/.codex/hooks.json")" == "$REPO/codex/hooks.json" ]]; then
  link "$REPO/codex/hooks.json" "$HOME/.codex/hooks.json"
else
  echo "codex: preserving existing hooks.json; merge $REPO/codex/hooks.json to enable live context"
fi
echo "codex: inspect /hooks to trust new or changed G-System hooks; AGENTS.md also provides a read fallback"

# Mirror repository-local guidance; user skills are installed neutrally above.
python3 "$REPO/scripts/mirror-claude.py" --discover

# Git identity
link "$(overlay git/gitconfig)" "$HOME/.gitconfig"

echo
echo "g-env base: done${INSTANCE:+ (instance $INSTANCE)}."
