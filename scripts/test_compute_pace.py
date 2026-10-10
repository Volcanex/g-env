import datetime as dt
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_loader("compute_pace", SourceFileLoader("compute_pace", str(REPO / "bin/compute-pace")))
pace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pace)
CSPEC = importlib.util.spec_from_file_location("ctx", REPO / "context/session-context.py")
ctx = importlib.util.module_from_spec(CSPEC)
CSPEC.loader.exec_module(ctx)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 4, 12, 0, tzinfo=UTC)  # a Sunday, 13:00 London


def at(day, hour, minute=0):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def row(metric, used, resets, provider="claude", note=None):
    return {"provider": provider, "metric": metric, "status": "ok", "unit": "percent",
            "remaining": 100 - used, "used": used, "limit": 100, "resets_at": resets.isoformat(),
            "fetched_at": NOW.isoformat(), "note": note}


_rm_spec = importlib.util.spec_from_loader("refresh_models_budget", SourceFileLoader("refresh_models_budget", str(Path(__file__).resolve().parents[1] / "bin/refresh-models")))
_rm = importlib.util.module_from_spec(_rm_spec)
_rm_spec.loader.exec_module(_rm)
LADDER_MAX_CHARS = _rm.LADDER_MAX_CHARS  # the product's own cap (260)


class IntervalTests(unittest.TestCase):
    IDLE, TAIL = dt.timedelta(minutes=30), dt.timedelta(minutes=10)

    def test_gaps_capped_at_idle_and_last_prompt_gets_tail(self):
        turns = [at(3, 9), at(3, 9, 10), at(3, 12)]
        got = pace.intervals(turns, self.IDLE, self.TAIL)
        hours = [(e - s).total_seconds() / 3600 for s, e in got]
        self.assertEqual(hours, [10 / 60, 0.5, 10 / 60])

    def test_new_day_prompt_does_not_extend_previous_day(self):
        got = pace.intervals([at(3, 22), at(4, 8)], self.IDLE, self.TAIL)
        self.assertEqual((got[0][1] - got[0][0]), self.TAIL)

    def test_overlap_clips_to_window(self):
        ivs = [(at(4, 10), at(4, 11))]
        self.assertAlmostEqual(pace.overlap_hours(ivs, at(4, 10, 30), at(4, 12)), 0.5)


def obs(metric, used, resets, hours_ago, provider="claude"):
    r = row(metric, used, resets, provider)
    r["fetched_at"] = (NOW - dt.timedelta(hours=hours_ago)).isoformat()
    return r


def day_turns(per_day_hours=(9, 10, 11), days=28):
    out = []
    for n in range(0, days + 1):
        d = NOW - dt.timedelta(days=n)
        out += [d.replace(hour=h, minute=m) for h in per_day_hours for m in (0, 20, 40)]
    return out


