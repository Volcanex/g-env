"""eburn scheduling and routing, without launching model jobs."""
import datetime as dt
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

REPO = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(file)))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


eb = module('g_eburn', REPO / 'bin/g-eburn')
runner = module('eburn_agent_run', REPO / 'bin/g-agent-run')
NOW = dt.datetime(2026, 10, 13, 12, tzinfo=dt.timezone.utc)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(eb.temperature, 'EBURN_ABOVE', {})
    root = tmp_path / 'eburn'
    pace = tmp_path / 'pace.json'
    repos = tmp_path / 'repos'
    repos.mkdir()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'projects': [{'id': f'repo{i}', 'managed': True} for i in range(12)]}))
    for i in range(12):
        (repos / f'repo{i}').mkdir()
    for key, value in {'DIR': root, 'PACE': pace, 'MODE': tmp_path / 'mode.json', 'REPOS': repos,
                       'DB': repos / 'repo0/var/gadmin.db', 'MANIFEST': manifest}.items():
        monkeypatch.setenv('G_EBURN_' + key, str(value))
    monkeypatch.setattr(eb.temperature, 'state', lambda *a, **kw: {'temp_override': None})
    mock = Mock(side_effect=AssertionError('unexpected subprocess'))
    monkeypatch.setattr(eb.subprocess, 'run', mock)
    state = {'enabled': True, 'target': 50, 'queue': [], 'running': [], 'last': {}}
    return SimpleNamespace(root=root, pace=pace, repos=repos, state=state, mock=mock)


def write_pace(s, elapsed=42, remaining=100, risk=None, effective=None):
    reset = NOW + dt.timedelta(hours=168 - elapsed)
    row = {'provider': 'codex', 'window_hours': 168, 'resets_at': reset.isoformat(),
           'remaining_pct': remaining}
    if risk:
        row['reset_risk'] = risk
    if effective:
        row['effective_resets_at'] = effective.isoformat()
    s.pace.write_text(json.dumps({'computed_at': NOW.isoformat(), 'quotas': [row]}))
    return row


def run(s, dry=False):
    if not dry:
        s.root.mkdir(exist_ok=True)
    eb.run(SimpleNamespace(command='tick', dry_run=dry), s.root, s.state, NOW)


def fake_jobs(s):
    def dispatch(argv, **kw):
        directory = Path(kw['env']['G_AGENT_JOBS']) / str(s.mock.call_count)
        directory.mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout=f'STILL RUNNING: g-agent-call --wait {directory}\n')
    s.mock.side_effect = dispatch


@pytest.mark.parametrize('elapsed,risk,expected', [(0, None, 0), (42, None, 25), (84, None, 50),
                                                 (120, None, 50), (12, 'early', 25), (24, 'early', 50)])
def test_floor(setup, elapsed, risk, expected):
    write_pace(setup, elapsed=elapsed, risk=risk)
    q, _ = eb.quota(NOW, 50)
    assert q['floor'] == expected


def test_interactive_usage_and_custom_target(setup):
    write_pace(setup, remaining=70)
    q, _ = eb.quota(NOW, 50)
    assert q['mode'] == 'idle'
    q, _ = eb.quota(NOW, 80)
    assert q['mode'] == 'floor' and q['floor'] == 40


def test_effective_reset_does_not_move_window_start(setup):
    write_pace(setup, elapsed=12, risk='early', effective=NOW + dt.timedelta(hours=10))
    q, _ = eb.quota(NOW, 50)
    assert q['floor'] == 25
    assert q['mode'] == 'emergency' and q['goal'] == 95 and q['cap'] == 10


@pytest.mark.parametrize('remaining,latched,mode', [(11, False, 'emergency'), (10, False, 'idle'),
                                                   (9, True, 'emergency'), (5, True, 'idle')])
def test_emergency_threshold_and_latch(setup, remaining, latched, mode):
    row = write_pace(setup, elapsed=156, remaining=remaining)
    q, _ = eb.quota(NOW, 50, row['resets_at'] if latched else None)
    assert q['mode'] == mode


def test_old_latch_not_reused(setup):
    write_pace(setup, elapsed=156, remaining=8)
    assert eb.quota(NOW, 50, 'old window')[0]['mode'] == 'idle'


