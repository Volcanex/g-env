#!/usr/bin/env python3
"""Thin, fail-open adapter to the sibling g-anti-ai copy gate."""

import json
from pathlib import Path
import os
import sys


def report_broken(gate, err):
    """Fail loud: the hook still allows, but the break is recorded in g-levels as impossible."""
    try:
        import datetime
        from pathlib import Path
        log = Path(os.environ.get("GLEVELS_LOG", "/home/gabriel/g-levels/var/results.jsonl"))
        log.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "gate": gate, "version": 1,
               "charter": "g-system", "repo": "hooks", "path": os.path.basename(__file__),
               "result": "impossible", "note": f"hook broke and allowed: {type(err).__name__}: {err}"[:500],
               "by": "hook"}
        with log.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def main():
    try:
        # Source checkout also works on hosts where ganti is not pip-installed.
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "g-anti-ai"))
        from ganti.gate import evaluate
        output = evaluate(json.load(sys.stdin))
        if output:
            print(json.dumps(output))
    except (Exception, SystemExit) as e:
        report_broken("correct.copy.voice", e)


if __name__ == "__main__":
    main()
