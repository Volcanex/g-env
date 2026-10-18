"""Project discovery and publication, using disposable repositories and SSH hosts."""
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_loader('g_projects', SourceFileLoader('g_projects', str(REPO / 'bin/g-projects')))
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def init(repo):
    repo.mkdir(parents=True)
    git(repo, 'init', '--quiet', '--initial-branch=main')
    git(repo, 'config', 'user.name', 'Project test')
    git(repo, 'config', 'user.email', 'projects@example.invalid')
    return repo


def repo(root, name, origin='git@github.com:Operator/project.git', gcore=None,
         complete=False, commits=False):
    directory = init(root / name)
    if origin:
        git(directory, 'remote', 'add', 'origin', origin)
    if gcore or complete:
        (directory / 'pyproject.toml').write_text(
            '[project]\nname = "project"\nversion = "1"\ndescription = "Test"\n'
            f'[tool.{gcore or "gcore"}]\ndepends = []\n')
    if complete:
        (directory / 'CLAUDE.md').write_text('Test repository.\n')
    if commits:
        (directory / 'README').write_text('Test repository.\n')
        git(directory, 'add', '.')
        git(directory, 'commit', '--quiet', '-m', 'Create test repository')
    return directory


def write_manifest(setup, projects=None, system=None):
    data = {'hosts': [{'id': 'h'}, {'id': 'b'}], 'projects': projects or [], 'system': system or []}
    setup.manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    return data


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', '/dev/null')
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    instance = init(tmp_path / 'instance')
    git(instance, 'remote', 'add', 'origin', 'https://github.com/Operator/gp-core.git')
    root = tmp_path / 'repos'
    remote = tmp_path / 'remote'
    root.mkdir()
    remote.mkdir()
    ssh_bin = tmp_path / 'bin'
    ssh_bin.mkdir()
    ssh = ssh_bin / 'ssh'
    ssh.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