@pytest.mark.parametrize('case', ['missing', 'stale', 'expired', 'malformed'])
def test_bad_pace_no_dispatch(setup, case, capsys):
    if case != 'missing':
        write_pace(setup, elapsed=169 if case == 'expired' else 42)
        data = json.loads(setup.pace.read_text())
        if case == 'stale':
            data['computed_at'] = (NOW - dt.timedelta(seconds=3601)).isoformat()
        setup.pace.write_text('{' if case == 'malformed' else json.dumps(data))
    run(setup)
    setup.mock.assert_not_called()
    assert 'pace' in capsys.readouterr().out or case == 'expired'


@pytest.mark.parametrize('override', [None, 'COLD', 'FREEZING'])
def test_disabled_or_cold(setup, monkeypatch, override, capsys):
    write_pace(setup, elapsed=160)
    if override is None:
        setup.state['enabled'] = False
    monkeypatch.setattr(eb.temperature, 'state', lambda *a, **k: {'temp_override': override})
    run(setup)
    setup.mock.assert_not_called()
    assert 'disabled:' in capsys.readouterr().out
    assert not (setup.root / 'state.json').exists()


def create_ticket(s):
    db = eb.path('DB', '')
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE tickets (id INTEGER, created_at TEXT, updated_at TEXT, status TEXT, '
                     'scope TEXT, kind TEXT, severity TEXT, page TEXT, elements TEXT, edits TEXT, note TEXT)')
        conn.execute("INSERT INTO tickets VALUES (1, 'a', 'b', 'open', 'page', 'bug', 'major', '/', '[]', '[]', 'fix')")
        conn.execute("INSERT INTO tickets VALUES (2, 'a', 'b', 'baked', 'page', 'bug', 'major', '/', '[]', '[]', 'fixed')")


def test_source_priority_and_ticket_readonly(setup):
    create_ticket(setup)
    queued = {'key': 'queue', 'source': 'queue', 'workdir': str(setup.repos / 'repo1')}
    setup.state['queue'] = [queued]
    assert eb.choose(setup.state, NOW)['source'] == 'queue'
    setup.state['queue'] = []
    item = eb.choose(setup.state, NOW)
    assert item['source'] == 'ticket' and item['sandbox'] == 'read-only'
    assert 'Never apply' in eb.brief(item) and 'FINDINGS: <n>' in eb.brief(item)
    setup.state['last'][item['key']] = NOW.isoformat()
    assert eb.choose(setup.state, NOW)['source'] == 'rotation'


def test_rotation_spacing_and_oldest(setup):
    for repo in eb.repos():
        for kind in eb.KINDS:
            setup.state['last'][f'rotation:{repo}:{kind}'] = (NOW - dt.timedelta(days=6)).isoformat()
    assert eb.choose(setup.state, NOW) is None
    setup.state['last']['rotation:repo0:security'] = (NOW - dt.timedelta(days=7)).isoformat()
    setup.state['last']['rotation:repo1:tests'] = (NOW - dt.timedelta(days=8)).isoformat()
    assert eb.choose(setup.state, NOW)['key'] == 'rotation:repo1:tests'
    setup.state['last']['rotation:repo1:tests'] = NOW.isoformat()
    assert eb.choose(setup.state, NOW)['key'] == 'rotation:repo0:security'


def test_dry_run_no_writes(setup, capsys):
    write_pace(setup)
    run(setup, dry=True)
    setup.mock.assert_not_called()
    assert not setup.root.exists()
    assert capsys.readouterr().out.count('would dispatch:') == 4


@pytest.mark.parametrize('elapsed,cap', [(42, 4), (160, 10)])
def test_dispatch_caps_and_no_repo_collisions(setup, elapsed, cap):
    write_pace(setup, elapsed=elapsed)
    fake_jobs(setup)
    run(setup)
    assert setup.mock.call_count == cap
    assert len({j['workdir'] for j in setup.state['running']}) == cap
    for call in setup.mock.call_args_list:
        argv = call.args[0]
        assert '--block' not in argv and '--allow-claude' not in argv
        assert argv[argv.index('--stakes') + 1] == 'important'
        assert argv[argv.index('--window') + 1] == '0'
        assert argv[argv.index('--sandbox') + 1] == 'read-only'
        assert 'FINDINGS: <n>' in call.kwargs['input']
    run(setup)
    assert setup.mock.call_count == cap


