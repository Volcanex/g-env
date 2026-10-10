"""refresh-models schema, diff and guards. No model calls: candidates come from --candidate-file."""
import copy
import importlib.util
import datetime as dt
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_loader('refresh_models', SourceFileLoader('refresh_models', str(REPO / 'bin/refresh-models')))
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)
TODAY = dt.date(2026, 10, 11)
SEED = json.loads((REPO / 'context/models.json').read_text())


def good():
    data = copy.deepcopy(SEED)
    data['as_of'] = TODAY.isoformat()
    return data


class SchemaTest(unittest.TestCase):
    def test_seed_is_valid_for_its_own_date(self):
        self.assertEqual(rm.validate(SEED, dt.date.fromisoformat(SEED['as_of'])), [])

    def test_as_of_must_be_today(self):
        data = good()
        data['as_of'] = '2026-10-04'
        self.assertTrue(any('as_of' in e for e in rm.validate(data, TODAY)))

    def test_every_number_needs_a_url(self):
        data = good()
        del data['models'][0]['sources']['AA']
        data['models'][1]['sources']['cost'] = 'not a url'
        data['effort_ladder'][0]['source'] = ''
        data['swe_bench_pro']['source'] = ''
        errors = ' | '.join(rm.validate(data, TODAY))
        for needle in ('opus-5.5: score AA has no source URL', 'sonnet-5.5: cost', 'ladder sonnet-5.5-low: source URL', 'swe_bench_pro'):
            self.assertIn(needle, errors)

    def test_dominated_or_unordered_ladder_is_rejected(self):
        data = good()
        data['effort_ladder'].append({**data['effort_ladder'][0], 'model': 'sonnet-5.5', 'effort': 'xhigh', 'score': 52, 'cost_usd': 2.75, 'ttft_s': 36})
        errors = ' | '.join(rm.validate(data, TODAY))
        self.assertIn('dominated', errors)
        self.assertIn('not ordered', errors)

    def test_latency_keeps_sonnet_medium_on_the_frontier(self):
        cells = {(c['model'], c['effort']): c for c in SEED['effort_ladder']}
        self.assertIn(('sonnet-5.5', 'medium'), cells)
        self.assertNotIn(('sonnet-5.5', 'xhigh'), cells)

    def test_ladder_is_claude_only(self):
        data = good()
        data['effort_ladder'].insert(0, {**data['effort_ladder'][0], 'model': 'gpt-6-luna', 'effort': 'max', 'score': 38, 'cost_usd': 0.07, 'ttft_s': 111})
        self.assertTrue(any('Claude models only' in e for e in rm.validate(data, TODAY)))

    def test_normalise_rounds_source_jitter_away(self):
        data = good()
        for c, (cost, ttft) in zip(data['effort_ladder'], ((0.4201, 1.17), (0.55, 4.5), (0.59, 1.08))):
            c['cost_usd'], c['ttft_s'] = cost, ttft
        rm.normalise(data)
        self.assertEqual([(c['cost_usd'], c['ttft_s']) for c in data['effort_ladder'][:3]], [(0.42, 1), (0.55, 5), (0.59, 1)])
        self.assertFalse(rm.diff(SEED, {**data, 'as_of': SEED['as_of']})['material'])

    def test_ladder_line_fits_and_roster_must_exist(self):
        data = good()
        data['effort_ladder'][0]['model'] = 'ghost-1'
        self.assertTrue(any('not in roster' in e for e in rm.validate(data, TODAY)))


