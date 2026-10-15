"""Value scores and routing, with fixture quotas and fake CLIs only."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import datetime as dt
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'context'))
import temperature
import value

NOW = dt.datetime(2026, 10, 5, 13, tzinfo=dt.timezone.utc)


def load(name, filename):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(REPO / filename)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('value_runner', 'bin/g-agent-run')
caller = load('value_caller', 'bin/g-agent-call')


def quota(provider, window='7d', remaining=60, burn=1, safe=1, low=False, now=NOW):
    return {'id': f'{provider} {window}', 'provider': provider, 'remaining_pct': remaining,
            'used_pct': 100 - remaining, 'window_hours': 5 if window == '5h' else 168,
            'resets_at': (now + dt.timedelta(hours=2 if window == '5h' else 120)).isoformat(),
            'burn_pct_per_work_hour': burn, 'safe_burn_pct_per_work_hour': safe, 'low_sample': low}


class ValueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.pace = self.home / 'pace.json'
        self.mode = self.home / 'mode.json'
        self.paths = {'home': self.home, 'now': NOW, 'pace_path': self.pace, 'mode_path': self.mode}

    def write(self, *quotas):
        self.pace.write_text(json.dumps({'computed_at': NOW.isoformat(), 'quotas': quotas}))

    def override(self, provider, temp=None, speed=None):
        temperature.set_override(provider, temp=temp, speed=speed, now=NOW, path=self.mode)

    def test_point_ids_specs_and_provider_normalisation(self):
        points = {i['id']: i for i in value.points()}
        self.assertEqual(set(points), {'opus-low', 'opus-medium', 'opus-high', 'opus-xhigh', 'opus-max',
                                       'sonnet-low', 'sonnet-medium', 'sonnet-high',
                                       'haiku-high', 'haiku-xhigh',
                                       'sol-low', 'sol-medium', 'sol-high', 'sol-xhigh',
                                       'luna-low', 'luna-medium', 'luna-high', 'luna-xhigh',
                                       'astra-low', 'astra-medium', 'astra-high', 'astra-xhigh'})
        self.assertEqual((points['opus-medium']['c'], points['sol-medium']['c']), (1, 1))
        self.assertEqual(points['opus-medium']['spec'], 'claude:opus')
        self.assertEqual(points['sonnet-high']['spec'], 'claude:sonnet')
        self.assertEqual(points['astra-high']['spec'], 'codex:gpt-6-astra')
        self.assertAlmostEqual(points['sol-high']['c'], 0.32 / 0.21)
        self.assertAlmostEqual(points['sonnet-high']['c'], 0.88 / 1.34)
        self.assertEqual(points['luna-medium']['ttft_s'], 8)

    def test_models_path_controls_points_and_references(self):
        data = json.loads((REPO / 'context/models.json').read_text())
        next(i for i in data['codex_ladder'] if i['effort'] == 'medium')['cost_usd'] = 0.42
        path = self.home / 'models.json'
        path.write_text(json.dumps(data))
        points = {i['id']: i for i in value.points(path)}
        self.assertAlmostEqual(points['sol-high']['c'], 0.32 / 0.42)

    def test_claude_reserve_rescales_affordable_burn(self):
        self.write(quota('claude', remaining=65, burn=2, safe=3))
        p = value.prices(**self.paths)['claude']
        self.assertAlmostEqual(p['r'], 2 / (3 * 50 / 60))
        self.assertAlmostEqual(p['lambda'], 9 * 0.8 ** 2)
        self.assertEqual((p['reserve'], p['source']), (15, 'claude 7d'))

    def test_codex_reserve_spends_to_its_own_reset(self):
        self.write(quota('codex', remaining=45, burn=2, safe=4))
        p = value.prices(**self.paths)['codex']
        self.assertAlmostEqual(p['r'], 2 / (4 * 45 / 40))
        self.assertEqual(p['reserve'], 0)

    def test_reserve_barrier_applies_even_with_low_sample(self):
        for remaining in (15, 14, 0):
            with self.subTest(remaining=remaining):
                self.write(quota('claude', remaining=remaining, burn=None, low=True))
                p = value.prices(**self.paths)['claude']
                self.assertEqual((p['r'], p['lambda']), (10, 900))
        self.write(quota('codex', remaining=0))
        self.assertEqual(value.prices(**self.paths)['codex']['r'], 10)

    def test_tightest_5h_or_7d_price_wins(self):
        self.write(quota('claude', '5h', burn=4), quota('claude', burn=1),
                   quota('claude', '30d', burn=1000))
        p = value.prices(**self.paths)['claude']
        self.assertEqual(p['source'], 'claude 5h')
        self.assertAlmostEqual(p['r'], 4 * 55 / 45)

    def test_low_sample_and_missing_burn_use_measured_ratio(self):
        for burn, low in ((None, False), (2, True)):
            with self.subTest(burn=burn, low=low):
                self.write(quota('claude', burn=burn, low=low))
                m = temperature.measured('claude', now=NOW, path=self.pace)
                self.assertAlmostEqual(value.prices(**self.paths)['claude']['r'], 1 / m[1])

    def test_zero_burn_buys_speed(self):
        self.write(quota('codex', burn=0))
        p = value.prices(**self.paths)['codex']
        self.assertEqual((p['r'], p['lambda'], p['mu']), (0, 0, 2))

    def test_missing_or_corrupt_data_is_neutral(self):
        for body in (None, '{broken', '[]', '{}'):
            with self.subTest(body=body):
                if body is not None:
                    self.pace.write_text(body)
                current = value.prices(**self.paths)
                self.assertEqual([p['r'] for p in current.values()], [1, 1])
                self.assertEqual([p['source'] for p in current.values()], ['default', 'default'])

    def test_unsupported_windows_do_not_set_a_price(self):
        self.write(quota('codex', '30d', remaining=0))
        self.assertEqual(value.prices(**self.paths)['codex']['r'], 1)

    def test_unsupported_window_cannot_bind_low_sample_fallback(self):
        valid = quota('codex', burn=None, low=True)
        self.write(valid)
        expected = value.prices(**self.paths)['codex']['r']
        self.write(valid, quota('codex', '30d', burn=1000))
        self.assertAlmostEqual(value.prices(**self.paths)['codex']['r'], expected)

    def test_overrides_fix_all_temperature_ratios(self):
        self.write(quota('claude', remaining=10), quota('codex', burn=100))
        for word, r in value.RATIOS.items():
            for provider in value.BASE:
                with self.subTest(word=word, provider=provider):
                    self.override(provider, temp=word)
                    p = value.prices(**self.paths)[provider]
                    self.assertEqual((p['r'], p['temp'], p['source']), (r, word, 'override'))
                    self.assertAlmostEqual(p['lambda'], value.BASE[provider] * r ** 2)

    def test_expired_override_stops_fixing_price(self):
        self.override('codex', temp='BURN')
        later = NOW + dt.timedelta(days=1)
        self.assertEqual(value.prices(**{**self.paths, 'now': later})['codex']['r'], 1)

    def test_fast_and_slow_change_price_and_speed(self):
        self.override('codex', temp='HOT')
        normal = value.prices(**self.paths)['codex']
        self.override('codex', temp='HOT', speed='FAST')
        fast = value.prices(**self.paths)['codex']
        self.override('codex', temp='HOT', speed='SLOW')
        slow = value.prices(**self.paths)['codex']
        self.assertEqual((normal['mu'], fast['mu'], slow['mu']), (1, 4, 0))
        self.assertEqual(fast['lambda'], normal['lambda'])
        self.assertAlmostEqual(slow['lambda'], normal['lambda'] * 0.6)

    def test_fast_and_slow_change_the_pick(self):
        self.override('claude', temp='COLD')
        self.override('codex', temp='BURN', speed='FAST')
        fast = value.pick('build', **self.paths)['pick']
        self.override('codex', temp='BURN', speed='SLOW')
        slow = value.pick('build', **self.paths)['pick']
        self.assertEqual((fast['id'], slow['id']), ('sol-medium', 'sol-xhigh'))

    def test_stakes_raise_quality_within_two_ranks(self):
        self.override('claude', temp='COLD')
        normal = value.pick('build', **self.paths)['pick']
        important = value.pick('build', stakes='important', **self.paths)['pick']
        self.assertEqual((normal['id'], important['id']), ('sol-high', 'sol-xhigh'))
        self.assertGreater(important['q'], normal['q'])

    def test_cap_blocks_an_unconstrained_critical_jump_to_max(self):
        points = [{'id': f'opus-{effort}', 'provider': 'claude', 'spec': 'claude:opus',
                   'effort': effort, 'q': 40 + 2 * index, 'c': 0.3 * index, 'ttft_s': 1}
                  for index, effort in enumerate(('low', 'medium', 'high', 'xhigh', 'max'))]
        with patch.object(value, 'points', return_value=points):
            normal = value.pick('explore', **self.paths)['pick']
            critical = value.pick('explore', stakes='critical', **self.paths)['pick']
            self.assertEqual((normal['id'], critical['id']), ('opus-low', 'opus-high'))
            unconstrained = max(points, key=lambda i: 2 * i['q'] - 9 * i['c'])
            self.assertEqual(unconstrained['id'], 'opus-max')
            self.assertNotIn('opus-max', [i['id'] for i in value.rank('explore', stakes='critical', **self.paths)])

    def test_every_role_and_stakes_obeys_quality_cap(self):
        self.override('claude', temp='BURN')
        self.override('codex', temp='COLD')
        config = json.loads((REPO / 'context/providers.json').read_text())['value']
        for role, rule in config.items():
            eligible = sorted([i for i in value.points() if i['id'].split('-')[0] in rule['families']
                               and i['q'] >= rule['floor']], key=lambda i: (i['q'], i['id']))
            ids = [i['id'] for i in eligible]
            normal = value.pick(role, **self.paths)['pick']['id']
            for stakes in value.STAKES:
                chosen = value.pick(role, stakes=stakes, **self.paths)['pick']['id']
                self.assertLessEqual(ids.index(chosen), ids.index(normal) + 2)

    def test_claude_gate_allow_and_critical_bypass(self):
        for word in ('COLD', 'FREEZING'):
            self.override('claude', temp=word)
            for stakes in ('routine', 'normal', 'important'):
                self.assertTrue(all(i['provider'] == 'codex' for i in value.rank('review', stakes, **self.paths)))
            allowed = value.rank('review', allow_claude=True, **self.paths)
            critical = value.rank('review', stakes='critical', **self.paths)
            self.assertTrue(any(i['provider'] == 'claude' for i in allowed))
            self.assertTrue(any(i['provider'] == 'claude' for i in critical))

    def test_measured_temperature_also_gates_claude(self):
        self.write(quota('claude', burn=3, safe=1))
        self.assertEqual(value.prices(**self.paths)['claude']['temp'], 'FREEZING')
        self.assertTrue(all(i['provider'] == 'codex' for i in value.rank('build', **self.paths)))

    def test_claude_only_ignores_gate(self):
        self.override('claude', temp='FREEZING')
        for role in ('gather', 'build', 'review'):
            ranked = value.rank(role, claude_only=True, **self.paths)
            self.assertTrue(ranked)
            self.assertTrue(all(i['provider'] == 'claude' for i in ranked))
            self.assertEqual(value.pick(role, claude_only=True, **self.paths)['pick']['provider'], 'claude')

    def test_role_families_and_floors_are_editable(self):
        config = json.loads((REPO / 'context/providers.json').read_text())
        config['value']['build'] = {'families': ['astra'], 'floor': 51}
        path = self.home / 'providers.json'
        path.write_text(json.dumps(config))
        ranked = value.rank('build', providers_path=path, **self.paths)
        self.assertTrue(ranked)
        self.assertTrue(all(i['id'].startswith('astra-') and i['q'] >= 51 for i in ranked))

    def test_rank_scores_and_pick_explanation(self):
        self.override('claude', temp='COLD')
        ranked = value.rank('build', stakes='important', **self.paths)
        current = value.prices(**self.paths)
        scores = []
        for point in ranked:
            p = current[point['provider']]
            score = 1.4 * point['q'] - p['lambda'] * point['c'] - p['mu'] * math.log10(max(point['ttft_s'], 1))
            self.assertAlmostEqual(point['score'], score)
            scores.append(score)
        self.assertEqual(scores, sorted(scores, reverse=True))
        result = value.pick('build', stakes='important', **self.paths)
        self.assertEqual(result['pick'], ranked[0])
        self.assertEqual(result['runners_up'], ranked[1:3])
        self.assertIn(ranked[0]['id'], result['why'])
        self.assertTrue(result['why'].endswith('.'))

    def test_snapshot_prices_grid_and_inputs_stay_consistent(self):
        self.write(quota('claude'), quota('codex'))
        before = self.pace.read_text()
        result = value.snapshot(**self.paths)
        self.assertEqual(result['computed_at'], NOW.isoformat())
        self.assertEqual(result['prices'], value.prices(**self.paths))
        self.assertEqual(len(result['grid']), 11)
        for row, grid in result['grid'].items():
            role, _, domain = row.partition(' ')
            for stakes, point_id in grid.items():
                self.assertEqual(point_id, value.pick(role, stakes, domain=domain or None, **self.paths)['pick']['id'])
        for i in result['points']:
            p = result['prices'][i['provider']]
            self.assertAlmostEqual(i['price_now'], p['lambda'] * i['c'] + p['mu'] * math.log10(i['ttft_s']))
        self.assertEqual(self.pace.read_text(), before)

    def test_explain_agrees_with_rank_and_says_why_points_sit_out(self):
        self.write(quota('claude'), quota('codex', remaining=25))
        self.override('claude', temp='COLD')
        for row in [(r, None) for r in value._config()['value']] + [('build', 'ui')]:
            for stakes in value.STAKES:
                result = value.explain(row[0], stakes, domain=row[1], **self.paths)
                ranked = value.rank(row[0], stakes, domain=row[1], **self.paths)
                self.assertEqual(result['ranked'], ranked)
                self.assertEqual(result['pick'], value.pick(row[0], stakes, domain=row[1], **self.paths)['pick']['id'])
                self.assertEqual({i['id'] for i in result['points'] if i['eligible']}, {i['id'] for i in ranked})
                self.assertTrue(all(i['reason'] for i in result['points'] if not i['eligible']))
        build = {i['id']: i for i in value.explain('build', **self.paths)['points']}
        self.assertEqual(build['luna-high']['reason'], 'Not a build model')
        self.assertEqual(build['sol-low']['reason'], 'Under the build quality floor of 46')
        self.assertIn('Claude is Cold', build['opus-medium']['reason'])
        critical = {i['id']: i for i in value.explain('build', 'critical', **self.paths)['points']}
        self.assertNotIn('Claude is', critical['opus-medium']['reason'] or '')
        ui = value.explain('build', 'routine', domain='ui', **self.paths)
        self.assertEqual((ui['stakes'], ui['taste_weight']), ('routine', 2))
        self.assertTrue(ui['pick'].startswith('opus'))
        for bad in [('nope', 'normal', None), ('build', 'huge', None), ('build', 'normal', 'nope')]:
            with self.assertRaises(ValueError):
                value.explain(bad[0], bad[1], domain=bad[2], **self.paths)

    def test_g_pick_cli_prints_line_json_and_grid(self):
        env = {**os.environ, 'HOME': str(self.home)}
        argv = [str(REPO / 'bin/g-pick'), '--role', 'build', '--stakes', 'important']
        result = subprocess.run(argv, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn('build, important:', result.stdout)
        self.assertIn('Codex price', result.stdout)
        result = subprocess.run(argv + ['--json', '--claude-only'], env=env, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual((data['stakes'], data['pick']['provider']), ('important', 'claude'))
        grid = subprocess.run([str(REPO / 'bin/g-pick'), '--grid'], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(len(grid.stdout.splitlines()), 12)
        self.assertIn('critical', grid.stdout)
        bad = subprocess.run([str(REPO / 'bin/g-pick'), '--role', 'unknown'], env=env, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)

    def test_codex_eburn_moves_non_taste_work_off_claude(self):
        self.write(quota('claude'), quota('codex'))
        self.override('codex', temp='EBURN')
        for role in value._config()['value']:
            rule = value._config()['value'][role]
            pick = value.pick(role, 'normal', **self.paths)['pick']
            if not rule.get('taste'):
                self.assertEqual(pick['provider'], 'codex', role)
        ui = value.pick('build', 'routine', domain='ui', **self.paths)['pick']['id']
        self.override('codex', temp='BURN')
        self.assertEqual(value.pick('build', 'routine', domain='ui', **self.paths)['pick']['id'], ui)
        self.override('codex', temp='EBURN')
        self.assertEqual(value.pick('build', 'critical', allow_claude=True, **self.paths)['prices']['codex']['temp'], 'EBURN')


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config = json.loads((REPO / 'context/providers.json').read_text())
        self.config['timeout_s']['build'] = 2
        self.config_path = self.home / 'providers.json'
        self.config_path.write_text(json.dumps(self.config))
        self.pace = self.home / 'pace.json'
        self.mode = self.home / 'mode.json'
        self.args = argparse.Namespace(role='build', stakes='important', allow_claude=True, claude_only=False)
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        (self.bin / 'cat').symlink_to('/bin/cat')
        self.prompt = self.home / 'prompt.txt'
        self.prompt.write_text('fixture brief')
        self.log = self.home / 'log.jsonl'
        self.meta = self.home / 'meta.json'
        self.env = {'PATH': str(self.bin), 'G_AGENT_PROVIDERS': str(self.config_path),
                    'G_AGENT_PACE': str(self.pace), 'G_AGENT_MODE': str(self.mode), 'G_AGENT_LOG': str(self.log)}
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake(self, provider, body):
        path = self.bin / provider
        path.write_text('#!/bin/sh\ncat >/dev/null\n' + body)
        path.chmod(0o755)

    def run_job(self, *flags):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.main(['--role', 'build', '--prompt-file', str(self.prompt), '--workdir', str(self.home),
                                '--meta', str(self.meta), *flags])

    def test_runner_orders_value_best_then_other_provider(self):
        ranked = value.rank('build', 'important', allow_claude=True, pace_path=self.pace, mode_path=self.mode)
        order = runner.provider_order(self.args, self.config, self.config_path, self.pace)
        self.assertEqual(order[0], ranked[0])
        self.assertEqual(order[1], next(i for i in ranked if i['provider'] != ranked[0]['provider']))
        self.assertEqual(len(order), 3)
        self.assertEqual(len({i['id'] for i in order}), 3)

    def test_eburn_retry_changes_model_not_lifted_effort(self):
        temperature.set_override('codex', temp='EBURN', path=self.mode)
        args = argparse.Namespace(role='gather', stakes='normal', allow_claude=False, claude_only=False)
        order = runner.provider_order(args, self.config, self.config_path, self.pace)
        runs = [(i['spec'], runner.lifted(i['spec'], i['effort'], self.pace)) for i in order]
        self.assertEqual(len(runs), len(set(runs)))
        self.assertNotEqual(order[1]['spec'], order[0]['spec'])

    def test_web_domain_never_ranks_luna(self):
        ranked = value.rank('gather', 'normal', domain='web', pace_path=self.pace, mode_path=self.mode)
        self.assertTrue(ranked)
        self.assertFalse([i for i in ranked if 'luna' in i['spec']])

    def test_missing_value_and_value_errors_use_legacy_roles_and_efforts(self):
        expected = [{'spec': i, 'effort': 'high'} for i in self.config['roles']['build']]
        self.config.pop('value')
        self.assertEqual(runner.provider_order(self.args, self.config, self.config_path, self.pace), expected)
        self.config['value'] = {}
        with patch.object(runner.value, 'rank', side_effect=ValueError('bad ladder')):
            self.assertEqual(runner.provider_order(self.args, self.config, self.config_path, self.pace), expected)
        self.config.pop('value')
        self.args.claude_only = True
        self.assertEqual(runner.provider_order(self.args, self.config, self.config_path, self.pace), [expected[1]])

    def test_value_efforts_and_point_ids_reach_cli_and_log(self):
        self.fake('codex', 'printf "%s\\n" "$@" > "$ARGV_OUT"\nexit 1\n')
        self.fake('claude', 'printf "%s\\n" "$@" > "$CLAUDE_ARGV"\necho \'{"result":"ok","usage":{}}\'\n')
        with patch.dict(os.environ, {'ARGV_OUT': str(self.home / 'codex.txt'), 'CLAUDE_ARGV': str(self.home / 'claude.txt')}):
            self.assertEqual(self.run_job('--stakes', 'important', '--allow-claude'), 0)
        attempts = json.loads(self.meta.read_text())['attempts']
        self.assertEqual([i['point'] for i in attempts], ['sol-xhigh', 'opus-medium'])
        self.assertEqual([i['effort'] for i in attempts], ['xhigh', 'medium'])
        self.assertEqual([i['provider'] for i in attempts], ['codex:gpt-6.1-sol', 'claude:opus'])
        self.assertIn('model_reasoning_effort=xhigh', (self.home / 'codex.txt').read_text())
        argv = (self.home / 'claude.txt').read_text().splitlines()
        self.assertEqual(argv[argv.index('--effort') + 1], 'medium')
        self.assertEqual([json.loads(i)['point'] for i in self.log.read_text().splitlines()], ['sol-xhigh', 'opus-medium'])

    def test_runner_attempts_at_most_three_points(self):
        self.fake('codex', 'exit 7\n')
        self.fake('claude', 'exit 7\n')
        self.assertEqual(self.run_job('--allow-claude'), 3)
        self.assertEqual(len(json.loads(self.meta.read_text())['attempts']), 3)

    def test_runner_keeps_quota_floor_for_value_points(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.pace.write_text(json.dumps({'computed_at': now.isoformat(), 'quotas': [quota('codex', remaining=1, now=now)]}))
        temperature.set_override('codex', temp='BURN', now=now, path=self.mode)
        self.fake('claude', 'echo \'{"result":"ok","usage":{}}\'\n')
        self.assertEqual(self.run_job('--allow-claude'), 0)
        first = json.loads(self.meta.read_text())['attempts'][0]
        self.assertEqual((first['point'], first['status'], first['reason']), ('sol-xhigh', 'skipped', 'quota-floor'))
        self.assertEqual(first['effort'], 'xhigh')

    def test_runner_critical_and_claude_only_bypass_attempt_gate(self):
        now = dt.datetime.now(dt.timezone.utc)
        temperature.set_override('claude', temp='COLD', now=now, path=self.mode)
        self.fake('codex', 'exit 7\n')
        self.fake('claude', 'echo \'{"result":"ok","usage":{}}\'\n')
        self.assertEqual(self.run_job(), 3)
        self.assertTrue(all(i['provider'].startswith('codex:') for i in json.loads(self.meta.read_text())['attempts']))
        self.assertEqual(self.run_job('--stakes', 'critical'), 0)
        self.assertEqual(self.run_job('--claude-only'), 0)
        self.assertTrue(all(i['provider'].startswith('claude:') for i in json.loads(self.meta.read_text())['attempts']))

    def test_caller_passes_stakes_and_claude_only(self):
        args = argparse.Namespace(role='build', stakes='critical', workdir=str(self.home), label='fixture',
                                  web=False, allow_claude=False, claude_only=True, sandbox=None)
        with patch.object(caller, 'JOBS', self.home / 'jobs'), patch.object(caller.sys, 'stdin', io.StringIO('do work')), \
                patch.object(caller.subprocess, 'Popen') as popen:
            caller.start(args)
        argv = shlex.split(popen.call_args.args[0][2])
        self.assertEqual(argv[argv.index('--stakes') + 1], 'critical')
        self.assertIn('--claude-only', argv)


if __name__ == '__main__':
    unittest.main()


class DomainTests(unittest.TestCase):
    def test_design_prefers_taste_through_price_not_a_rule(self):
        cfg = value._config()
        roles = cfg['value']
        points = value.points()
        def best(claude_temp, claude_r, stakes='normal'):
            current = {'claude': {'r': claude_r, 'lambda': 9 * claude_r ** 2, 'mu': 2 * max(0, 1 - claude_r), 'temp': claude_temp},
                       'codex': {'r': 1, 'lambda': 3.3, 'mu': 0, 'temp': 'COOL'}}
            return value._rank('design', stakes, False, False, points, current, roles)[0]['id']
        tastes = value.tastes(json.loads((value.HERE / 'models.json').read_text()))
        self.assertGreater(tastes['opus-5.5'], tastes['gpt-6-astra'])
        self.assertGreater(tastes['gpt-6-astra'], tastes['gpt-6.1-sol'])
        self.assertEqual(best('COLD', 1.5), 'opus-low')
        self.assertEqual(best('FREEZING', 2.5)[:5], 'astra')
        self.assertTrue(best('HOT', 0.5, 'critical') in ('opus-high', 'opus-xhigh'))
        self.assertTrue(value._rank('build', 'normal', False, False, points,
                        {'claude': {'r': 2.5, 'lambda': 56, 'mu': 0, 'temp': 'FREEZING'},
                         'codex': {'r': 0.3, 'lambda': 0.3, 'mu': 1.4, 'temp': 'BURN'}}, roles)[0]['id'].startswith('sol'))
        self.assertTrue(value.values_taste('design'))
        self.assertTrue(value.values_taste('build', 'ui'))
        self.assertFalse(value.values_taste('build'))

    def test_gather_prefers_luna_at_normal_pace(self):
        cfg = value._config()
        current = {'claude': {'r': 1.5, 'lambda': 9 * 2.25, 'mu': 0, 'temp': 'COLD'},
                   'codex': {'r': 1.0, 'lambda': 3.3, 'mu': 0, 'temp': 'COOL'}}
        ranked = value._rank('gather', 'normal', False, False, value.points(), current, cfg['value'])
        self.assertTrue(ranked[0]['id'].startswith('luna'))


class PreviewTests(unittest.TestCase):
    """Overrides preview a mode without saving it; flips name the smallest change that makes a rival win."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.pace = self.home / 'pace.json'
        self.mode = self.home / 'mode.json'
        self.paths = {'home': self.home, 'now': NOW, 'pace_path': self.pace, 'mode_path': self.mode}
        self.pace.write_text(json.dumps({'computed_at': NOW.isoformat(), 'quotas': [
            quota('claude', burn=2, safe=1), quota('codex', burn=1, safe=1)]}))

    def live(self, provider, temp=None, speed=None):
        temperature.set_override(provider, temp=temp, speed=speed, now=NOW, path=self.mode)

    def apply(self, flip, overrides=None):
        """The preview and stakes a flip describes, on top of the overrides it was searched from."""
        preview = {p: dict((overrides or {}).get(p) or {}) for p in ('claude', 'codex')}
        stakes = None
        for c in flip['changes']:
            if c['what'] == 'stakes':
                stakes = c['to']
            else:
                provider, key = c['what'].split()
                preview[provider][key] = None if c['to'] == 'NORMAL' else c['to']
        return stakes, preview

    def test_preview_matches_the_same_override_set_live_and_saves_nothing(self):
        cases = [{'claude': {'temp': 'HOT'}}, {'codex': {'temp': 'FREEZING', 'speed': 'SLOW'}},
                 {'claude': {'temp': 'BURN', 'speed': 'FAST'}, 'codex': {'temp': 'COLD'}}]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                if self.mode.exists():
                    self.mode.unlink()
                preview = {r: value.explain(r, 'important', overrides=overrides, **self.paths) for r in ('build', 'design')}
                grid = value.snapshot(overrides=overrides, **self.paths)['grid']
                self.assertFalse(self.mode.exists())
                for provider, o in overrides.items():
                    self.live(provider, **o)
                for role, result in preview.items():
                    saved = value.explain(role, 'important', **self.paths)
                    self.assertEqual(result['pick'], saved['pick'])
                    self.assertEqual([round(i['score'], 9) for i in result['points']],
                                     [round(i['score'], 9) for i in saved['points']])
                    self.assertEqual(result['pick'], value.pick(role, 'important', overrides=overrides,
                                                                home=self.home, now=NOW, pace_path=self.pace,
                                                                mode_path=self.home / 'none.json')['pick']['id'])
                self.assertEqual(grid, value.snapshot(**self.paths)['grid'])

    def test_preview_marks_its_source_and_can_clear_a_live_override(self):
        measured = value.prices(**self.paths)['claude']
        p = value.prices(overrides={'claude': {'temp': 'HOT'}}, **self.paths)['claude']
        self.assertEqual((p['temp'], p['r'], p['source']), ('HOT', value.RATIOS['HOT'], 'preview'))
        self.live('claude', temp='BURN', speed='FAST')
        self.assertEqual(value.prices(**self.paths)['claude']['speed'], 'FAST')
        cleared = value.prices(overrides={'claude': {'temp': None, 'speed': 'NORMAL'}}, **self.paths)['claude']
        self.assertEqual(cleared, {**measured, 'speed': 'NORMAL'})
        for bad in [{'claude': {'temp': 'TEPID'}}, {'codex': {'speed': 'LUDICROUS'}}, {'jev': {'temp': 'HOT'}}]:
            with self.assertRaises(ValueError):
                value.prices(overrides=bad, **self.paths)

    def test_parts_add_up_to_the_score_and_rivals_surround_the_pick(self):
        for role in ('build', 'design', 'gather', 'review'):
            for overrides in (None, {'claude': {'temp': 'HOT'}}):
                with self.subTest(role=role, overrides=overrides):
                    result = value.explain(role, overrides=overrides, **self.paths)
                    for i in result['points']:
                        parts = i['parts']
                        self.assertAlmostEqual(parts['quality'] + parts['taste'] - parts['price'] - parts['speed'], i['score'])
                    rivals = result['rivals']
                    self.assertNotIn(result['pick'], rivals)
                    self.assertEqual(rivals[0], result['ranked'][1]['id'])
                    self.assertEqual(len(rivals), len(set(rivals)))
                    shown = {i['id']: i for i in result['points'] if i['id'] in rivals}
                    self.assertTrue(all(i['eligible'] or i['sidelined'] for i in shown.values()))
                    pick_provider = next(i['provider'] for i in result['points'] if i['id'] == result['pick'])
                    for provider in {i['provider'] for i in result['points'] if i['eligible'] or i['sidelined']} - {pick_provider}:
                        self.assertIn(provider, {i['provider'] for i in shown.values()})

    def test_flips_make_the_rival_win_and_nothing_smaller_does(self):
        for role, stakes, overrides in [('build', 'normal', None), ('design', 'normal', None),
                                        ('gather', 'routine', {'codex': {'temp': 'HOT'}}), ('review', 'important', None)]:
            base = value.explain(role, stakes, overrides=overrides, **self.paths)
            found = value.flips(role, stakes, overrides=overrides, **self.paths)
            self.assertEqual(list(found), base['rivals'])
            for rival, flip in found.items():
                with self.subTest(role=role, rival=rival):
                    if flip is None:
                        continue
                    self.assertEqual(flip['size'], len(flip['changes']))
                    self.assertTrue(flip['text'].startswith('Wins'))
                    new_stakes, preview = self.apply(flip, overrides)
                    self.assertEqual(value.explain(role, new_stakes or stakes, overrides=preview, **self.paths)['pick'], rival)
                    if flip['size'] == 2:
                        for dim in value.FLIP_DIMS:
                            levels = list(value.STAKES) if dim == 'stakes' else list(value.RATIOS) if 'temp' in dim else list(temperature.SPEEDS)
                            provider, _, key = dim.partition(' ')
                            here = base['stakes'] if dim == 'stakes' else base['prices'][provider][key]
                            for level in levels:
                                if level == here:  # flips only search changes; re-pinning the live label is not one (pins ratio 1.0, drops live speed cost)
                                    continue
                                one = {'changes': [{'what': dim, 'to': level}]}
                                s, p = self.apply(one, overrides)
                                self.assertNotEqual(value.explain(role, s or stakes, overrides=p, **self.paths)['pick'], rival)

    def test_flips_reads_named_ids_and_says_none_when_out_of_reach(self):
        found = value.flips('review', ids=['luna-low', 'sol-xhigh'], **self.paths)
        self.assertIsNone(found['luna-low'])
        self.assertEqual(set(found), {'luna-low', 'sol-xhigh'})
        hot = value.flips('build', 'normal', ids=['opus-high', 'opus-xhigh'], overrides={'claude': {'temp': 'HOT'}}, **self.paths)
        for flip in filter(None, hot.values()):
            self.assertTrue(all(c['from'] != c['to'] for c in flip['changes']))
            self.assertNotIn({'what': 'claude temp', 'from': 'HOT', 'to': 'HOT'}, flip['changes'])
        self.assertTrue(any(hot.values()))