@pytest.mark.parametrize('fallback', [False, True])
def test_never_claude_even_if_routing_fails(setup, monkeypatch, fallback):
    config = eb.providers()
    setup.root.mkdir()
    file = setup.root / 'providers.json'
    file.write_text(json.dumps(config))
    if fallback:
        monkeypatch.setattr(runner.value, 'rank', Mock(side_effect=RuntimeError('ranking failed')))
    for role in config['roles']:
        args = SimpleNamespace(role=role, stakes='important', allow_claude=False, claude_only=False, domain=None)
        order = runner.provider_order(args, config, file, setup.pace)
        assert order and all(p['spec'].startswith('codex:') for p in order)


def test_completion_report_and_journal(setup):
    write_pace(setup)
    fake_jobs(setup)
    run(setup)
    job = setup.state['running'][0]
    directory = Path(job['jobdir'])
    (directory / 'exit').write_text('0')
    (directory / 'out.txt').write_text('High: src/a.py:12 has a bug.\nFINDINGS: 1\n')
    eb.complete(setup.root, setup.state, NOW)
    assert Path(job['report']).read_text().endswith('FINDINGS: 1\n')
    events = [json.loads(line) for line in (setup.root / 'runs.jsonl').read_text().splitlines()]
    assert [e['event'] for e in events] == ['dispatch'] * 4 + ['completion']
    assert events[-1]['findings'] == 1
    assert len(setup.state['running']) == 3


def test_cli_queue_controls(setup, capsys):
    assert eb.main(['add', 'Review code', '--workdir', str(setup.repos / 'repo0'), '--sandbox', 'workspace-write', '--label', 'my work']) == 0
    assert eb.main(['list']) == 0
    assert 'my work [review, workspace-write]' in capsys.readouterr().out
    for command in (['off'], ['target', '65'], ['on']):
        assert eb.main(command) == 0
    state = json.loads((setup.root / 'state.json').read_text())
    assert state['enabled'] and state['target'] == 65
    assert state['queue'][0]['sandbox'] == 'workspace-write'


def test_failed_launch_does_not_track_old_job(setup):
    write_pace(setup)
    old = setup.root / 'jobs/old'
    old.mkdir(parents=True)
    setup.mock.side_effect = None
    setup.mock.return_value = SimpleNamespace(returncode=2, stdout='', stderr='cannot start')
    with pytest.raises(OSError, match='did not create a job'):
        run(setup)
    assert setup.state['running'] == []
    assert setup.state['last'] == {}


def test_fast_completion_and_failure_saved(setup):
    write_pace(setup)
    def dispatch(argv, **kw):
        directory = Path(kw['env']['G_AGENT_JOBS']) / str(setup.mock.call_count)
        directory.mkdir(parents=True)
        (directory / 'exit').write_text('3')
        return SimpleNamespace(returncode=3, stdout='job failed')
    setup.mock.side_effect = dispatch
    run(setup)
    eb.complete(setup.root, setup.state, NOW)
    assert not setup.state['running']
    events = [json.loads(line) for line in (setup.root / 'runs.jsonl').read_text().splitlines()]
    assert len(events) == 8
    assert events[-1]['exit'] == 3 and events[-1]['findings'] is None


def test_status_shows_reports_and_running(setup, capsys):
    write_pace(setup)
    fake_jobs(setup)
    run(setup)
    report = setup.root / 'reports/report.md'
    report.parent.mkdir()
    report.write_text('FINDINGS: 2\n')
    eb.run(SimpleNamespace(command='status'), setup.root, setup.state, NOW)
    output = capsys.readouterr().out
    assert 'report.md: FINDINGS: 2' in output and 'running: eburn' in output


def test_rotation_only_managed_existing_repos(setup):
    manifest = eb.path('MANIFEST', '')
    manifest.write_text(json.dumps({'projects': [
        {'id': 'repo0', 'managed': False}, {'id': 'missing', 'managed': True},
        {'id': 'renamed', 'url': 'https://example.test/owner/repo1', 'managed': True}]}))
    assert eb.repos() == {'renamed': setup.repos / 'repo1'}


def test_report_name_preserves_earlier_ticket_revision(setup):
    write_pace(setup)
    create_ticket(setup)
    old = setup.root / 'reports/2026-10-13-repo0-ticket-1.md'
    old.parent.mkdir(parents=True)
    old.write_text('earlier report\nFINDINGS: 1\n')
    fake_jobs(setup)
    run(setup)
    job = setup.state['running'][0]
    assert job['report'].endswith('repo0-ticket-1-2.md')
    assert old.read_text() == 'earlier report\nFINDINGS: 1\n'
