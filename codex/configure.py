#!/usr/bin/env python3
"""Fill missing G-System routing defaults without replacing local overrides."""

import argparse
from pathlib import Path
import re
import tomllib


def configure(path):
    source = path.read_text() if path.exists() else ''
    config = tomllib.loads(source)
    if 'web_search' not in config:
        source = 'web_search = "live"\n' + source
    defaults = {
        'enabled': 'true',
        'max_concurrent_threads_per_session': '4',
        'default_subagent_model': '"gpt-6-luna"',
        'default_subagent_reasoning_effort': '"medium"',
    }
    agents = config.get('agents', {})
    additions = ''.join(f'{key} = {value}\n' for key, value in defaults.items()
                        if key not in agents)
    if additions:
        section = re.search(r'^\[agents\][ \t]*(?:#.*)?$', source, re.M)
        if section:
            source = source[:section.end()] + '\n' + additions + source[section.end():]
        elif re.search(r'^agents\s*=', source, re.M):
            raise ValueError('Inline agents config requires a manual merge')
        else:
            source += '\n[agents]\n' + additions
    if 'status_line' not in config.get('tui', {}):
        items = '["context-used", "model-with-reasoning", "project-name", "git-branch", "five-hour-limit", "weekly-limit", "thread-title"]'
        addition = f'status_line = {items}\n'
        section = re.search(r'^\[tui\][ \t]*(?:#.*)?$', source, re.M)
        if section:
            source = source[:section.end()] + '\n' + addition + source[section.end():]
        elif re.search(r'^tui\s*=', source, re.M):
            raise ValueError('Inline tui config requires a manual merge')
        else:
            source += '\n[tui]\n' + addition
    tomllib.loads(source)
    original = path.read_text() if path.exists() else ''
    if source != original:
        if path.exists():
            backup = path.with_name(path.name + '.before-g-routing')
            if not backup.exists():
                backup.write_text(original)
                backup.chmod(0o600)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        path.chmod(0o600)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    configure(args.config)