class BuildTests(unittest.TestCase):
    def test_burn_is_measured_over_recorded_span_per_total_work_hour(self):
        rows = [row("seven_day", 30, at(11, 0))]
        # recorded 26h ago at 10%: the span covers 2 work days of 3h, so 20 points over ~6h
        data = pace.build(rows, day_turns(), NOW, observations=[obs("seven_day", 10, at(11, 0), 26)])
        q = data["quotas"][0]
        self.assertFalse(q["low_sample"])
        self.assertAlmostEqual(q["burn_pct_per_work_hour"] * q["sampled_work_hours"], 20, places=1)
        self.assertGreater(q["work_hours_left"], 5)
        self.assertAlmostEqual(q["projected_remaining_pct"],
                               max(0, 70 - q["burn_pct_per_work_hour"] * q["work_hours_left"]), places=1)
        self.assertEqual(data["hours"]["trailing_days"], 28)

    def test_safe_burn_would_land_at_about_five_percent(self):
        rows = [row("seven_day", 40, at(11, 0))]
        q = pace.build(rows, day_turns(), NOW, observations=[obs("seven_day", 30, at(11, 0), 26)])["quotas"][0]
        self.assertAlmostEqual(q["safe_burn_pct_per_work_hour"] * q["work_hours_left"], 60 - 5, places=1)
        self.assertGreater(q["safe_burn_pct_per_work_hour"], 0)
        late = pace.build([row("seven_day", 40, NOW - dt.timedelta(minutes=1))], day_turns(), NOW)["quotas"][0]
        self.assertIsNone(late["safe_burn_pct_per_work_hour"])  # no work hours left: no rate to compare

    def test_usual_pace_uses_seven_days_and_widens_when_thin(self):
        busy_week = [t for t in day_turns(per_day_hours=(9, 10, 11, 12, 13, 14)) if t >= NOW - dt.timedelta(days=7)]
        quiet_month = [t for t in day_turns(per_day_hours=(9,)) if t < NOW - dt.timedelta(days=7)]
        h = pace.build([], busy_week + quiet_month, NOW)["hours"]
        self.assertEqual(h["usual_pace_days"], 7)
        self.assertGreater(h["usual_pace_daily"], h["avg_daily_28d"])
        sparse = [at(1, 10), at(1, 10, 20), at(2, 10)] + [t for t in day_turns(per_day_hours=(9,)) if t < NOW - dt.timedelta(days=14)]
        self.assertEqual(pace.build([], sparse, NOW)["hours"]["usual_pace_days"], 28)
        two_weeks = [t for t in day_turns(per_day_hours=(9,)) if NOW - dt.timedelta(days=14) <= t < NOW - dt.timedelta(days=8)]
        self.assertEqual(pace.build([], two_weeks, NOW)["hours"]["usual_pace_days"], 14)

    def test_projection_follows_the_recent_level(self):
        rows = [row("seven_day", 30, at(11, 0))]
        observations = [obs("seven_day", 10, at(11, 0), 26)]
        flat = pace.build(rows, day_turns(), NOW, observations=observations)["quotas"][0]
        recent = [t for t in day_turns(per_day_hours=(9, 10, 11, 12, 13, 14)) if t >= NOW - dt.timedelta(days=7)]
        older = [t for t in day_turns() if t < NOW - dt.timedelta(days=7)]
        busy = pace.build(rows, recent + older, NOW, observations=observations)["quotas"][0]
        self.assertGreater(busy["work_hours_left"], flat["work_hours_left"])

    def test_low_sample_has_no_rate_and_no_projection(self):
        rows = [row("seven_day", 10, at(11, 0))]
        for observations in (None, [obs("seven_day", 9, at(11, 0), 0.5)]):
            q = pace.build(rows, [at(4, 11)], NOW, observations=observations)["quotas"][0]
            self.assertTrue(q["low_sample"])
            self.assertIsNone(q["burn_pct_per_work_hour"])
            self.assertIsNone(q["projected_remaining_pct"])
            self.assertIsNone(q["runs_out_at"])

    def test_observation_from_another_window_or_too_old_is_not_a_baseline(self):
        rows = [row("seven_day", 10, at(11, 0))]
        other = obs("seven_day", 90, at(4, 1), 26)  # previous window: different reset
        old = obs("seven_day", 1, at(11, 0), 60)  # older than the 48h lookback is filtered by loader
        self.assertIsNone(pace.baseline([other], rows[0], at(11, 0), NOW))
        self.assertIsNone(pace.baseline([old], rows[0], at(11, 0), NOW))

    def test_reset_jitter_still_matches_the_same_window(self):
        o = obs("seven_day", 1, at(11, 0), 26)
        o["resets_at"] = (at(11, 0) + dt.timedelta(microseconds=297699)).isoformat()
        self.assertIsNotNone(pace.baseline([o], row("seven_day", 5, at(11, 0)), at(11, 0), NOW))

    def test_runs_out_when_projection_exceeds_remaining(self):
        rows = [row("seven_day", 80, at(11, 0))]
        q = pace.build(rows, day_turns(), NOW, observations=[obs("seven_day", 20, at(11, 0), 26)])["quotas"][0]
        self.assertIsNotNone(q["runs_out_at"])
        self.assertLess(dt.datetime.fromisoformat(q["runs_out_at"]), at(11, 0))
        self.assertEqual(q["projected_remaining_pct"], 0.0)

    def test_no_run_out_when_it_fits(self):
        rows = [row("seven_day", 11, at(11, 0))]
        q = pace.build(rows, day_turns(), NOW, observations=[obs("seven_day", 10, at(11, 0), 26)])["quotas"][0]
        self.assertIsNone(q["runs_out_at"])
        self.assertGreater(q["projected_remaining_pct"], 0)

    def test_only_quota_rows_become_quotas_and_openrouter_is_balance(self):
        rows = [row("primary", 41, at(9, 21), provider="codex", note="10080 min window"),
                {"provider": "jev", "metric": "usage", "status": "unsupported", "unit": None, "resets_at": None},
                {"provider": "openrouter", "metric": "credits", "status": "ok", "unit": "USD",
                 "remaining": 3.0, "limit": 45.0, "used": 42.0, "fetched_at": NOW.isoformat(), "resets_at": None}]
        data = pace.build(rows, [], NOW)
        self.assertEqual([q["id"] for q in data["quotas"]], ["codex 7d"])
        self.assertEqual(data["openrouter"]["remaining_usd"], 3.0)

    def test_load_observations_reads_archive_rows_and_skips_old_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jsonl"
            path.write_text("\n".join([
                json.dumps({"timestamp": "x", "claude": {}, "codex": {}}), "garbage",
                json.dumps({"fetched_at": NOW.isoformat(), "rows": [obs("seven_day", 5, at(11, 0), 1)]}),
                json.dumps({"fetched_at": NOW.isoformat(), "rows": [obs("seven_day", 5, at(11, 0), 70)]})]))
            self.assertEqual(len(pace.load_observations(path, NOW)), 1)
            self.assertEqual(pace.load_observations(Path(tmp) / "none", NOW), [])

    def test_load_recomputes_time_to_reset_and_missing_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pace.json"
            self.assertIsNone(pace.load(path))
            data = pace.build([row("seven_day", 10, at(11, 0))], [], NOW)
            path.write_text(json.dumps(data))
            got = pace.load(path, now=NOW.timestamp() + 3600)
            self.assertEqual(got["age_seconds"], 3600)
            self.assertEqual(got["quotas"][0]["seconds_to_reset"], data["quotas"][0]["seconds_to_reset"] - 3600)
            path.write_text("{broken")
            self.assertIsNone(pace.load(path))


