import contextlib
import datetime as dt
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_loader('g_reset', SourceFileLoader('g_reset', str(REPO / 'bin/g-reset')))
reset = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reset)
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 13, 12, tzinfo=UTC)


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = self.home / '.cache/genv/compute-resets.json'
        patch = mock.patch.object(reset.Path, 'home', return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.dict('os.environ', {'HOME': str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(reset.main(args), 0)
        return out.getvalue()

    def test_dates_use_london_and_datetimes_preserve_offsets(self):
        self.assertEqual(reset.when('2026-10-13').isoformat(), '2026-10-12T23:00:00+00:00')
        self.assertEqual(reset.when('2026-10-31').isoformat(), '2026-10-31T00:00:00+00:00')
        self.assertEqual(reset.when('2026-10-13T06:00').hour, 5)
        self.assertEqual(reset.when('2026-10-13T06:00+02:00').hour, 4)
        self.assertEqual(reset.when('2026-10-13T06:00Z').hour, 6)

    def test_add_list_remove_and_atomic_write(self):
        self.assertEqual(self.run_cli([]), '')
        output = self.run_cli(['add', 'codex', '2099-10-31', '--note', 'subscription ends'])
        entries = json.loads(self.path.read_text())
        point = entries[0]
        self.assertIn(point['id'], output)
        self.assertEqual(len(point['id']), 8)
        self.assertEqual(point['kind'], 'point')
        self.assertEqual(point['note'], 'subscription ends')
        self.assertEqual(point['at'], '2099-10-31T00:00:00+00:00')
        self.assertIsNotNone(dt.datetime.fromisoformat(point['added_at']).tzinfo)
        self.assertFalse(self.path.with_suffix('.tmp').exists())
        self.run_cli(['add', 'claude', '--from', '2099-10-05', '--to', '2099-11-02'])
        window = json.loads(self.path.read_text())[1]
        self.assertEqual(window['kind'], 'window')
        self.assertIn('2099-10-04T23:00:00+00:00 to 2099-11-02T00:00:00+00:00', self.run_cli([]))
        self.run_cli(['rm', point['id']])
        self.assertEqual(json.loads(self.path.read_text()), [window])

    def test_prunes_points_at_seven_days_but_keeps_recent_history(self):
        entries = [{'id': str(n), 'kind': 'point', 'at': (NOW - dt.timedelta(days=n)).isoformat()}
                   for n in (0, 1, 6, 7, 8)]
        reset.save(entries, self.path)
        kept = reset.load(self.path, NOW)
        self.assertEqual([e['id'] for e in kept], ['0', '1', '6'])
        self.assertEqual(json.loads(self.path.read_text()), kept)

    def test_rejects_bad_or_conflicting_arguments(self):
        for args in (['add', 'other', '2099-10-31'], ['add', 'codex', 'bad'],
                     ['add', 'codex'], ['add', 'codex', '--from', '2099-10-05'],
                     ['add', 'codex', '--from', '2099-11-02', '--to', '2099-10-05'],
                     ['add', 'codex', '2099-10-31', '--to', '2099-11-02'], ['rm', 'missing']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                reset.main(args)
            self.assertEqual(exc.exception.code, 2)
        self.assertFalse(self.path.exists())

    def test_relays_to_h_with_shell_safe_arguments_and_no_local_write(self):
        token = self.home / '.config/genv/compute-token'
        token.parent.mkdir(parents=True)
        token.touch()
        args = ['add', 'codex', '2099-10-31', '--note', "subscription ends; $(false) 'quoted'"]
        with mock.patch.object(reset.subprocess, 'call', return_value=7) as call:
            self.assertEqual(reset.main(args), 7)
        command = call.call_args.args[0]
        self.assertEqual(command[:3], ['ssh', 'h', 'g-reset'])
        self.assertEqual(shlex.split(' '.join(command[3:])), args)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
