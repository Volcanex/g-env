import datetime as dt
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "bin/g-cron"
SPEC = importlib.util.spec_from_loader("g_cron", SourceFileLoader("g_cron", str(SCRIPT)))
gc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gc)
UTC = dt.timezone.utc


def ts(*a):
    return dt.datetime(*a, tzinfo=UTC).timestamp()


class GCronTests(unittest.TestCase):
    def test_next_fire_basic_step_and_dom_dow(self):
        self.assertEqual(gc.next_fire("0 6 * * *", ts(2026, 10, 4, 6, 0), UTC), ts(2026, 10, 5, 6, 0))
        self.assertEqual(gc.next_fire("*/15 * * * *", ts(2026, 10, 4, 6, 1), UTC), ts(2026, 10, 4, 6, 15))
        # Sunday 2026-10-04; "30 8 * * 2" is the next Tuesday
        self.assertEqual(gc.next_fire("30 8 * * 2", ts(2026, 10, 4, 9, 0), UTC), ts(2026, 10, 6, 8, 30))
        # dom and dow both restricted: either matches
        self.assertEqual(gc.next_fire("0 0 15 * 1", ts(2026, 10, 4, 0, 0), UTC), ts(2026, 10, 5, 0, 0))
        self.assertIsNone(gc.next_fire("@reboot", 0, UTC))

    def test_pulse_jsonl_wins_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "crons").mkdir()
            (root / "state").mkdir()
            (root / "crons/_example.md").write_text("---\nname: x\n---\n")
            (root / "crons/a.job.md").write_text('---\nname: a.job\nschedule: "0 6 * * *"\nrunner: agent\nrole: review\nenabled: true\n---\nDo it.\n')
            (root / "crons/b.job.md").write_text('---\nname: b.job\nschedule: "5 * * * *"\nrunner: claude\nenabled: true # on\nmodel: claude-sonnet-5\n---\nBody.\n')
            (root / "state/cron-runs.json").write_text(json.dumps({"a.job": "2026-10-04T06:00:00+00:00", "b.job": "2026-10-04T09:05:00+00:00"}))
            (root / "state/cron.log").write_text("2026-10-04T09:05:00+00:00 name=b.job exit=1 duration=3s last_line='boom'\n")
            (root / "state/cron-runs.jsonl").write_text(json.dumps(
                {"name": "a.job", "started": "2026-10-04T06:00:00+00:00", "result": "noop", "provider": "claude", "summary": "nothing"}) + "\n")
            old = gc.PULSE
            gc.PULSE = root
            try:
                rows = {r["name"]: r for r in gc.collect_pulse(now=ts(2026, 10, 4, 10, 0))}
            finally:
                gc.PULSE = old
            self.assertEqual(set(rows), {"a.job", "b.job"})
            self.assertEqual(rows["a.job"]["result"], "noop")
            self.assertEqual(rows["a.job"]["model_label"], "review/claude")
            self.assertEqual(rows["b.job"]["result"], "fail")
            self.assertEqual(rows["b.job"]["summary"], "boom")
            self.assertTrue(rows["b.job"]["enabled"])

    def test_problems_flag_failed_overdue_noop_and_unreachable_but_skip_noise(self):
        now = ts(2026, 10, 4, 12, 0)
        base = {"host": "h", "enabled": True, "noise": False, "exit": None, "scope": "system"}
        jobs = [
            {**base, "kind": "timer", "name": "bad", "result": "exit-code", "exit": "1", "last": now - 60, "next": now + 60, "timer_state": "active"},
            {**base, "kind": "timer", "name": "stuck", "result": "success", "last": now - 86400, "next": now + 60,
             "interval_s": 900, "timer_state": "active"},
            {**base, "kind": "timer", "name": "apt-daily", "result": "failed", "noise": True, "last": now, "next": now},
            {**base, "kind": "pulse", "name": "p.noop", "result": "noop", "schedule": "0 * * * *", "last": now - 60, "next": now, "summary": "s"},
            {**base, "kind": "pulse", "name": "p.off", "enabled": False, "result": "fail", "schedule": "0 * * * *", "last": now - 99999, "next": None},
        ]
        got = {(p["name"], p["problem"].split()[0]) for p in gc.problems(
            jobs, [{"id": "b2", "status": "unreachable", "error": "timeout"}], now)}
        self.assertEqual(got, {("-", "unreachable"), ("bad", "failed"), ("stuck", "overdue"), ("p.noop", "last")})

    def test_cron_parsing_names_and_model_detection(self):
        text = ("MAILTO=x\n# c\n*/5 * * * * /usr/bin/flock -n /l /usr/bin/python3 /x/mirror.py --a # genv-mirror\n"
                "@reboot sleep 30 && /x/brain_gmap.py\n")
        lines = list(gc._cron_lines(text))
        self.assertEqual([s for s, _ in lines], ["*/5 * * * *", "@reboot"])
        self.assertEqual(gc._cron_name(lines[0][1]), "genv-mirror")
        self.assertEqual(gc._cron_name(lines[1][1]), "brain_gmap")
        self.assertEqual(gc.detect_model("run codex exec --x"), ["codex"])
        self.assertEqual(gc.detect_model("ls /tmp"), [])

    def test_loads_from_stdin_away_from_the_framework(self):
        # Remote hosts run this script as `python3 - --collect`, with no context/ beside it.
        with tempfile.TemporaryDirectory() as t, SCRIPT.open() as script:
            run = subprocess.run([sys.executable, "-", "--help"], stdin=script, cwd=t, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