class DiffTest(unittest.TestCase):
    def test_as_of_only_is_not_material(self):
        d = rm.diff(SEED, good())
        self.assertFalse(d['material'])
        self.assertEqual(rm.summarise(d), 'no change')

    def test_score_move_and_model_counts(self):
        new = good()
        new['models'][0]['scores']['AA'] = 64
        new['models'][1]['scores']['AA'] = 57
        d = rm.diff(SEED, new)
        self.assertEqual(d['big_moves'], [('opus-5.5 AA', 58, 64)])
        self.assertTrue(d['needs_review'] and d['material'])
        self.assertEqual(d['changed'], ['opus-5.5', 'sonnet-5.5'])


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run_git(self.tmp, 'init', '-q', '--bare', 'remote.git')
        self.run_git(self.tmp, 'clone', '-q', 'remote.git', 'repo')
        self.repo = self.tmp / 'repo'
        self.run_git(self.repo, 'config', 'user.email', 't@t')
        self.run_git(self.repo, 'config', 'user.name', 't')
        (self.repo / 'context').mkdir()
        (self.repo / 'context/models.json').write_text(rm.dump(SEED))
        (self.repo / 'other.txt').write_text('x')
        self.run_git(self.repo, 'add', '.')
        self.run_git(self.repo, 'commit', '-qm', 'seed')
        self.run_git(self.repo, 'push', '-q', 'origin', 'HEAD')
        (self.repo / 'other.txt').write_text('someone else, uncommitted')  # must survive the job
        self.pages = []
        for target, value in (('notify', lambda level, msg: self.pages.append((level, msg))), ('CACHE', self.tmp / 'cache')):
            patcher = patch.object(rm, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_git(self, cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout

    def apply(self, candidate, *extra):
        cfile = self.tmp / 'cand.json'
        cfile.write_text(candidate if isinstance(candidate, str) else json.dumps(candidate))
        return rm.main(['--candidate-file', str(cfile), '--repo', str(self.repo), '--today', TODAY.isoformat(), *extra])

    def commits(self):
        return int(self.run_git(self.repo, 'rev-list', '--count', 'HEAD'))

    def on_disk(self):
        return json.loads((self.repo / 'context/models.json').read_text())

    def test_invalid_candidate_leaves_file_and_pages_why(self):
        bad = good()
        del bad['models'][0]['sources']['AA']
        self.assertEqual(self.apply(bad), 1)
        self.assertEqual(self.on_disk(), SEED)
        self.assertEqual(self.commits(), 1)
        self.assertEqual(self.pages[0][0], 'error')
        self.assertIn('no source URL', self.pages[0][1])

    def test_unparseable_candidate_is_a_failure_not_a_crash(self):
        self.assertEqual(self.apply('not json'), 1)
        self.assertEqual(self.pages[0][0], 'error')

    def test_unchanged_data_commits_freshness_without_a_page(self):
        self.assertEqual(self.apply(good()), 0)
        self.assertEqual(self.on_disk()['as_of'], TODAY.isoformat())
        self.assertEqual(self.commits(), 2)
        self.assertEqual(self.pages, [])
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD'), self.run_git(self.tmp / 'remote.git', 'rev-parse', 'HEAD'))
        self.assertEqual((self.repo / 'other.txt').read_text(), 'someone else, uncommitted')
        self.assertNotIn('other.txt', self.run_git(self.repo, 'show', '--stat', 'HEAD'))

    def test_small_ladder_change_commits_pushes_and_pages_one_line(self):
        new = good()
        new['effort_ladder'][4]['score'] = 52
        self.assertEqual(self.apply(new), 0)
        self.assertEqual(self.commits(), 2)
        self.assertEqual(len(self.pages), 1)
        self.assertEqual(self.pages[0][0], 'info')
        self.assertIn('ladder changed', self.pages[0][1])
        self.assertIn('ladder opus-medium 51->52', self.pages[0][1])
        self.assertNotIn('\n', self.pages[0][1])
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD'), self.run_git(self.tmp / 'remote.git', 'rev-parse', 'HEAD'))

    def test_model_removed_commits(self):
        new = good()
        new['models'] = [m for m in new['models'] if m['id'] != 'haiku-4.5']
        self.assertEqual(self.apply(new), 0)
        self.assertIn('removed haiku-4.5', self.pages[0][1])
        self.assertEqual(self.commits(), 2)

    def test_score_jump_of_five_is_held_for_review(self):
        new = good()
        new['models'][0]['scores']['AA'] = 63
        self.assertEqual(self.apply(new), 0)
        self.assertEqual(self.commits(), 1)
        self.assertEqual(self.on_disk(), SEED)
        self.assertEqual(json.loads((self.tmp / 'cache/models.candidate.json').read_text())['models'][0]['scores']['AA'], 63)
        self.assertEqual(self.pages[0][0], 'warn')
        self.assertIn('review', self.pages[0][1])

    def test_four_models_changed_is_held_for_review(self):
        new = good()
        for m in new['models'][:4]:
            m['role'] += ' (edited)'
        self.assertEqual(self.apply(new), 0)
        self.assertEqual(self.commits(), 1)
        self.assertTrue((self.tmp / 'cache/models.candidate.json').exists())
        self.assertIn('4 models changed', self.pages[0][1])

    def test_dry_run_writes_commits_and_pages_nothing(self):
        new = good()
        new['effort_ladder'][4]['score'] = 52
        new['models'][0]['scores']['AA'] = 70
        with patch('builtins.print') as shown:
            self.assertEqual(self.apply(new, '--dry-run'), 0)
        self.assertEqual((self.on_disk(), self.commits(), self.pages), (SEED, 1, []))
        self.assertFalse((self.tmp / 'cache').exists())
        self.assertIn('would write', ' '.join(str(c) for c in shown.call_args_list))

    def test_dry_run_prints_the_diff(self):
        new = good()
        new['effort_ladder'][4]['score'] = 52
        with patch('builtins.print') as shown:
            self.apply(new, '--dry-run')
        output = ' '.join(str(c) for c in shown.call_args_list)
        self.assertIn('models.json (candidate)', output)
        self.assertIn('+  "score": 52', output.replace('\\n', '\n').replace('+   ', '+  '))


if __name__ == '__main__':
    unittest.main()
