import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "bin/compute-usage"
SPEC = importlib.util.spec_from_loader(
    "compute_usage", SourceFileLoader("compute_usage", str(SCRIPT)))
compute = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compute)


class ComputeUsageTests(unittest.TestCase):
    def test_claude_cache_age_stale_and_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sl-last.json"
            self.assertEqual(compute.read_claude(path, now=1000)["reason"], "cache_missing")
            path.write_text(json.dumps({"rate_limits": {
                "five_hour": {"used_percentage": 37, "resets_at": 2000},
                "seven_day": {"used_percentage": None}}}))
            os.utime(path, (0, 0))
            got = compute.read_claude(path, now=1000)
            self.assertEqual(got["age_seconds"], 1000)
            self.assertTrue(got["stale"])
            self.assertEqual(got["windows"], {"five_hour": {
                "used_percent": 37, "remaining_percent": 63, "resets_at": 2000}})

    def test_codex_multiple_buckets_and_nullable_windows(self):
        result = {"rateLimitsByLimitId": {
            "codex": {"limitName": "Plan", "primary": {
                "usedPercent": 25, "windowDurationMins": 300, "resetsAt": 99},
                "secondary": None},
            "other": {"limitName": None, "primary": None, "secondary": {
                "usedPercent": 80, "windowDurationMins": None, "resetsAt": None}}}}
        got = compute.read_codex(fetcher=lambda **_: result)
        self.assertEqual(got["status"], "ok")
        self.assertEqual(len(got["buckets"]), 2)
        self.assertEqual(got["buckets"][0]["windows"]["primary"]["used_percent"], 25)
        self.assertNotIn("primary", got["buckets"][1]["windows"])
        self.assertEqual(got["buckets"][1]["windows"]["secondary"], {
            "used_percent": 80, "window_minutes": None, "resets_at": None})
        empty = compute.read_codex(fetcher=lambda **_: {'rateLimits': {
            'primary': None, 'secondary': None}})
        self.assertEqual(empty['status'], 'unavailable')

    def test_timeout_and_exited_server_are_concise(self):
        with tempfile.TemporaryDirectory() as temp:
            server = Path(temp) / 'fake-codex'
            for body, reason in [('import time; time.sleep(10)', 'timed out'),
                                 ('raise SystemExit(0)', 'exited')]:
                server.write_text('#!/usr/bin/env python3\n' + body + '\n')
                server.chmod(0o700)
                start = time.monotonic()
                with self.assertRaisesRegex(compute.CodexReadError, reason):
                    compute._codex_request(timeout=0.2, executable=str(server))
                self.assertLess(time.monotonic() - start, 2)

    def test_quota_extraction_omits_account_and_arbitrary_fields(self):
        raw = {'accountId': 'private-account', 'token': 'private-token',
               'rateLimits': {'primary': {'usedPercent': 30,
                                          'resetsAt': 'private-token'},
                              'conversation': 'private-conversation'}}
        encoded = json.dumps(compute.read_codex(fetcher=lambda **_: raw))
        self.assertNotIn('private-', encoded)
        self.assertNotIn('accountId', encoded)

    def test_archive_contains_only_sanitized_snapshot_and_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "cache" / "compute-usage.jsonl"
            payload = {"timestamp": "2026-10-03T00:00:00+00:00",
                       "claude": {"status": "unavailable", "reason": "cache_missing"},
                       "codex": {"status": "ok", "buckets": []}}
            compute.record(payload, archive)
            self.assertEqual(json.loads(archive.read_text()), payload)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            self.assertEqual(archive.parent.stat().st_mode & 0o777, 0o700)
            self.assertNotIn("conversation", archive.read_text())

    def test_webshare_rows_period_and_account_wide_stats(self):
        plans = {"results": [
            {"id": 1, "status": "cancelled", "proxy_subtype": "isp", "bandwidth_limit": 250.0,
             "proxy_replacements_total": 10, "proxy_replacements_used": 0},
            {"id": 2, "status": "active", "proxy_subtype": "isp", "bandwidth_limit": 250.0,
             "proxy_replacements_total": 100, "proxy_replacements_used": 7}]}
        sub = {"plan": 2, "term": "monthly", "start_date": "2026-10-15T12:55:54Z",
               "end_date": "2026-11-14T12:55:54Z"}
        urls = []

        def fake(url, headers, timeout=10):
            urls.append(url)
            self.assertEqual(headers["Authorization"], "Token k")
            if "subscription/plan/" in url:
                return plans
            if "stats/aggregate" in url:
                return {"bandwidth_total": 5e9, "requests_total": 10, "requests_failed": 1}
            return sub

        now = 1792400000  # 2026-10-19
        old = compute._get_json, compute._env_key
        compute._get_json, compute._env_key = fake, lambda name: "k"
        try:
            rows = compute.fetch_webshare(now)
        finally:
            compute._get_json, compute._env_key = old
        bw, rep = rows
        self.assertEqual((bw["metric"], bw["used"], bw["remaining"], bw["limit"], bw["unit"]),
                         ("bandwidth:2", 5.0, 245.0, 250.0, "GB"))
        self.assertEqual(bw["resets_at"], "2026-11-14T12:55:54+00:00")
        self.assertEqual(bw["period_start"], "2026-10-15T12:55:54+00:00")
        self.assertEqual((rep["metric"], rep["used"], rep["remaining"], rep["limit"]),
                         ("replacements:2", 7, 93, 100))
        stats = [u for u in urls if "stats/aggregate" in u][0]
        self.assertIn("timestamp__gte=2026-10-15T12:55:54Z", stats)
        self.assertIn("plan_id=2", stats)

    def test_webshare_missing_key_and_period_rollover(self):
        old = compute._env_key
        compute._env_key = lambda name: None
        try:
            self.assertEqual(compute.fetch_webshare(0)[0]["status"], "unavailable")
        finally:
            compute._env_key = old
        sub = {"start_date": "2026-01-31T00:00:00Z", "term": "annual", "end_date": "2027-01-31T00:00:00Z"}
        for day, want in (("2026-02-25", ("2026-01-31", "2026-02-28")), ("2026-03-05", ("2026-02-28", "2026-03-31"))):
            start, end = compute._webshare_period(sub, time.mktime(time.strptime(day, "%Y-%m-%d")) - time.timezone)
            self.assertEqual((start.date().isoformat(), end.date().isoformat()), want)


if __name__ == "__main__":
    unittest.main()
