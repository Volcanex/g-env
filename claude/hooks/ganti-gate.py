#!/usr/bin/env python3
"""Thin, fail-open adapter to the sibling g-anti-ai copy gate."""

import json
from pathlib import Path
import sys


def main():
    try:
        # Source checkout also works on hosts where ganti is not pip-installed.
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "g-anti-ai"))
        from ganti.gate import evaluate
        output = evaluate(json.load(sys.stdin))
        if output:
            print(json.dumps(output))
    except (Exception, SystemExit):
        pass


if __name__ == "__main__":
    main()
