#!/usr/bin/env bash
# Compatibility entry point for existing Claude SessionStart configurations.
exec python3 "$HOME/.agents/session-context.py" --hook "$@"