with open(os.environ['PROJECT_TEST_SSH_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
host = sys.argv[-2]
target = json.loads(os.environ['PROJECT_TEST_HOSTS'])[host]
if isinstance(target, int):
    print('test host unavailable', file=sys.stderr)
    sys.exit(target)
result = subprocess.run([sys.executable, '-', target], input=sys.stdin.read(), text=True)
sys.exit(result.returncode)
''')
    ssh.chmod(0o755)
    monkeypatch.setenv('PATH', str(ssh_bin) + ':' + os.environ['PATH'])
    for key, value in {'G_INSTANCE': instance, 'GCORE_DIR': instance,
                       'G_PROJECTS_MANIFEST': instance / 'manifest.json', 'G_PROJECTS_ROOT': root,
                       'PROJECT_TEST_HOSTS': json.dumps({'b': str(remote)}),
                       'PROJECT_TEST_SSH_LOG': tmp_path / 'ssh.log'}.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv('G_PROJECTS_GIT_DIR', raising=False)
    state = SimpleNamespace(instance=instance, manifest=instance / 'manifest.json', root=root,
                            remote=remote, tmp=tmp_path, ssh_log=tmp_path / 'ssh.log')
    write_manifest(state)
    return state


def test_worktree_and_symlink_skipped(setup, capsys):
    original = repo(setup.root, 'original', commits=True)
    git(original, 'worktree', 'add', '--quiet', '-b', 'worktree', str(setup.root / 'working-copy'))
    (setup.root / 'linked-copy').symlink_to(original, target_is_directory=True)
    before = setup.manifest.read_bytes()
    assert gp.main(['sync']) == 0
    output = capsys.readouterr().out
    assert 'add h/original:' in output
    assert 'working-copy' not in output and 'linked-copy' not in output
    assert setup.manifest.read_bytes() == before


@pytest.mark.parametrize('origin', ['git@github.com:SomeoneElse/project.git',
                                   'https://gitlab.com/Operator/project',
                                   'https://github.com.evil.invalid/Operator/project'])
def test_non_owner_remote_skipped(setup, capsys, origin):
    repo(setup.root, 'external', origin=origin)
    assert gp.main(['sync']) == 0
    assert capsys.readouterr().out == 'dry run: 0 project(s) to add\n'


@pytest.mark.parametrize('table', ['gcore', 'g-core'])
def test_tool_gcore_without_remote_registered(setup, capsys, table):
    repo(setup.root, 'declared', origin=None, gcore=table)
    assert gp.main(['sync']) == 0
    lines = capsys.readouterr().out.splitlines()
    entry = json.loads(lines[0].split(': ', 1)[1])
    assert entry == {'id': 'declared', 'host': 'h', 'managed': False}
    assert list(entry) == ['id', 'host', 'managed']


@pytest.mark.parametrize('manifest_url,origin', [
    ('https://github.com/OPERATOR/Project.git/', 'git@github.com:operator/project.git'),
    ('git@github.com:Operator/PROJECT.git', 'https://github.com/operator/project/'),
    ('https://github.com/operator/project', 'ssh://git@github.com/Operator/Project.git'),
])
@pytest.mark.parametrize('section', ['projects', 'system'])
def test_url_match_dedupe(setup, capsys, manifest_url, origin, section):
    write_manifest(setup, **{section: [{'id': 'different-id', 'url': manifest_url}]})
    repo(setup.root, 'checkout', origin=origin)
    assert gp.main(['sync']) == 0
    assert capsys.readouterr().out == 'dry run: 0 project(s) to add\n'


def test_id_dedupe_and_missing_urls_do_not_match_each_other(setup, capsys):
    write_manifest(setup, projects=[{'id': 'known'}, {'id': 'other'}])
    repo(setup.root, 'known', gcore='gcore', origin=None)
    repo(setup.root, 'new', gcore='gcore', origin=None)
    assert gp.main(['sync']) == 0
    output = capsys.readouterr().out
    assert 'add h/new:' in output and 'add h/known:' not in output


@pytest.mark.parametrize('style', ['compact', 'crlf', 'escaped-unicode'])
def test_format_guard_aborts_without_writing(setup, capsys, style):
    data = write_manifest(setup, projects=[{'id': 'café'}])
    if style == 'compact':
        body = json.dumps(data).encode()
    elif style == 'crlf':
        body = setup.manifest.read_bytes().replace(b'\n', b'\r\n')
    else:
        body = (json.dumps(data, indent=2, ensure_ascii=True) + '\n').encode()
    setup.manifest.write_bytes(body)
    repo(setup.root, 'new')
    assert gp.main(['sync', '--apply']) == 1
    assert 'refusing --apply without writing' in capsys.readouterr().err
    assert setup.manifest.read_bytes() == body
    assert git(setup.instance, 'diff', '--cached', '--name-only') == ''
    assert not setup.ssh_log.exists()


def test_drift_report_preserves_entries(setup, capsys):
    write_manifest(setup, projects=[{'id': 'gone', 'host': 'h', 'managed': True},
                                     {'id': 'fails', 'host': 'h', 'managed': True},
                                     {'id': 'passes', 'host': 'h', 'managed': False},
                                     {'id': 'remote-gone', 'host': 'b'},
                                     {'id': 'remote-passes', 'host': 'b', 'managed': False}])
    repo(setup.root, 'fails')
    repo(setup.root, 'passes', complete=True)
    repo(setup.remote, 'remote-passes', complete=True)
    before = setup.manifest.read_bytes()
    assert gp.main(['sync']) == 0
    assert capsys.readouterr().out == (
        'drift h/gone: directory missing\n'
        'drift h/fails: managed=true, check.py fails\n'
        'drift h/passes: managed=false, check.py passes\n'
        'drift b/remote-gone: directory missing\n'
        'dry run: 0 project(s) to add\n')
    assert setup.manifest.read_bytes() == before


def test_no_remote_diagnostic_excludes_work_and_backup_dirs(setup, capsys):
    for name in ['g-scraper', 'empty', 'foo-wt-feature', 'foo-wt', 'foo.bak', 'foo-backup']:
        repo(setup.root, name, origin=None, commits=name != 'empty')
    other = repo(setup.root, 'upstream-only', origin=None, commits=True)
    git(other, 'remote', 'add', 'upstream', 'https://github.com/SomeoneElse/project')
    repo(setup.remote, 'remote-no-origin', origin=None, commits=True)
    assert gp.main(['sync']) == 0
    assert capsys.readouterr().out == 'no remote h/g-scraper: has commits\ndry run: 0 project(s) to add\n'


def test_remote_registration_is_unmanaged_and_deduped_across_hosts(setup, capsys, monkeypatch):
    repo(setup.root, 'local', complete=True)
    repo(setup.remote, 'local-copy', complete=True)
    repo(setup.remote, 'remote', origin='https://github.com/Operator/remote', complete=True)
    checked = []
    original = gp.compliant

    def compliant(directory, *args):
        checked.append(directory)
        return original(directory, *args)

    monkeypatch.setattr(gp, 'compliant', compliant)
    assert gp.main(['sync']) == 0
    output = capsys.readouterr().out
    assert 'add h/local:' in output and '"managed": true' in output
    assert 'add b/remote:' in output and '"managed": false' in output
    assert 'local-copy' not in output
    assert checked == [setup.root / 'local']
    assert json.loads(setup.ssh_log.read_text())[0:5] == ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'b']


def test_unreachable_host_is_skipped_without_drift(setup, capsys, monkeypatch):
    write_manifest(setup, projects=[{'id': 'remote-missing', 'host': 'b'}])
    monkeypatch.setenv('PROJECT_TEST_HOSTS', json.dumps({'b': 255}))
    assert gp.main(['sync']) == 0
    assert capsys.readouterr().out == 'skip b: test host unavailable\ndry run: 0 project(s) to add\n'


def test_remote_scan_error_is_an_error(setup, capsys, monkeypatch):
    monkeypatch.setenv('PROJECT_TEST_HOSTS', json.dumps({'b': 2}))
    assert gp.main(['sync']) == 1
    assert 'scan b: test host unavailable' in capsys.readouterr().err


@pytest.mark.parametrize('existing_topology', [False, True])
def test_apply_commits_only_manifest_and_hook_output_and_pushes(setup, capsys, existing_topology):
    existing = {'id': 'existing', 'description': 'café', 'custom': ['keep', 1]}
    write_manifest(setup, projects=[existing])
    repo(setup.root, 'new-project', origin='git@github.com:Operator/NEW.git', complete=True)
    publication = init(setup.tmp / 'publication')
    manifest = publication / 'manifest.json'
    manifest.write_bytes(setup.manifest.read_bytes())
    for name in ['staged', 'dirty']:
        (publication / name).write_text('original\n')
    if existing_topology:
        (publication / 'CLAUDE.md').write_text('Original topology\n')
        (publication / 'AGENTS.md').symlink_to('CLAUDE.md')
    git(publication, 'add', '.')
    git(publication, 'commit', '--quiet', '-m', 'Create publication fixture')
    bare = setup.tmp / 'remote.git'
    subprocess.run(['git', 'init', '--quiet', '--bare', '--initial-branch=main', str(bare)], check=True)
    git(publication, 'remote', 'add', 'origin', str(bare))
    git(publication, 'push', '--quiet', '-u', 'origin', 'main')
    for name in ['staged', 'dirty']:
        (publication / name).write_text('unrelated edit\n')
    git(publication, 'add', 'staged')
    hook = publication / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nprintf "Rendered topology\\n" > CLAUDE.md\n'
                    'ln -sf CLAUDE.md AGENTS.md\ngit add CLAUDE.md AGENTS.md\n')
    hook.chmod(0o755)
    args = ['sync', '--apply', '--manifest', str(manifest), '--git-dir', str(publication)]
    assert gp.main(args) == 0
    assert 'added, committed and pushed 1 project(s)' in capsys.readouterr().out
    data = json.loads(manifest.read_bytes())
    assert data['projects'][0] == existing
    assert data['projects'][1] == {'id': 'new-project', 'url': 'https://github.com/operator/new',
                                    'host': 'h', 'managed': True}
    assert list(data['projects'][1]) == ['id', 'url', 'host', 'managed']
    changed = git(publication, 'show', '--format=', '--name-only', 'HEAD').splitlines()
    assert changed == (['CLAUDE.md', 'manifest.json'] if existing_topology
                       else ['AGENTS.md', 'CLAUDE.md', 'manifest.json'])
    assert git(publication, 'diff', '--cached', '--name-only') == 'staged'
    assert git(publication, 'diff', '--name-only') == 'dirty'
    assert 'new-project' in git(publication, 'log', '-1', '--format=%s')
    assert git(bare, 'rev-parse', 'main') == git(publication, 'rev-parse', 'HEAD')
    assert json.loads(git(bare, 'show', 'main:manifest.json')) == data
    head = git(publication, 'rev-parse', 'HEAD')
    assert gp.main(args) == 0
    assert 'sync: 0 project(s) to add' in capsys.readouterr().out
    assert git(publication, 'rev-parse', 'HEAD') == head
