#!/usr/bin/env python3
"""Compatibility entry point; all context loading lives in the neutral layer."""
import runpy
from pathlib import Path

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'context/session-context.py'), run_name='__main__')
