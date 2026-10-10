#!/usr/bin/env python3
"""Install neutral context and skills; migrate legacy roots without deleting data."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'context'))
from instance import HINT, instance as find_instance  # noqa: E402


def exists(path):
    return path.exists() or path.is_symlink()


def backup(path):
    target = path.with_name(path.name + '.pre-neutral')
    index = 1
    while exists(target):
        target = path.with_name(path.name + f'.pre-neutral.{index}')
        index += 1
    path.rename(target)
    print(f'Preserved {path} at {target}')
    return target


def link(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and target.resolve() == source.resolve():
        return
    if exists(target):
        backup(target)
    target.symlink_to(source)


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text)
    temp.chmod(0o600)
    temp.replace(path)


def legacy_host(source, managed, legacy_root, neutral_root, seen=None):
    if not source.is_file() or source.resolve() in managed:
        return ''
    seen = {} if seen is None else seen
    seen.setdefault(source.resolve(), neutral_root / 'host.md')
    lines = []
    fence = None
    for line in source.read_text().splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
        match = re.fullmatch(r'@(.+\.md)\s*', line.strip()) if fence is None else None
        if match:
            imported = Path(match.group(1)).expanduser()
            imported = imported if imported.is_absolute() else source.resolve().parent / imported
            imported = imported.resolve()
            if imported in managed:
                continue
            if imported.is_relative_to(legacy_root) and imported.is_file():
                destination = neutral_root / 'imports' / imported.relative_to(legacy_root)
                if imported in seen:
                    destination = seen[imported]
                else:
                    seen[imported] = destination
                    migrated = legacy_host(imported, managed, legacy_root, neutral_root, seen)
                    if destination.exists() and destination.read_text() != migrated:
                        # An independent neutral import wins; preserve the legacy
                        # version beside it and point this import to that version.
                        destination = destination.with_name(destination.stem + '-legacy' + destination.suffix)
                        if destination.exists() and destination.read_text() != migrated:
                            raise ValueError(f'Preserved conflicting host import: {destination}')
                    seen[imported] = destination
                    if not destination.exists():
                        write_private(destination, migrated)
                imported = destination
            line = '@' + str(imported) + '\n'
        lines.append(line)
    return ''.join(lines).strip()


def copy_skills(source, target):
    """Preserve unmanaged directories and links, including broken links."""
    conflicts = []
    if not source.is_dir() or source.resolve() == target.resolve():
        return conflicts
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        if exists(destination):
            if entry.resolve() != destination.resolve():
                conflicts.append(entry.name)
            continue
        if entry.is_symlink():
            resolved = entry.resolve()
            if resolved.is_relative_to(source.resolve()):
                resolved = target / resolved.relative_to(source.resolve())
            destination.symlink_to(resolved)
        elif entry.is_dir():
            shutil.copytree(entry, destination, symlinks=True)
        else:
            shutil.copy2(entry, destination)
    return conflicts


def install_skills(repo, home):
    neutral = home / '.agents/skills'
    legacy = home / '.claude/skills'
    previous = None
    if neutral.is_symlink():
        previous = neutral.resolve()
        backup(neutral)
    neutral.mkdir(parents=True, exist_ok=True)
    bundled = (repo / 'skills').resolve()
    if previous and previous.is_dir() and previous != bundled:
        copy_skills(previous, neutral)
    already_copied = previous and legacy.resolve() == previous
    conflicts = [] if already_copied or legacy.resolve() == bundled else copy_skills(legacy, neutral)
    ledger = home / '.agents/managed-skills.json'
    try:
        managed = json.loads(ledger.read_text())
        if not isinstance(managed, dict):
            raise ValueError('managed skill ledger must be an object')
    except FileNotFoundError:
        managed = {}
    candidates = {}
    for directory in sorted((repo / 'skills').glob('*/')):
        if (directory / 'SKILL.md').is_file():
            candidates[directory.name] = directory.resolve()
    for directory in sorted(repo.parent.glob('*/skills/*/')):
        if (directory / 'SKILL.md').is_file():
            candidates.setdefault(directory.name, directory.resolve())
    installed = {}
    for name, old in managed.items():
        if Path(name).name != name:
            continue
        target = neutral / name
        if name not in candidates and target.is_symlink() and str(target.readlink()) == old:
            target.unlink()
    for name, source in candidates.items():
        target = neutral / name
        if exists(target):
            if target.is_symlink() and (target.resolve() == source or
                                       str(target.readlink()) == managed.get(name)):
                target.unlink()
            else:
                print(f'Skills: preserved unmanaged {target}')
                continue
        target.symlink_to(source)
        installed[name] = str(source)
    write_private(ledger, json.dumps(installed, indent=2) + '\n')
    if conflicts:
        # Keep both active namespaces when names mean different things. Claude's
        # independent entry must not be replaced by Codex's entry with that name.
        print('Skills: preserved divergent Claude library: ' + ', '.join(conflicts))
        for name in installed:
            target = legacy / name
            if not exists(target):
                target.symlink_to(neutral / name)
    else:
        link(neutral, legacy)


def materialize_client_root(path):
    """Old bootstraps linked whole vendor roots; never write through into genv."""
    if not path.is_symlink():
        return
    original = path.resolve()
    if not original.is_dir():
        raise ValueError(f'Preserved unavailable client root: {path}')
    backup(path)
    path.mkdir()
    for entry in original.iterdir():
        destination = path / entry.name
        if entry.is_symlink() or entry.is_dir():
            destination.symlink_to(entry.resolve())
        else:
            shutil.copy2(entry, destination)


def install(repo, home, instance=None):
    given, repo, home = repo, repo.resolve(), home.resolve()
    for vendor in ('.claude', '.codex'):
        materialize_client_root(home / vendor)
    root = home / '.agents'
    root.mkdir(parents=True, exist_ok=True)
    sources = {'host': str(root / 'host.md'), 'shared': str(repo / 'context/shared.md')}
    files = {'conventions': 'conventions.md', 'topology': 'CLAUDE.md'}
    if instance:
        sources.update((name, str(instance.resolve() / file)) for name, file in files.items())
    managed = {Path(p).resolve() for k, p in sources.items() if k != 'host'}
    managed.update((repo / name).resolve() for name in ('claude/CLAUDE.md', 'claude/AGENTS.md', 'claude/root.md'))
    old_host = home / '.claude/CLAUDE.md'
    migrated = legacy_host(old_host, managed, (home / '.claude').resolve(), root)
    host = root / 'host.md'
    original = host.read_text() if host.exists() else ''
    if migrated and migrated not in original:
        original += ('\n\n' if original else '') + migrated + '\n'
        write_private(host, original)
    elif not host.exists():
        write_private(host, '# Host context\n\nAdd host-specific facts here; shared rules and topology are loaded separately.\n')
    config = root / 'context.json'
    if config.exists():
        data = json.loads(config.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('sources', {}), dict):
            raise ValueError(f'Preserved invalid context config: {config}')
    else:
        data = {}
    configured = data.setdefault('sources', {})
    for name, value in sources.items():
        # Move a source to the instance only while it still holds the old sibling
        # gcore default; a value someone chose stays.
        old = {str(p.parent / 'gcore' / files[name]) for p in (given, repo)} if name in files else ()
        if configured.get(name) in old and Path(value).exists():
            configured[name] = value
        configured.setdefault(name, value)
    serialized = json.dumps(data, indent=2) + '\n'
    if not config.exists() or config.read_text() != serialized:
        write_private(config, serialized)
    link(repo / 'context/session-context.py', root / 'session-context.py')
    install_skills(repo, home)
    link(repo / 'claude/root.md', old_host)
    link(old_host, home / '.claude/AGENTS.md')
    link(repo / 'codex/AGENTS.md', home / '.codex/AGENTS.md')
    link(repo / 'codex/session-context.py', home / '.codex/session-context.py')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--instance', '--gcore', dest='instance', type=Path,
                        help='instance checkout (gp-core); --gcore is the legacy name')
    args = parser.parse_args()
    instance = args.instance or find_instance(repo=args.repo)
    if not instance:
        print(HINT, file=sys.stderr)
    install(args.repo, args.home, instance)


if __name__ == '__main__':
    main()
