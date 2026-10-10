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


if __name__ == "__main__":
    unittest.main()