class CodexPromptTests(unittest.TestCase):
    def test_counts_only_top_level_user_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026/10/03"
            day.mkdir(parents=True)

            def rollout(name, meta, stamps):
                lines = [json.dumps({"type": "session_meta", "timestamp": stamps[0], "payload": meta})]
                lines += [json.dumps({"type": "event_msg", "timestamp": t, "payload": {"type": "task_started"}}) for t in stamps]
                (day / name).write_text("\n".join(lines))
            top = {"originator": "codex-tui", "thread_source": "user"}
            rollout("a.jsonl", top, ["2026-10-03T10:00:00Z", "2026-10-03T10:10:00Z"])
            rollout("b.jsonl", {"originator": "codex-tui", "thread_source": "subagent"}, ["2026-10-03T10:05:00Z"])
            rollout("c.jsonl", {"originator": "codex_exec", "thread_source": "user"}, ["2026-10-03T10:06:00Z"])
            rollout("d.jsonl", {"originator": "Claude Code", "thread_source": "user"}, ["2026-10-03T10:07:00Z"])
            self.assertEqual(pace.codex_prompts("2026-10-01", root=tmp),
                             ["2026-10-03T10:00:00Z", "2026-10-03T10:10:00Z"])
            self.assertEqual(pace.codex_prompts("2026-10-04", root=tmp), [])


class SessionBlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / ".cache/genv").mkdir(parents=True)

    def write(self, data):
        (self.home / ".cache/genv/compute-pace.json").write_text(json.dumps(data))

    def test_renders_one_line_per_quota_within_budget(self):
        rows = [row("five_hour", 2, at(4, 16)), row("seven_day", 2, at(11, 0))]
        self.write(pace.build(rows, [at(4, 11), at(4, 11, 20)], NOW, hosts=["h", "b"], sources=["claude", "codex"]))
        text = ctx.pace_section(self.home, now=NOW.timestamp())
        self.assertTrue(text.startswith("## Compute pace\n"))
        self.assertIn("claude 5h: 98% left", text)
        self.assertIn("claude 7d: 98% left, resets Sun", text)
        self.assertIn("used 2% so far (<3 work-h sampled)", text)
        self.assertIn("work (claude+codex, h,b):", text)
        self.assertIn("usual pace 28d avg", text)  # no complete active day: widest window
        self.assertLessEqual(len(text), 500)

    def test_rate_and_run_out_render(self):
        rows = [row("seven_day", 80, at(11, 0))]
        self.write(pace.build(rows, day_turns(), NOW, observations=[obs("seven_day", 20, at(11, 0), 26)]))
        text = ctx.pace_section(self.home, now=NOW.timestamp())
        self.assertRegex(text, r"burn \d+\.\d%/work-h vs safe \d+\.\d% \(5 work-h sampled\) -> runs out ~")
        self.write(pace.build([row("seven_day", 11, at(11, 0))], day_turns(), NOW,
                              observations=[obs("seven_day", 10, at(11, 0), 26)]))
        self.assertIn("% left at reset", ctx.pace_section(self.home, now=NOW.timestamp()))

    def test_without_a_collector_there_are_no_active_hours(self):
        with mock.patch.dict(sys.modules, {"ledger": None}):
            self.assertIsNone(pace.collect_turns(NOW)[0])
        path = self.home / ".cache/genv/compute-pace.json"
        data = pace.refresh(path, NOW, [row("seven_day", 2, at(11, 0))], lambda now: (None, [], [], None, None))
        self.assertEqual(data["hours"]["sources"], [])
        text = ctx.pace_section(self.home, now=NOW.timestamp())
        self.assertIn("work: no active hours", text)
        self.assertIn("claude 7d: 98% left", text)

    def test_stale_data_is_marked_with_age(self):
        self.write(pace.build([row("seven_day", 2, at(11, 0))], [], NOW))
        text = ctx.pace_section(self.home, now=NOW.timestamp() + 3 * 3600)
        self.assertIn("[usage 3h old]", text)
        self.assertIn("[pace data 3h old]", text)

    def test_missing_corrupt_and_malformed_fail_soft(self):
        path = self.home / ".cache/genv/compute-pace.json"
        for body in (None, "{not json", "[]", json.dumps({"computed_at": "2026-10-04T12:00:00+00:00"}),
                     json.dumps({"computed_at": "garbage", "quotas": [], "hours": {}})):
            if body is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(body)
            text = ctx.pace_section(self.home, now=NOW.timestamp())
            self.assertEqual(text.count("\n"), 2, text)
            self.assertIn("pace unavailable", text)

    def test_context_survives_without_cache(self):
        root = self.home / ".agents"
        root.mkdir()
        (root / "host.md").write_text("host")
        out = ctx.context(self.home, "/p", pulse=False)
        self.assertIn("pace unavailable", out)
        self.assertIn("## Roster", out)
        self.assertLessEqual(len(out), ctx.TARGET_CHARS)


