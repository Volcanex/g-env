#!/usr/bin/env python3
"""Mirror repository guidance with relative links; preserve independent instructions."""
import argparse
from collections import Counter
import os
from pathlib import Path
import subprocess

SKIP = {'.git', 'node_modules', 'vendor', '.venv', 'venv', '__pycache__', 'Library',
        '.cache', '.npm', '.terraform', 'target', 'dist'}
SYSTEM_ROOTS = {'/proc', '/sys', '/dev', '/run', '/lost+found', '/var/lib',
                '/var/cache', '/var/log', '/var/tmp', '/usr/bin', '/usr/sbin',
                '/usr/lib', '/usr/lib64', '/usr/share', '/usr/include',
                '/bin', '/sbin', '/lib', '/lib64'}


def link(source, target, *, identical=False, dry_run=False, label='guidance'):
    relative = os.path.relpath(source, target.parent)
    if target.is_symlink():
        if os.readlink(target) == relative:
            return 'current'
        print(f'Conflict (preserved link): {target}')
        return 'conflict'
    if target.exists():
        if not (identical and target.is_file() and source.read_bytes() == target.read_bytes()):
            print(f'Conflict (preserved independent {label}): {target}')
            return 'conflict'
        if not dry_run:
            target.unlink()
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(relative, target_is_directory=source.is_dir())
    print(f'{"Would link" if dry_run else "Linked"} {target} -> {relative}')
    return 'linked'


def git(root, *args):
    # Only read-only ls-files/worktree discovery; trust this explicit directory
    # for this process without changing global Git ownership protection.
    return subprocess.check_output(['git', '-c', f'safe.directory={Path(root).resolve()}', '-C', str(root), *args], text=True,
                                   stderr=subprocess.PIPE, timeout=20)


def valid_git_root(root):
    """A .git marker is valid only when Git resolves this exact worktree root."""
    root = Path(root).resolve()
    try:
        actual = Path(git(root, 'rev-parse', '--show-toplevel').strip()).resolve()
    except (subprocess.SubprocessError, OSError):
        return False
    return actual == root


def discover(roots, depth=3, server_wide=False, skipped_invalid=None, guidance_dirs=None):
    """Scan for repositories, then include registered linked worktrees.

    Ordinary discovery stays depth-bounded. Server-wide discovery walks the
    filesystem while pruning OS and package trees that cannot contain user repos.
    """
    repos = set()
    for root in roots:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            continue
        for directory, names, files in os.walk(root, followlinks=False):
            here = Path(directory)
            if server_wide and guidance_dirs is not None and {'CLAUDE.md', 'AGENTS.md'} & set(files):
                guidance_dirs.add(here.resolve())
            if '.git' in names or '.git' in files:
                if valid_git_root(here):
                    repos.add(here)
                elif skipped_invalid is not None:
                    skipped_invalid.add(here)
                if not server_wide:
                    names[:] = []
                    continue
                names[:] = [n for n in names if n != '.git']
            level = len(here.relative_to(root).parts)
            names[:] = [n for n in names if n not in SKIP and (server_wide or not n.startswith('.'))]
            if server_wide and root == Path('/'):
                names[:] = [n for n in names
                            if str((here / n).resolve()) not in SYSTEM_ROOTS]
            if depth is not None and level >= depth:
                names[:] = []
    for repo in list(repos):
        try:
            for row in git(repo, 'worktree', 'list', '--porcelain', '-z').split('\0'):
                if row.startswith('worktree '):
                    path = Path(row[9:])
                    if path.is_dir():
                        path = path.resolve()
                        if valid_git_root(path):
                            repos.add(path)
                        elif skipped_invalid is not None:
                            skipped_invalid.add(path)
        except (subprocess.SubprocessError, OSError) as exc:
            print(f'Worktree discovery failed: {repo}: {type(exc).__name__}')
    return sorted(repos)


def is_knowledge_note(source):
    """Basic Memory imports are indexed notes, not executable repo guidance.

    Read only the initial frontmatter mapping. Filename alone is insufficient:
    a knowledge repository can contain imported CLAUDE.md files too.
    """
    with source.open(encoding='utf-8-sig') as stream:
        if stream.readline().strip() != '---':
            return False
        metadata = {}
        for line in stream:
            if line.strip() in {'---', '...'}:
                return metadata.get('type') == 'note' and bool(metadata.get('permalink'))
            if line[:1].isspace() or ':' not in line:
                continue
            key, value = line.rstrip().split(':', 1)
            metadata[key] = value.strip().strip('"\'')
    return False


