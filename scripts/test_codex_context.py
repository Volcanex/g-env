import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('context', REPO / 'context/session-context.py')
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)
spec = importlib.util.spec_from_file_location('instance', REPO / 'context/instance.py')
instance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(instance)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = self.home / '.agents'
        self.root.mkdir()
        (self.root / 'host.md').write_text('host context')
        sources = {name: str(self.root / (name + '.md')) for name in ('shared', 'conventions', 'topology')}
        for name, path in sources.items():
            Path(path).write_text(name + ' source')
        (self.root / 'context.json').write_text(json.dumps({'sources': sources}))
        self.addCleanup(self.tmp.cleanup)

    def test_live_imports_relative_paths_cycles_and_code_fences(self):
        source, child = self.root / 'host.md', self.root / 'child.md'
        source.write_text('@child.md\n@child.md\n```\n@example.md\n```\nhost context\n')
        child.write_text('shared context\n@host.md\n')
        output = context.expand(source)
        self.assertEqual(output.count('shared context'), 1)
        self.assertIn('@example.md', output)
        self.assertNotIn('Context unavailable', output)
        child.write_text('new decision\n')
        self.assertIn('new decision', context.expand(source))

    def test_missing_import_is_reported_and_host_context_survives(self):
        source = self.root / 'host.md'
        source.write_text('@missing.md\nhost context\n')
        output = context.context(self.home, '/project', pulse=False)
        self.assertIn('Context unavailable:', output)
        self.assertIn('host context', output)

    def test_no_claude_installation_needed(self):
        self.assertFalse((self.home / '.claude').exists())
        output = context.context(self.home, '/project', pulse=False)
        for value in ('host context', 'shared source', 'conventions source', 'topology source'):
            self.assertIn(value, output)

    def test_brain_receives_encoded_project_and_task(self):
        paths = []
        def fake_fetch(path):
            paths.append(path)
            return '- urgent: fresh pulse' if 'pulse' in path else 'orientation'
        with patch.object(context, 'fetch', side_effect=fake_fetch):
            output = context.context(self.home, '/project with spaces/#&', project='widget', task='auth & login')
        query = parse_qs(urlparse(next(p for p in paths if 'orient' in p)).query)
        self.assertEqual(query, {'cwd': ['/project with spaces/#&'], 'project': ['widget'], 'task': ['auth & login']})
        self.assertIn('fresh pulse', output)
        self.assertIn('orientation', output)

    def test_session_blocks_are_the_excerpt_and_unmarked_sources_render_whole(self):
        (self.root / 'host.md').write_text('# Host\n\nreference before\n<!-- SESSION:START -->\n'
                                           'Host identity: h\n<!-- SESSION:END -->\nreference after\n')
        big = '## Rules\n\n' + '\n\n'.join(f'Rule {n} stays whole.' for n in range(800))
        (self.root / 'conventions.md').write_text(big)
        output = context.context(self.home, '/project', pulse=False)
        self.assertIn('## Host (' + str(self.root / 'host.md') + '; rest on demand)\nHost identity: h', output)
        self.assertNotIn('reference before', output)
        self.assertNotIn('reference after', output)
        self.assertIn('Rule 0 stays whole.', output)
        self.assertIn('Rule 799 stays whole.', output)
        self.assertIn('### Rules', output)  # source headings nest under the section label
        self.assertNotIn('truncated', output.lower())

    def test_rendered_context_stays_under_target(self):
        """Soft guard on the real sources: trim them when this fails."""
        gcore = instance.instance(repo=REPO)
        if not gcore or not (gcore / 'conventions.md').exists():
            self.skipTest('no instance beside g-env; set G_INSTANCE')
        (self.root / 'host.md').write_text('# Host\n\n<!-- SESSION:START -->\n' + 'A typical host fact line of about eighty characters, kept short on purpose.\n' * 8
                                           + '<!-- SESSION:END -->\n' + 'Project reference detail.\n' * 200)
        sources = {'shared': str(REPO / 'context/shared.md'), 'conventions': str(gcore / 'conventions.md'),
                   'topology': str(gcore / 'CLAUDE.md')}
        (self.root / 'context.json').write_text(json.dumps({'sources': sources}))
        pulse = '<!-- pulse page -->\n\n' + ''.join(f'- routine entry {n} ' + 'x' * 200 + '\n' for n in range(30))
        pulse += '- Deploy is blocked and URGENT: fix the FAQ regression before anything else, ' + 'y' * 150 + '\n'
        orientation = ('## Brain (long-term corpus)\n1404 notes: design 179, finding 935\n\n'
                       '6 note(s) flagged broken: `a`, `b`\n\nHousekeeping: 87 condensation candidate(s), '
                       '**24 possibly outdated**, 55 overlapping pairs (run `/brain-condense`).\n\n'
                       '**On `genv`** (22 notes, relevance first):\n'
                       + ''.join(f'- `findings/a-fairly-long-note-slug-number-{n}-about-genv-wiring`: Title, lead\n' for n in range(9))
                       + '… 13 relevant note(s) omitted.\n\n**Writing is part of the work.** manual')
        with patch.object(context, 'fetch', side_effect=lambda path: pulse if 'pulse' in path else 'Unavailable: down' if 'status' in path else orientation):
            output = context.context(self.home, '/home/gabriel/genv')
        self.assertLessEqual(len(output), context.TARGET_CHARS, f'context is {len(output)} chars')
        self.assertNotIn('Context unavailable', output)
        self.assertNotIn('truncated', output.lower())
        for rule in ('PRs only when asked', 'commits and pushes before it finishes', 'Never skip hooks',
                     'numbered threads', "supersede and edit_major need the operator's assent",
                     'Build no cross-agent handover or automatic task resumption', 'Staff from the Compute pace data',
                     'Trade at most ~5% quality', 'Never expose secrets', '**h**', 'URGENT'):
            self.assertIn(rule, output)

    def test_pulse_keeps_priority_entries_only(self):
        page = '<!-- comment mentions urgent -->\n- routine one\n- P0 outage: broken thing\n- routine two\n'
        text = context.pulse_section(page)
        self.assertIn('- P0 outage: broken thing', text)
        self.assertNotIn('routine', text)
        self.assertIn('3 entries in all', text)
        many = ''.join(f'- urgent {n}\n' for n in range(8))
        text = context.pulse_section(many)
        self.assertIn('- urgent 4', text)
        self.assertNotIn('- urgent 5', text)
        self.assertIn('+3 more priority entries', text)
        self.assertIn('none flagged', context.pulse_section('- calm\n'))
        self.assertIn('Unavailable', context.pulse_section('Unavailable: down'))

    def test_health_names_only_what_is_broken(self):
        now = context.dt.datetime(2026, 10, 13, 12, 0, tzinfo=context.dt.timezone.utc)
        def board(minutes_ago, *rows):
            checked = (now - context.dt.timedelta(minutes=minutes_ago)).isoformat()
            return json.dumps({'checked_at': checked, 'services': [dict(r) for r in rows], 'dependencies': []})
        self.assertEqual(context.health_section(board(3, {'id': 'a', 'state': 'up', 'detail': 'HTTP 200'},
                                                      {'id': 'b', 'state': 'paused', 'detail': 'stopped'}), now), '')
        text = context.health_section(board(3, {'id': 'period-api', 'state': 'down', 'detail': 'HTTP 502'}), now)
        self.assertIn('Down: period-api (HTTP 502).', text)
        self.assertIn('stuck', context.health_section(board(40), now))
        self.assertEqual(context.health_section('Unavailable: down', now), '')
        self.assertIn('unreadable', context.health_section('<html>', now))

    def test_brain_is_one_line_plus_actionable_housekeeping(self):
        quiet = '## Brain\n12 notes: finding 12\n\nHousekeeping: 3 condensation candidate(s), **1 possibly outdated**, 2 drift.\n\n**Writing** manual'
        text = context.brain_section(quiet)
        self.assertIn('12 notes. Retrieve only', text)
        self.assertNotIn('Housekeeping', text)
        self.assertNotIn('manual\n', text)
        busy = ('## Brain\n900 notes: finding 900\n\n2 note(s) flagged broken: `x`\n\nHousekeeping: 4 note(s) stand on a '
                'dependency that moved; 80 condensation candidate(s), **24 possibly outdated**, 5 drift.\n\n'
                '**On `genv`** (7 notes, relevance first):\n- `a`: A\n- `b` [BROKEN]: B\n- `c`: C\n- `d`: D\n- `e`: E\n')
        text = context.brain_section(busy)
        self.assertIn('900 notes, 2 flagged broken.', text)
        self.assertIn('On `genv`: `a`, `b [BROKEN]`, `c`, `d` (+3 more: /api/orient)', text)
        self.assertIn('4 notes stand on a moved dependency', text)
        self.assertIn('80 condensation candidates, 24 possibly outdated', text)
        odd = 'A reshaped orientation the loader does not recognise.'
        self.assertIn(odd, context.brain_section(odd))

    def test_failing_section_degrades_to_one_line(self):
        with patch.object(context, 'models_section', side_effect=RuntimeError('boom')):
            output = context.context(self.home, '/project', pulse=False)
        self.assertIn('## Roster\nmodels unavailable (RuntimeError); read its source directly.', output)
        self.assertIn('host context', output)

    def test_fetch_timeout_fallback_and_failure_are_nonfatal(self):
        with patch.object(context.urllib.request, 'urlopen', side_effect=TimeoutError):
            self.assertIn('Unavailable:', context.fetch('/api/pulse/page'))
        with patch.object(context, 'fetch', return_value='Unavailable: endpoint down'):
            self.assertIn('host context', context.context(self.home, '/project'))

    def test_hook_wire_output_and_compatibility_entrypoint(self):
        for script in ('context/session-context.py', 'codex/session-context.py'):
            result = subprocess.run(['python3', str(REPO / script), '--hook', '--no-pulse'],
                                    input=json.dumps({'cwd': '/project'}), capture_output=True,
                                    text=True, check=True, env={**os.environ, 'HOME': str(self.home)})
            data = json.loads(result.stdout)['hookSpecificOutput']
            self.assertEqual(data['hookEventName'], 'SessionStart')
            self.assertIn('host context', data['additionalContext'])
            self.assertLessEqual(len(data['additionalContext']), 12000)
        hooks = json.loads((REPO / 'codex/hooks.json').read_text())
        self.assertEqual(hooks['hooks']['SessionStart'][0]['hooks'][0]['additionalContextLimit'], 12000)


if __name__ == '__main__':
    unittest.main()
