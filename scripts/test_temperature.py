import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'context'))
import temperature  # noqa: E402

NOW = dt.datetime(2026, 10, 5, 13, 0, tzinfo=dt.timezone.utc)


def quota(qid, provider, burn, safe, low=False, used=30.0, reset_days=5):
    return {'id': qid, 'provider': provider, 'window_hours': 168.0, 'used_pct': used,
            'resets_at': (NOW + dt.timedelta(days=reset_days)).isoformat(),
            'burn_pct_per_work_hour': burn, 'safe_burn_pct_per_work_hour': safe, 'low_sample': low}


class TemperatureTests(unittest.TestCase):
    def write(self, quotas, age_h=0):
        path = Path(tempfile.mkdtemp()) / 'pace.json'
        path.write_text(json.dumps({'computed_at': (NOW - dt.timedelta(hours=age_h)).isoformat(), 'quotas': quotas}))
        return path

    def test_work_hour_pace_decides_over_wall_clock(self):
        path = self.write([quota('claude 7d', 'claude', 2.5, 1.0), quota('codex 7d', 'codex', 0.3, 0.9)])
        self.assertEqual(temperature.provider('claude', now=NOW, path=path, mode_path=path.parent / 'none.json')[0], 'FREEZING')
        self.assertEqual(temperature.provider('codex', now=NOW, path=path, mode_path=path.parent / 'none.json')[0], 'BURN')

    def test_codex_effort_follows_codex_temperature(self):
        hot = self.write([quota('codex 7d', 'codex', 0.3, 0.9)])
        cold = self.write([quota('codex 7d', 'codex', 2.0, 0.8)])
        self.assertEqual(temperature.codex_effort('medium', now=NOW, path=hot, mode_path=hot.parent / 'none.json'), 'high')
        self.assertEqual(temperature.codex_effort('high', now=NOW, path=hot, mode_path=hot.parent / 'none.json'), 'xhigh')
        self.assertEqual(temperature.codex_effort('xhigh', now=NOW, path=hot, mode_path=hot.parent / 'none.json'), 'xhigh')
        self.assertEqual(temperature.codex_effort('medium', now=NOW, path=cold, mode_path=cold.parent / 'none.json'), 'low')

    def test_stale_or_missing_data_is_neutral(self):
        stale = self.write([quota('codex 7d', 'codex', 0.3, 0.9)], age_h=4)
        self.assertIsNone(temperature.provider('codex', now=NOW, path=stale, mode_path=stale.parent / 'none.json'))
        self.assertEqual(temperature.codex_effort('medium', now=NOW, path=stale, mode_path=stale.parent / 'none.json'), 'medium')
        self.assertIsNone(temperature.provider('codex', now=NOW, path=Path('/nonexistent'), mode_path=Path('/nonexistent')))

    def test_override_wins_and_lapses_at_london_midnight(self):
        pace = self.write([quota('claude 7d', 'claude', 2.5, 1.0), quota('codex 7d', 'codex', 1.0, 1.0)])
        mode = pace.parent / 'mode.json'
        temperature.set_override('claude', temp='BURN', speed='FAST', now=NOW, path=mode)
        self.assertEqual(temperature.provider('claude', now=NOW, path=pace, mode_path=mode)[0], 'BURN')
        s = temperature.state('claude', now=NOW, path=pace, mode_path=mode)
        self.assertEqual((s['measured'], s['speed']), ('FREEZING', 'FAST'))
        later = NOW + dt.timedelta(hours=11, minutes=1)
        self.assertEqual(temperature.overrides(now=later, path=mode), {})
        temperature.set_override('claude', now=NOW, path=mode)
        self.assertEqual(temperature.overrides(now=NOW, path=mode), {})

    def test_claude_burn_needs_wall_clock_room_too(self):
        # Half way through the week: 70% left is x2.3 on the wall clock, 85% is x5.7.
        early = self.write([quota('claude 7d', 'claude', 0.3, 0.9, used=30, reset_days=3.5),
                            quota('codex 7d', 'codex', 0.3, 0.9, used=30, reset_days=3.5)])
        none = early.parent / 'none.json'
        self.assertEqual(temperature.provider('claude', now=NOW, path=early, mode_path=none)[0], 'HOT')
        self.assertEqual(temperature.provider('codex', now=NOW, path=early, mode_path=none)[0], 'BURN')
        roomy = self.write([quota('claude 7d', 'claude', 0.3, 0.9, used=15, reset_days=3.5)])
        self.assertEqual(temperature.provider('claude', now=NOW, path=roomy, mode_path=none)[0], 'BURN')
        late = self.write([quota('claude 7d', 'claude', 0.3, 0.9, used=70, reset_days=0.25)])
        self.assertEqual(temperature.provider('claude', now=NOW, path=late, mode_path=none)[0], 'BURN')
        mode = early.parent / 'mode.json'
        temperature.set_override('claude', temp='BURN', now=NOW, path=mode)
        self.assertEqual(temperature.provider('claude', now=NOW, path=early, mode_path=mode)[0], 'BURN')

    def test_effective_reset_drives_fallback_ratio_and_weekly_wall(self):
        q = quota('claude 7d', 'claude', None, None, low=True, used=70)
        original = temperature.ratio(q, NOW)
        q['effective_resets_at'] = (NOW + dt.timedelta(hours=6)).isoformat()
        expected = temperature.headroom(70, 6 * 3600, 168 * 3600)
        self.assertEqual(temperature.ratio(q, NOW), expected)
        self.assertEqual(temperature.wall([q], 'claude', NOW), expected)
        self.assertGreater(expected, original)
        q['burn_pct_per_work_hour'], q['safe_burn_pct_per_work_hour'], q['low_sample'] = 0.3, 0.9, False
        path = self.write([q])
        self.assertEqual(temperature.measured('claude', now=NOW, path=path)[0], 'BURN')
        q['effective_resets_at'] = None
        q['low_sample'] = True
        self.assertEqual(temperature.ratio(q, NOW), original)
        self.assertEqual(temperature.wall([q], 'claude', NOW), original)

    def test_override_for_hours(self):
        mode = Path(tempfile.mkdtemp()) / 'mode.json'
        temperature.set_override('claude', temp='COLD', now=NOW, path=mode, hours=3)
        self.assertEqual(temperature.overrides(now=NOW + dt.timedelta(hours=2, minutes=59), path=mode)['claude']['temp'], 'COLD')
        self.assertEqual(temperature.overrides(now=NOW + dt.timedelta(hours=3), path=mode), {})

    def test_speed_moves_codex_effort(self):
        pace = self.write([quota('codex 7d', 'codex', 1.0, 1.0)])
        mode = pace.parent / 'mode.json'
        temperature.set_override('codex', speed='SLOW', now=NOW, path=mode)
        self.assertEqual(temperature.codex_effort('medium', now=NOW, path=pace, mode_path=mode), 'high')
        temperature.set_override('codex', temp='BURN', speed='FAST', now=NOW, path=mode)
        self.assertEqual(temperature.codex_effort('high', now=NOW, path=pace, mode_path=mode), 'medium')

    def test_eburn_before_reset_or_above_codex_floor(self):
        none = Path(tempfile.mkdtemp()) / 'none.json'
        def row(provider, left, hours):
            q = quota(f'{provider} 7d', provider, 0.3, 0.9, used=100 - left)
            q['remaining_pct'] = left
            q['effective_resets_at'] = (NOW + dt.timedelta(hours=hours)).isoformat()
            return q
        self.assertEqual(temperature.provider('claude', now=NOW, path=self.write([row('claude', 40, 6)]), mode_path=none)[0], 'EBURN')
        self.assertNotEqual(temperature.provider('claude', now=NOW, path=self.write([row('claude', 8, 6)]), mode_path=none)[0], 'EBURN')
        self.assertNotEqual(temperature.provider('claude', now=NOW, path=self.write([row('claude', 40, 48)]), mode_path=none)[0], 'EBURN')
        for left, hours, word in ((80, 168, 'EBURN'), (70, 168, 'BURN'), (55, 84, 'EBURN'), (45, 84, 'BURN'),
                                  (30, 24, 'EBURN'), (20, 24, 'BURN')):
            got = temperature.provider('codex', now=NOW, path=self.write([row('codex', left, hours)]), mode_path=none)[0]
            self.assertEqual(got == 'EBURN', word == 'EBURN', (left, hours, got))
        self.assertEqual(temperature.codex_effort('medium', now=NOW, path=self.write([row('codex', 60, 48)]), mode_path=none), 'xhigh')


if __name__ == '__main__':
    unittest.main()