def mirror_guidance(directory, *, dry_run=False):
    """Reconcile one CLAUDE.md/AGENTS.md pair, preserving divergent files."""
    claude = directory / 'CLAUDE.md'
    agents = directory / 'AGENTS.md'
    # ls-files includes sparse-checkout paths and pending deletions. With neither
    # name materialized there is no guidance to mirror or conflict to resolve.
    if not any(p.exists() or p.is_symlink() for p in (claude, agents)):
        return 'absent'
    if any(p.is_file() and is_knowledge_note(p) for p in (claude, agents)):
        return 'knowledge_notes'

    # Either orientation of an existing pair is current only when both names
    # resolve to the same file. samefile raises on dangling links and loops.
    if claude.is_symlink() or agents.is_symlink():
        try:
            if os.path.samefile(claude, agents):
                return 'current'
        except OSError:
            pass

    # A missing CLAUDE target behind the generated AGENTS link commonly means
    # an intentional instruction deletion in an old worktree. Preserve it.
    if (agents.is_symlink() and os.readlink(agents) == os.path.relpath(claude, agents.parent)
            and not claude.exists() and not claude.is_symlink()):
        print(f'Stale generated link preserved: {agents}')
        return 'stale_links'
    if (claude.is_symlink() and os.readlink(claude) == os.path.relpath(agents, claude.parent)
            and not agents.exists() and not agents.is_symlink()):
        print(f'Stale generated link preserved: {claude}')
        return 'stale_links'

    c_exists = claude.exists() or claude.is_symlink()
    a_exists = agents.exists() or agents.is_symlink()
    c_file, a_file = claude.is_file(), agents.is_file()
    if c_file and not a_exists:
        return link(claude, agents, dry_run=dry_run)
    if a_file and not c_exists:
        return link(agents, claude, dry_run=dry_run)
    if c_file and a_file and not claude.is_symlink() and not agents.is_symlink():
        if claude.read_bytes() == agents.read_bytes():
            return link(claude, agents, identical=True, dry_run=dry_run)
    print(f'Conflict (preserved guidance pair): {directory}')
    return 'conflict'


def mirror(root, dry_run=False, seen_guidance_dirs=None):
    root = Path(root).resolve()
    paths = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                '--', 'CLAUDE.md', ':(glob)**/CLAUDE.md', 'AGENTS.md',
                ':(glob)**/AGENTS.md').split('\0')
    counts = Counter()
    directories = {root / name for name in paths if name and Path(name).name in {'CLAUDE.md', 'AGENTS.md'}
                   and ((root / name).is_file() or (root / name).is_symlink())}
    guidance = {p.parent for p in directories}
    if seen_guidance_dirs is not None:
        seen_guidance_dirs.update(guidance)
    for directory in sorted(guidance):
        counts[mirror_guidance(directory, dry_run=dry_run)] += 1
    source = root / '.claude' / 'skills'
    if source.is_dir():
        counts[f'skill_{link(source, root / ".agents" / "skills", dry_run=dry_run, label="skill alias")}'] += 1
    return counts


def mirror_all(repositories, guidance_dirs, *, dry_run=False):
    """Mirror repository-indexed docs and filesystem-discovered instruction dirs once."""
    counts = Counter()
    covered = set()
    failures = []
    for repo in sorted(repositories):
        try:
            counts.update(mirror(repo, dry_run, seen_guidance_dirs=covered))
        except (subprocess.SubprocessError, OSError) as exc:
            failures.append((repo, type(exc).__name__))
    for directory in sorted(set(guidance_dirs) - covered):
        try:
            counts[mirror_guidance(directory, dry_run=dry_run)] += 1
        except OSError as exc:
            failures.append((directory, type(exc).__name__))
    return counts, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repos', nargs='*')
    parser.add_argument('--user-skills', action='store_true')
    parser.add_argument('--discover', action='store_true', help='Scan genv siblings and home (depth 3), including registered worktrees')
    parser.add_argument('--server-wide', action='store_true', help='Also discover repositories across the server, pruning OS and package trees')
    parser.add_argument('--discover-root', action='append', default=[])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    roots = list(args.discover_root)
    if args.discover:
        roots += [Path(__file__).resolve().parents[2], Path.home()]
    skipped_invalid = set()
    guidance_dirs = set()
    repos = set(discover(roots, skipped_invalid=skipped_invalid))
    if args.server_wide:
        repos.update(discover([Path('/')], depth=None, server_wide=True,
                              skipped_invalid=skipped_invalid, guidance_dirs=guidance_dirs))
    for path in args.repos:
        candidate = Path(path).resolve()
        if valid_git_root(candidate):
            repos.add(candidate)
        else:
            skipped_invalid.add(candidate)
    counts, failures = mirror_all(repos, guidance_dirs, dry_run=args.dry_run)
    failed = len(failures)
    for path, error in failures:
        print(f'Mirror failed: {path}: {error}')
    if args.user_skills:
        source = Path.home() / '.agents' / 'skills'
        if source.is_dir() and not source.is_symlink():
            counts[link(source, Path.home() / '.claude' / 'skills', dry_run=args.dry_run)] += 1
        else:
            print('Neutral user skills not installed; run scripts/install-context.py first')
    scope = 'filesystem-root (virtual filesystems and package caches pruned; mounts included)' if args.server_wide else 'explicit/depth-bounded roots'
    for path in sorted(skipped_invalid)[:10]:
        print(f'Skipped invalid repository marker: {path}')
    if len(skipped_invalid) > 10:
        print(f'Skipped invalid repository markers: {len(skipped_invalid) - 10} more')
    print(f'Mirror summary: scope={scope} repositories={len(repos)} linked={counts["linked"]} current={counts["current"]} conflicts={counts["conflict"]} stale_links={counts["stale_links"]} skills_linked={counts["skill_linked"]} skills_current={counts["skill_current"]} skill_conflicts={counts["skill_conflict"]} knowledge_notes={counts["knowledge_notes"]} invalid_skipped={len(skipped_invalid)} failed={failed}')
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
