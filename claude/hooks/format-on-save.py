#!/usr/bin/env python3
"""PostToolUse hook: format the file just written/edited, if a formatter exists.

Deterministic, idempotent, never blocks (always exits 0). Formatting only: no
lint autofix, no semantic changes. Determinism the model shouldn't spend tokens on.
"""

import json
import os
import shutil
import subprocess
import sys


def run(cmd):
    if shutil.which(cmd[0]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=20)
        except Exception:
            pass


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path or not os.path.isfile(path):
        return 0

    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        if shutil.which("ruff"):
            run(["ruff", "format", path])
        else:
            run(["black", "-q", path])
    elif ext in (".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".scss",
                 ".html", ".yaml", ".yml", ".vue", ".svelte"):
        run(["prettier", "--write", path])
    elif ext == ".go":
        run(["gofmt", "-w", path])
    elif ext == ".rs":
        run(["rustfmt", path])
    return 0


if __name__ == "__main__":
    sys.exit(main())
