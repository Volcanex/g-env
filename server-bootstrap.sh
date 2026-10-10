#!/usr/bin/env bash
# Bootstrap a remote server from the environment repo.
# Expects ~/environment/ to already be rsynced (ae-deploy does this).
# Idempotent: re-run anytime.
set -euo pipefail

REPO="$HOME/environment"
GREEN='\033[0;32m'; NC='\033[0m'
ok() { echo -e "${GREEN}✓${NC} $1"; }

# ── Symlinks ────────────────────────────────────────────────────────────────
link() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  [[ -L "$dst" ]] && rm "$dst"
  [[ -e "$dst" && ! -L "$dst" ]] && mv "$dst" "$dst.backup.$(date +%s)"
  ln -s "$src" "$dst"
}

bash "$REPO/bootstrap-base.sh"
link "$REPO/tmux/tmux.conf"      "$HOME/.tmux.conf"
link "$REPO/bin/ae-recover"      "$HOME/.local/bin/ae-recover"
ok "symlinks"

# ── Ensure ~/.local/bin is on PATH ───────────────────────────────────────────
if ! grep -q '\.local/bin' "$HOME/.bashrc" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
  ok "added ~/.local/bin to PATH in .bashrc"
fi

# ── Reload tmux config in any running sessions ───────────────────────────────
if command -v tmux &>/dev/null && tmux list-sessions &>/dev/null 2>&1; then
  tmux source-file "$HOME/.tmux.conf" 2>/dev/null && ok "tmux config reloaded" || true
fi

# ── mosh ────────────────────────────────────────────────────────────────────
if ! command -v mosh &>/dev/null; then
  sudo apt-get install -y -q mosh
  ok "mosh installed"
else
  ok "mosh already present"
fi

# ── Node (via nvm) ───────────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
  export NVM_DIR="$HOME/.nvm"
  # shellcheck disable=SC1091
  source "$NVM_DIR/nvm.sh"
  nvm install --lts
  ok "node installed"
else
  ok "node already present ($(node -v))"
fi

export NVM_DIR="$HOME/.nvm"
[[ -f "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh"

# ── Claude Code ──────────────────────────────────────────────────────────────
if ! command -v claude &>/dev/null; then
  npm install -g @anthropic-ai/claude-code --silent
  ok "claude installed"
else
  ok "claude already present ($(claude --version 2>/dev/null || echo '?'))"
fi

# ── Codex ────────────────────────────────────────────────────────────────────
if ! command -v codex &>/dev/null; then
  npm install -g @openai/codex --silent
  ok "codex installed"
else
  ok "codex already present"
fi

echo
echo "done. open a new shell or: source ~/.bashrc"
