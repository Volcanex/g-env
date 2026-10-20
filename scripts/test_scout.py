"""Luna boost hooks and scout rounds, with no model calls or network."""
from contextlib import redirect_stdout
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load('luna_scout', REPO / 'claude/hooks/luna-scout.py')
scout = load('g_scout', REPO / 'bin/g-scout')


class HookTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.directory = self.home / '.cache/genv/scout/agent123'
        self.transcript = self.home / 'projects/session/subagents/agent-agent123.jsonl'
        self.transcript.parent.mkdir(parents=True)
        self.brief = 'Find a working implementation of this authentication API. ' * 10
        self.transcript.write_text(json.dumps({'message': {'content': self.brief}}) + '\n')
        self.event = {'session_id': 'session', 'transcript_path': str(self.home / 'projects/session.jsonl'),
                      'cwd': str(self.home), 'agent_id': 'agent123', 'agent_type': 'general-purpose'}
        self.now = 1000
        for patcher in (patch.object(hook.Path, 'home', return_value=self.home),
                        patch.dict(os.environ, {'G_SCOUT': '1', 'G_SCOUT_DEBUG': '0'}),
                        patch.object(hook.time, 'time', return_value=self.now)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def invoke(self, kind, **fields):
        event = {**self.event, 'hook_event_name': kind, **fields}
        output = io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO(json.dumps(event))), redirect_stdout(output):
            hook.main()
        return output.getvalue()

    def active(self, age=120, count=0, last=0, delivered=None):
        self.directory.mkdir(parents=True, exist_ok=True)
        hook.write_state(self.directory, {'started': self.now - age, 'count': count, 'last_injection': last,
                                         'delivered': delivered or [], 'history': [], 'commands': {}})

    def findings(self, *scores):
        rows = [{'url': f'https://example.org/{index}', 'what': f'official API {index}', 'why': 'exact fix',
                 'score': score, 'ts': self.now} for index, score in enumerate(scores)]
        (self.directory / 'findings.jsonl').write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        return rows

    def fail_tool(self, **fields):
        return self.invoke('PostToolUse', tool_name='Bash', tool_input={'command': 'python3 example.py'},
                           tool_response={'exit_code': 1, 'stderr': 'Error: unknown API'}, **fields)

    def test_start_reads_brief_and_detaches_scout(self):
        with patch.object(hook.subprocess, 'Popen') as spawn:
            self.assertEqual(self.invoke('SubagentStart'), '')
        self.assertEqual((self.directory / 'brief.txt').read_text(), self.brief)
        args, options = spawn.call_args
        self.assertEqual(args[0][0], str(REPO / 'bin/g-scout'))
        self.assertIn(str(self.transcript), args[0])
        self.assertTrue(options['start_new_session'])
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertEqual(hook.read_state(self.directory)['started'], self.now)

    def test_start_accepts_text_blocks_and_caps_brief(self):
        self.transcript.write_text(json.dumps({'message': {'content': [
            {'type': 'text', 'text': 'x' * 7000}, {'type': 'image', 'text': 'ignored'}]}}) + '\n')
        with patch.object(hook.subprocess, 'Popen'):
            self.invoke('SubagentStart')
        self.assertEqual((self.directory / 'brief.txt').read_text(), 'x' * 6000)

    def test_start_retries_a_missing_transcript(self):
        content = self.transcript.read_text()
        self.transcript.unlink()
        with patch.object(hook.time, 'sleep', side_effect=lambda _: self.transcript.write_text(content)) as sleep:
            with patch.object(hook.subprocess, 'Popen') as spawn:
                self.invoke('SubagentStart')
        sleep.assert_called_once_with(0.02)
        spawn.assert_called_once()

    def test_first_tool_call_starts_a_scout_whose_brief_came_late(self):
        content = self.transcript.read_text()
        self.transcript.unlink()
        with patch.object(hook.time, 'sleep'), patch.object(hook.subprocess, 'Popen') as spawn:
            self.invoke('SubagentStart')
            spawn.assert_not_called()
            self.transcript.write_text(content)
            self.fail_tool()
        spawn.assert_called_once()
        self.assertEqual((self.directory / 'brief.txt').read_text(), self.brief)
        self.assertFalse((self.directory / 'pending.json').exists())

    def test_resumed_subagent_is_active_again(self):
        with patch.object(hook.subprocess, 'Popen') as spawn:
            self.invoke('SubagentStart')
            hook.write_state(self.directory, {**hook.read_state(self.directory), 'count': 2})
            self.invoke('SubagentStop')
            self.invoke('SubagentStart')
            self.invoke('SubagentStart')
        self.assertFalse((self.directory / 'stop').exists())
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(hook.read_state(self.directory)['count'], 2)

    def test_start_skips_short_briefs(self):
        self.transcript.write_text(json.dumps({'message': {'content': 'x' * 299}}) + '\n')
        with patch.object(hook.subprocess, 'Popen') as spawn:
            self.invoke('SubagentStart')
        spawn.assert_not_called()
        self.assertFalse((self.directory / 'brief.txt').exists())

    def test_start_skips_guide_and_statusline(self):
        with patch.object(hook.subprocess, 'Popen') as spawn:
            for kind in hook.SKIP:
                self.invoke('SubagentStart', agent_type=kind)
        spawn.assert_not_called()

    def test_disabled_start_never_spawns(self):
        with patch.dict(os.environ, {'G_SCOUT': '0'}), patch.object(hook.subprocess, 'Popen') as spawn:
            self.invoke('SubagentStart')
        spawn.assert_not_called()

    def test_start_prunes_dirs_older_than_two_days(self):
        old, fresh = self.directory.parent / 'old', self.directory.parent / 'fresh'
        old.mkdir(parents=True)
        fresh.mkdir()
        os.utime(old, (self.now - 3 * 86400,) * 2)
        os.utime(fresh, (self.now,) * 2)
        with patch.object(hook.subprocess, 'Popen'):
            self.invoke('SubagentStart')
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_start_is_not_duplicated(self):
        with patch.object(hook.subprocess, 'Popen') as spawn:
            self.invoke('SubagentStart')
            self.invoke('SubagentStart')
        spawn.assert_called_once()

    def test_best_finding_is_one_pointer_and_counted(self):
        self.active()
        self.findings(4, 5, 3)
        result = json.loads(self.fail_tool())['hookSpecificOutput']
        self.assertEqual(result, {'hookEventName': 'PostToolUse',
                                 'additionalContext': 'Luna boost, web research worth 5/5: official API 1. '
                                                      'Why it matters: exact fix Source: https://example.org/1'})
        state = hook.read_state(self.directory)
        self.assertEqual((state['count'], state['delivered'], state['last_injection']),
                         (1, ['https://example.org/1'], self.now))
        boost = json.loads((self.home / '.cache/genv/boost.jsonl').read_text())
        self.assertEqual(boost, {'ts': self.now, 'agent_type': 'general-purpose', 'score': 5,
                                'url': 'https://example.org/1'})

    def test_budget_never_exceeds_six(self):
        self.active(count=6)
        self.findings(5)
        self.assertEqual(self.fail_tool(), '')
        self.assertEqual(hook.read_state(self.directory)['count'], 6)

    def test_sixth_injection_is_allowed(self):
        self.active(count=5, last=self.now - 120)
        self.findings(5)
        self.assertTrue(self.fail_tool())
        self.assertEqual(hook.read_state(self.directory)['count'], 6)

    def test_spacing_blocks_even_score_five(self):
        self.active(count=1, last=self.now - 119)
        self.findings(5)
        self.assertEqual(self.fail_tool(), '')

    def test_spacing_allows_at_two_minutes(self):
        self.active(count=1, last=self.now - 120)
        self.findings(4)
        self.assertTrue(self.fail_tool())

    def test_failed_tool_event_counts_as_failure_and_answers_in_kind(self):
        self.active()
        self.findings(4)
        output = self.invoke('PostToolUseFailure', tool_name='Bash', tool_input={'command': 'python3 example.py'},
                             error='Exit code 1\nModuleNotFoundError: No module named webauthn')
        self.assertEqual(json.loads(output)['hookSpecificOutput']['hookEventName'], 'PostToolUseFailure')
        self.assertEqual(hook.read_state(self.directory)['history'], [True])

    def test_first_minute_needs_a_strong_find(self):
        self.active(age=59)
        self.findings(3)
        self.assertEqual(self.fail_tool(), '')
        self.findings(4)
        self.assertTrue(self.fail_tool())

    def test_score_four_allowed_at_one_minute(self):
        self.active(age=60)
        self.findings(4)
        self.assertTrue(self.fail_tool())

    def test_score_three_needs_struggle_and_lower_scores_never_qualify(self):
        self.active()
        self.findings(2, 3, 4)
        state = hook.read_state(self.directory)
        self.assertEqual([r['score'] for r in hook.eligible_findings(self.directory, state, self.now, False)], [4])
        self.assertEqual([r['score'] for r in hook.eligible_findings(self.directory, state, self.now, True)], [4, 3])
        self.findings(3)
        self.assertTrue(self.fail_tool())

    def test_successful_edits_never_inject(self):
        self.active()
        self.findings(5)
        self.assertEqual(self.invoke('PostToolUse', tool_name='Edit', tool_response={'success': True}), '')
        self.assertEqual(hook.read_state(self.directory)['count'], 0)

    def test_successful_edit_of_error_text_is_not_a_failure(self):
        self.active()
        self.findings(5)
        response = {'filePath': 'example.py', 'newString': 'raise ValueError("Error: bad input")'}
        self.assertEqual(self.invoke('PostToolUse', tool_name='Edit', tool_response=response), '')
        self.assertFalse((self.directory / 'signals.jsonl').exists())

    def test_failed_bash_records_compact_signal(self):
        self.active()
        self.assertEqual(self.fail_tool(), '')
        signal = json.loads((self.directory / 'signals.jsonl').read_text())
        self.assertEqual((signal['tool'], signal['ts']), ('Bash', self.now))
        self.assertIn('Error:', signal['error'])
        self.assertLessEqual(len(signal['error']), 300)

    def test_error_markers_and_exit_code_variants(self):
        self.active()
        for response in ({'exitCode': 7}, {'returncode': 1}, 'Exit code: 2',
                         'Traceback (most recent call last):', 'Error: API missing',
                         'FAILED tests/test_api.py', 'python: command not found'):
            self.invoke('PostToolUse', tool_name='Bash', tool_response=response)
        signals = list(scout.records(self.directory / 'signals.jsonl'))
        self.assertEqual(len(signals), 7)

    def test_command_run_three_times_is_a_signal_without_injecting_on_success(self):
        self.active()
        self.findings(4)
        for _ in range(3):
            self.assertEqual(self.invoke('PostToolUse', tool_name='Bash', tool_input={'command': 'pytest -q'},
                                         tool_response={'exit_code': 0, 'stdout': 'ok'}), '')
        signal = json.loads((self.directory / 'signals.jsonl').read_text())
        self.assertIn('command repeated 3 times: pytest -q', signal['error'])

    def test_three_errors_in_last_eight_calls_keeps_struggle_signal(self):
        self.active()
        for _ in range(3):
            self.fail_tool()
        self.invoke('PostToolUse', tool_name='Read', tool_response='ok')
        signals = list(scout.records(self.directory / 'signals.jsonl'))
        self.assertEqual(signals[-1]['error'], '3 errors in last 8 tool calls')
        for _ in range(8):
            self.invoke('PostToolUse', tool_name='Read', tool_response='ok')
        self.assertEqual(hook.read_state(self.directory)['history'], [False] * 8)

    def test_top_find_lands_after_success_but_not_mid_edit(self):
        self.active()
        self.findings(5)
        self.assertEqual(self.invoke('PostToolUse', tool_name='Edit', tool_response='ok'), '')
        out = self.invoke('PostToolUse', tool_name='Read', tool_response='ok')
        self.assertIn('worth 5/5: official API 0.', out)

    def test_no_findings_means_no_output(self):
        self.active()
        self.assertEqual(self.fail_tool(), '')

    def test_delivered_urls_are_not_repeated(self):
        self.active(delivered=['https://example.org/0'])
        self.findings(5)
        self.assertEqual(self.fail_tool(), '')

    def test_pointer_caps_label_and_preserves_url(self):
        row = {'url': 'https://example.org/api', 'what': ('label\n' * 100), 'score': 4}
        line = hook.pointer(row)
        self.assertEqual(line, 'Luna boost, web research worth 4/5: ' + ('label ' * 14)[:80] + '. Source: https://example.org/api')
        self.assertEqual(hook.pointer({'url': 'https://example.org/' + 'x' * 300}), '')

    def test_pointer_delivers_the_content_on_one_line(self):
        row = {'url': 'https://example.org/api', 'what': 'API docs', 'score': 5, 'why': 'fixes the\nexact error',
               'content': 'Vertices.raw takes a Float32List of\npositions and ' + 'x' * 700}
        line = hook.pointer(row)
        self.assertTrue(line.startswith('Luna boost, web research worth 5/5: API docs. '
                                        'Vertices.raw takes a Float32List of positions and xxx'))
        self.assertIn(' Why it matters: fixes the exact error Source: https://example.org/api', line)
        self.assertNotIn('\n', line)
        self.assertLess(len(line), 1000)

    def test_malformed_findings_are_ignored(self):
        self.active()
        rows = ['not json', '[]', '{"url":"file:///tmp/x","score":5}',
                '{"url":"https://example.org/x","score":true}',
                '{"url":"https://example.org/x","score":6}']
        (self.directory / 'findings.jsonl').write_text('\n'.join(rows))
        self.assertEqual(self.fail_tool(), '')

    def test_stop_writes_stop_and_prevents_injections(self):
        self.active()
        self.findings(5)
        self.assertEqual(self.invoke('SubagentStop'), '')
        self.assertTrue((self.directory / 'stop').exists())
        self.assertEqual(self.fail_tool(), '')

    def test_disabled_stop_still_stops_existing_scout(self):
        self.active()
        with patch.dict(os.environ, {'G_SCOUT': '0'}):
            self.invoke('SubagentStop')
        self.assertTrue((self.directory / 'stop').exists())

    def test_debug_records_each_decision(self):
        self.active()
        with patch.dict(os.environ, {'G_SCOUT_DEBUG': '1'}):
            self.fail_tool()
            self.findings(5)
            self.fail_tool()
            self.fail_tool()
        decisions = list(scout.records(self.directory / 'hook.log'))
        self.assertEqual([row['reason'] for row in decisions],
                         ['no eligible findings', 'injected', 'injection spacing'])
        self.assertEqual(decisions[1]['eligible'], ['https://example.org/0'])
        for row in decisions:
            self.assertEqual((row['event'], row['agent_id']), ('PostToolUse', 'agent123'))
            self.assertIn('signal', row)

    def test_parent_event_and_bad_input_fail_quietly(self):
        self.assertEqual(self.invoke('PostToolUse', agent_id=''), '')
        self.assertEqual(self.invoke('SubagentStart', agent_id='../escape'), '')
        with patch.object(sys, 'stdin', io.StringIO('{')), redirect_stdout(io.StringIO()) as output:
            hook.main()
        self.assertEqual(output.getvalue(), '')

    def test_busy_state_does_not_block(self):
        self.active()
        with patch.object(hook.fcntl, 'flock', side_effect=BlockingIOError):
            self.assertEqual(self.fail_tool(), '')

    def test_shipped_hook_registration_preserves_format_hook(self):
        settings = json.loads((REPO / 'claude/settings.json').read_text())['hooks']
        for event in ('SubagentStart', 'PostToolUse', 'SubagentStop'):
            scouts = [entry for entry in settings[event] if any('luna-scout.py' in h['command'] for h in entry['hooks'])]
            self.assertEqual(len(scouts), 1)
            self.assertNotIn('matcher', scouts[0])
        self.assertTrue(any('format-on-save.py' in h['command'] for e in settings['PostToolUse'] for h in e['hooks']))


class ScoutTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.brief = self.directory / 'brief.txt'
        self.brief.write_text('Implement authentication with official APIs. ' * 10)
        self.fake = self.directory / 'fake-runner'

    def run_fake(self, body):
        self.fake.write_text('#!' + sys.executable + '\n' + body)
        self.fake.chmod(0o755)
        env = {**os.environ, 'G_SCOUT_RUNNER': str(self.fake), 'G_SCOUT_INTERVAL': '0.01'}
        return subprocess.run([sys.executable, str(REPO / 'bin/g-scout'), '--dir', str(self.directory),
                               '--brief-file', str(self.brief)], env=env, capture_output=True, text=True, timeout=10)

    def test_parser_drops_mixed_noise_non_urls_bad_scores_and_repeats(self):
        output = '\n'.join(['Here are some findings:', '```json', '[]', 'null', '{bad}',
                             '{"url":"file:///tmp/private","score":5}', '{"url":"https://","score":4}',
                             '{"url":"https://example.org/no-score"}', '{"url":"https://example.org/float","score":4.5}',
                             '{"url":"https://example.org/bool","score":true}', '{"url":"https://example.org/old","score":5}',
                             '{"url":"https://example.org/new","what":"API docs","why":"fix","score":4}',
                             '{"url":"https://example.org/new","score":5}', '```'])
        found = scout.parse_findings(output, {'https://example.org/old'})
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]['url'], found[0]['score']), ('https://example.org/new', 4))
        self.assertIn('ts', found[0])

    def test_parser_accepts_at_most_three_and_rejects_credentials(self):
        rows = [{'url': 'https://secret@example.org/private', 'score': 5}]
        rows += [{'url': f'https://example.org/{i}', 'score': 4} for i in range(5)]
        self.assertEqual(len(scout.parse_findings('\n'.join(map(json.dumps, rows)), set())), 3)

    def test_recent_tools_keeps_last_eight_and_caps_input(self):
        transcript = self.directory / 'transcript.jsonl'
        rows = [{'message': {'content': [{'type': 'tool_use', 'name': 'Bash',
                                          'input': {'command': str(i) + 'x' * 300}}]}} for i in range(12)]
        transcript.write_text('noise\n' + '\n'.join(map(json.dumps, rows)))
        recent = scout.recent_tools(str(transcript))
        self.assertEqual(len(recent), 8)
        self.assertIn('4xxxx', recent[0]['input'])
        self.assertTrue(all(len(row['input']) == 200 for row in recent))

    def test_stop_file_prevents_any_round(self):
        (self.directory / 'stop').touch()
        result = self.run_fake("raise RuntimeError('must not run')\n")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertFalse((self.directory / 'scout.log').exists())

    def test_relative_directory_reaches_runner_from_its_own_cache(self):
        directory = self.directory / 'cache'
        directory.mkdir()
        self.fake.write_text('#!' + sys.executable + '''
import json, pathlib, sys
prompt = pathlib.Path(sys.argv[sys.argv.index('--prompt-file') + 1])
assert prompt.exists()
assert pathlib.Path.cwd().name == 'cache'
print(json.dumps({'url':'https://example.org/api', 'score':5}))
''')
        self.fake.chmod(0o755)
        env = {**os.environ, 'G_SCOUT_RUNNER': str(self.fake), 'G_SCOUT_INTERVAL': '0.01'}
        result = subprocess.run([sys.executable, str(REPO / 'bin/g-scout'), '--dir', 'cache',
                                 '--brief-file', str(self.brief)], cwd=self.directory, env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(list(scout.records(directory / 'findings.jsonl'))), 1)

    def test_fake_rounds_store_findings_and_focus_on_new_signals(self):
        result = self.run_fake('''import json, pathlib, sys
root = pathlib.Path.cwd()
prompt = pathlib.Path(sys.argv[sys.argv.index('--prompt-file') + 1]).read_text()
context = json.loads(prompt.split('Private job context:\\n', 1)[1])
calls = root / 'calls.jsonl'
with calls.open('a') as out:
    out.write(json.dumps({'argv': sys.argv, 'context': context}) + '\\n')
count = len(calls.read_text().splitlines())
if count == 3:
    (root / 'stop').touch()
else:
    print('not JSON')
    print(json.dumps({'url':'https://example.org/api', 'what':'official API', 'why':'fix', 'score':5}))
    print(json.dumps({'url':'file:///tmp/x', 'score':5}))
    if count == 1:
        (root / 'signals.jsonl').write_text(json.dumps({'tool':'Bash', 'error':'Error: missing API', 'ts':1}) + '\\n')
''')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        found = list(scout.records(self.directory / 'findings.jsonl'))
        self.assertEqual([row['url'] for row in found], ['https://example.org/api'])
        calls = list(scout.records(self.directory / 'calls.jsonl'))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]['context']['struggle_signals'], [])
        self.assertEqual(calls[1]['context']['struggle_signals'][0]['error'], 'Error: missing API')
        self.assertEqual(calls[2]['context']['struggle_signals'], [])
        self.assertEqual(calls[1]['context']['already_found'][0]['url'], found[0]['url'])
        self.assertIn('--role', calls[0]['argv'])
        self.assertIn('boost', calls[0]['argv'])
        self.assertTrue(calls[0]['argv'][calls[0]['argv'].index('--label') + 1].startswith('Luna boost: '))
        self.assertIn('credentials', (self.directory / 'prompt.txt').read_text())

    def test_runner_error_is_quiet_and_stops_rounds(self):
        result = self.run_fake('raise SystemExit(7)\n')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        rounds = list(scout.records(self.directory / 'scout.log'))
        self.assertEqual([row['status'] for row in rounds], ['start', 'error'])

    def test_missing_binary_is_quiet(self):
        with patch.dict(os.environ, {'G_SCOUT_RUNNER': str(self.directory / 'absent')}):
            self.assertEqual(scout.main(['--dir', str(self.directory), '--brief-file', str(self.brief)]), 0)

    def test_stop_interrupts_inflight_round(self):
        self.fake.write_text('#!' + sys.executable + '''
import pathlib, time
pathlib.Path('running').touch()
time.sleep(10)
''')
        self.fake.chmod(0o755)
        env = {**os.environ, 'G_SCOUT_RUNNER': str(self.fake), 'G_SCOUT_INTERVAL': '0.01'}
        proc = subprocess.Popen([sys.executable, str(REPO / 'bin/g-scout'), '--dir', str(self.directory),
                                 '--brief-file', str(self.brief)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while not (self.directory / 'running').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.directory / 'running').exists())
            (self.directory / 'stop').touch()
            out, err = proc.communicate(timeout=3)
            self.assertEqual((proc.returncode, out, err), (0, b'', b''))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_unchanged_transcript_skips_rounds_until_the_agent_moves(self):
        transcript = self.directory / 'transcript.jsonl'
        tool = lambda cmd: json.dumps({'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': cmd}}]}})
        transcript.write_text(tool('ls') + '\n')
        self.fake.write_text('#!' + sys.executable + """
import pathlib, time
root = pathlib.Path.cwd()
calls = root / 'calls.txt'
calls.write_text(calls.read_text() + 'x' if calls.exists() else 'x')
if len(calls.read_text()) == 1:
    pathlib.Path(%r).open('a').write(%r + '\\n')
else:
    (root / 'stop').touch()
print('NONE')
""" % (str(transcript), tool('pytest')))
        self.fake.chmod(0o755)
        env = {**os.environ, 'G_SCOUT_RUNNER': str(self.fake), 'G_SCOUT_INTERVAL': '0.01'}
        result = subprocess.run([sys.executable, str(REPO / 'bin/g-scout'), '--dir', str(self.directory),
                                 '--brief-file', str(self.brief), '--transcript', str(transcript)],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.directory / 'calls.txt').read_text(), 'xx')

    def test_idle_agent_gets_no_rounds_after_the_first(self):
        transcript = self.directory / 'transcript.jsonl'
        transcript.write_text(json.dumps({'message': {'content': [{'type': 'tool_use', 'name': 'Read', 'input': {}}]}}) + '\n')
        self.fake.write_text('#!' + sys.executable + "\nimport pathlib\np = pathlib.Path('calls.txt')\np.write_text((p.read_text() if p.exists() else '') + 'x')\nprint('NONE')\n")
        self.fake.chmod(0o755)
        env = {**os.environ, 'G_SCOUT_RUNNER': str(self.fake), 'G_SCOUT_INTERVAL': '0.01'}
        proc = subprocess.Popen([sys.executable, str(REPO / 'bin/g-scout'), '--dir', str(self.directory),
                                 '--brief-file', str(self.brief), '--transcript', str(transcript)], env=env)
        time.sleep(1)
        (self.directory / 'stop').touch()
        proc.wait(timeout=3)
        self.assertEqual((self.directory / 'calls.txt').read_text(), 'x')

    def test_quiet_rounds_back_off(self):
        result = self.run_fake("print('NONE')\n")
        self.assertEqual(result.returncode, 0)
        rounds = [row['ts'] for row in scout.records(self.directory / 'scout.log') if row.get('status') == 'start']
        gaps = [b - a for a, b in zip(rounds, rounds[1:])]
        self.assertEqual(len(rounds), 40)
        self.assertGreater(min(gaps[5:]), 0.07)

    def test_new_signal_cuts_a_backed_off_wait_short(self):
        self.fake.write_text('#!' + sys.executable + """
import json, pathlib
p = pathlib.Path('calls.txt')
n = len(p.read_text()) + 1 if p.exists() else 1
p.write_text('x' * n)
if n == 1:
    print('NONE')
else:
    pathlib.Path('stop').touch()
""")
        self.fake.chmod(0o755)
        env = {**os.environ, 'G_SCOUT_RUNNER': str(self.fake), 'G_SCOUT_INTERVAL': '1'}
        proc = subprocess.Popen([sys.executable, str(REPO / 'bin/g-scout'), '--dir', str(self.directory),
                                 '--brief-file', str(self.brief)], env=env)
        time.sleep(1.3)
        (self.directory / 'signals.jsonl').write_text(json.dumps({'tool': 'Bash', 'error': 'Error: x', 'ts': 1}) + '\n')
        started = time.monotonic()
        proc.wait(timeout=5)
        self.assertLess(time.monotonic() - started, 1.2)
        self.assertEqual((self.directory / 'calls.txt').read_text(), 'xx')

    def test_label_uses_teammate_summary(self):
        self.assertEqual(scout.label('<teammate-message teammate_id="team-lead" summary="Finish Wing motion">\nDo it'),
                         'Luna boost: Finish Wing motion')
        self.assertEqual(scout.label('<teammate-message teammate_id="x">\n  Build the  page'), 'Luna boost: Build the page')
        self.assertEqual(scout.label('Plain brief'), 'Luna boost: Plain brief')

    def test_prompt_asks_for_none_when_nothing_found(self):
        self.run_fake("import pathlib; pathlib.Path('stop').touch()\n")
        self.assertIn('output exactly NONE', (self.directory / 'prompt.txt').read_text())

    def test_round_limit_is_forty(self):
        result = self.run_fake("print('{\"url\":\"https://example.org/api\",\"score\":4}')\n")
        self.assertEqual(result.returncode, 0)
        rounds = list(scout.records(self.directory / 'scout.log'))
        self.assertEqual(len([row for row in rounds if row.get('status') == 'start']), 40)

    def test_deadline_prevents_round_after_fifty_minutes(self):
        with patch.object(scout.time, 'monotonic', side_effect=[0, 3000]), patch.object(scout.subprocess, 'Popen') as spawn:
            self.assertEqual(scout.main(['--dir', str(self.directory), '--brief-file', str(self.brief)]), 0)
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