class ModelsTests(unittest.TestCase):
    def roster(self, tmp, as_of="2026-10-04"):
        path = Path(tmp) / "models.json"
        path.write_text(json.dumps({"as_of": as_of, "sources": ["x"], "models": [
            {"id": "m1", "access": "claude-sub", "cost": "1x", "scores": {"AA": 58, "TB4": 59.6}, "role": "boss"},
            {"id": "m2", "access": "api", "cost": "", "scores": {}, "role": "classify", "note": "soon"}]}))
        return path

    def test_renders_compact_roster_with_scores_and_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = ctx.models_section(self.roster(tmp), today=dt.date(2026, 10, 5))
        self.assertIn("## Roster (as of 2026-10-04;", text)
        self.assertNotIn("stale", text)
        self.assertIn("claude-sub: m1 AA58 TB4 59.6, 1x: boss", text)
        self.assertIn("api: m2 classify; soon", text)

    def test_stale_marker_after_45_days_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.roster(tmp)
            self.assertNotIn("stale", ctx.models_section(path, today=dt.date(2026, 10, 4) + dt.timedelta(days=45)))
            self.assertIn("(stale, refresh)", ctx.models_section(path, today=dt.date(2026, 10, 4) + dt.timedelta(days=46)))

    def test_missing_or_broken_file_fails_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            for path in (Path(tmp) / "none.json", bad):
                bad.write_text("{nope")
                text = ctx.models_section(path)
                self.assertIn("model roster unavailable", text)
                self.assertEqual(text.count("\n"), 2)
            bad.write_text(json.dumps({"as_of": "not a date", "models": []}))
            self.assertIn("unavailable", ctx.models_section(bad))

    def test_shipped_roster_fits_its_budget(self):
        text = ctx.models_section(today=dt.date(2026, 10, 4))
        self.assertIn("luna", text)
        self.assertNotIn("jev", text)
        self.assertLessEqual(len(text), 1000)  # roster ~500 plus the ladder and SWE-bench lines

    def test_shipped_ladder_is_one_line_within_260_chars_with_latency(self):
        text = ctx.models_section(today=dt.date(2026, 10, 4))
        ladder = [l for l in text.splitlines() if l.startswith("effort ladder")]
        self.assertEqual(len(ladder), 1)
        self.assertLessEqual(len(ladder[0]), LADDER_MAX_CHARS)
        self.assertIn("sonnet-low .35/36/1s→sonnet-med .48/41/2s→opus-low .55/42/9s", ladder[0])
        self.assertIn("opus-max 6/58/704s", ladder[0])
        self.assertIn("SWE-bench Pro opus $/solved: low .12 87.4%, medium .22 92.8%, high .29 95.3%", text)

    def test_long_ladder_drops_arrow_spaces_instead_of_truncating(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.roster(tmp)
            data = json.loads(path.read_text())
            data["effort_ladder"] = [{"model": "sonnet-5.5", "effort": "xhigh", "score": 50 + n, "cost_usd": 1.5 + n, "ttft_s": 100 + n}
                                     for n in range(7)]
            path.write_text(json.dumps(data))
            line = [l for l in ctx.models_section(path, today=dt.date(2026, 10, 5)).splitlines() if l.startswith("effort ladder")][0]
        self.assertLessEqual(len(line), LADDER_MAX_CHARS)
        self.assertIn("→", line)
        self.assertNotIn(" → ", line)
        self.assertEqual(line.count("sonnet-xhigh"), 7)


if __name__ == "__main__":
    unittest.main()
