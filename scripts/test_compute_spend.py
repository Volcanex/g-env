"""compute-spend quota attribution, using temporary homes and no network."""
import copy
import datetime as dt
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_loader("compute_spend", SourceFileLoader("compute_spend", str(REPO / "bin/compute-spend")))
spend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spend)
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 5, 12, tzinfo=UTC)
PACE = {"quotas": [{"id": "claude 7d", "provider": "claude", "resets_at": "2026-10-11T01:00:00Z"},
                    {"id": "codex 7d", "provider": "codex", "resets_at": "2026-10-09T21:50:00Z"}]}


def assistant(mid, stamp="2026-10-05T10:00:00Z", model="claude-opus-5-5", **usage):
    return {"type": "assistant", "timestamp": stamp, "message": {"id": mid, "model": model, "usage": usage}}


def job(role="build", stamp="2026-10-05T10:00:00Z", provider="codex:gpt-6.1-sol", status="ok", **extra):
    return {"ts": stamp, "role": role, "provider": provider, "status": status, **extra}


class SpendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {"HOME": str(self.home), "G_SPEND_HOST": "h", "G_SPEND_REMOTES": ""})
        env.start()
        self.addCleanup(env.stop)
        network = patch.object(spend.subprocess, "run", side_effect=AssertionError("unexpected network"))
        self.ssh = network.start()
        self.addCleanup(network.stop)

    def lines(self, relative, rows, modified=None):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(row if isinstance(row, str) else json.dumps(row) for row in rows) + "\n")
        stamp = (modified or NOW).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def collect(self, pace=PACE):
        return spend.collect(now=NOW, pace=pace)

    def test_dedupe_keeps_last_usage_and_skips_synthetic_and_bad_lines(self):
        self.lines(".claude/projects/p/session.jsonl", [
            'not JSON, no assistant marker', '{"assistant": broken',
            {"type": "user", "message": "assistant"},
            assistant("one", input_tokens=10, output_tokens=1),
            assistant("one", input_tokens=100, cache_creation_input_tokens=40, cache_read_input_tokens=50, output_tokens=20),
            assistant("synthetic", model="<synthetic>", output_tokens=1000)])
        data = self.collect()
        amount = data["claude"]["today"]["by_model"]["claude-opus-5-5"]
        self.assertEqual(amount, {"input": 100, "cache_write": 40, "cache_read": 50, "output": 20, "units": 51.0, "turns": 1})
        worker = data["claude"]["today"]["by_worker"]["main"]
        self.assertEqual(worker["models"], {"claude-opus-5-5": 51.0})

    def test_dedupe_across_files(self):
        self.lines(".claude/projects/p/a.jsonl", [assistant("one", input_tokens=10)])
        self.lines(".claude/projects/p/b.jsonl", [assistant("one", input_tokens=20)])
        self.assertEqual(self.collect()["claude"]["week"]["by_model"]["claude-opus-5-5"]["input"], 20)

    def test_subagent_metadata_and_fallback(self):
        path = self.lines(".claude/projects/p/s/subagents/agent-123.jsonl", [assistant("sub", model="claude-sonnet-5-5", output_tokens=10)])
        path.with_suffix(".meta.json").write_text(json.dumps({"agentType": "g-superworker", "description": "worker"}))
        self.lines(".claude/projects/p/s/subagents/agent-456.jsonl", [assistant("unknown", output_tokens=2)])
        workers = self.collect()["claude"]["today"]["by_worker"]
        self.assertEqual(workers["opus-medium-worker"]["models"], {"claude-sonnet-5-5": 6.0})
        self.assertEqual(workers["subagent"]["output"], 2)

    def test_only_recent_transcripts_are_opened(self):
        old = self.lines(".claude/projects/p/old.jsonl", [assistant("old", output_tokens=100)], NOW - dt.timedelta(days=30))
        self.lines(".claude/projects/p/new.jsonl", [assistant("new", output_tokens=2)])
        real_open = Path.open

        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path, old)
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded):
            self.assertEqual(self.collect()["claude"]["today"]["by_model"]["claude-opus-5-5"]["output"], 2)

    def test_london_days_across_midnight(self):
        self.lines(".claude/projects/p/s.jsonl", [
            assistant("yesterday", "2026-10-04T22:59:59Z", output_tokens=1),
            assistant("today", "2026-10-04T23:00:00Z", output_tokens=2),
            assistant("too-old", "2026-09-27T22:59:59Z", output_tokens=4),
            assistant("first-day", "2026-09-27T23:00:00Z", output_tokens=8),
            assistant("future", "2026-10-06T10:00:00Z", output_tokens=16)])
        section = self.collect()["claude"]
        self.assertEqual(section["today"]["by_model"]["claude-opus-5-5"]["output"], 2)
        daily = {row["date"]: row["by_model"] for row in section["daily"]}
        self.assertEqual(list(daily), [f"2026-09-{d}" for d in (28, 29, 30)] + [f"2026-10-0{d}" for d in range(1, 6)])
        self.assertEqual(daily["2026-10-04"]["claude-opus-5-5"]["output"], 1)
        self.assertEqual(daily["2026-09-28"]["claude-opus-5-5"]["output"], 8)

    def test_london_winter_midnight_and_dst_day(self):
        now = dt.datetime(2026, 10, 26, 12, tzinfo=UTC)
        self.lines(".claude/projects/p/s.jsonl", [assistant("yesterday", "2026-10-25T23:30:00Z", output_tokens=1),
                                                 assistant("today", "2026-10-26T00:00:00Z", output_tokens=2)], now)
        section = spend.collect(now=now, pace={})["claude"]
        self.assertEqual(section["today"]["by_model"]["claude-opus-5-5"]["output"], 2)
        self.assertEqual(section["daily"][-2]["by_model"]["claude-opus-5-5"]["output"], 1)

    def test_provider_week_starts_from_pace_and_fallback(self):
        self.lines(".claude/projects/p/s.jsonl", [assistant("before", "2026-10-04T00:59:59Z", output_tokens=1),
                                                 assistant("after", "2026-10-04T01:00:00Z", output_tokens=2)])
        self.lines(".cache/genv/g-agent-run.jsonl", [job(stamp="2026-10-02T21:49:59Z", usage={"output": 1}),
                                                    job(stamp="2026-10-02T21:50:00Z", usage={"output": 2})])
        data = self.collect()
        self.assertEqual(data["claude"]["week_start"], "2026-10-04T01:00:00+00:00")
        self.assertEqual(data["codex"]["week_start"], "2026-10-02T21:50:00+00:00")
        self.assertEqual(data["claude"]["week"]["by_model"]["claude-opus-5-5"]["output"], 2)
        self.assertEqual(data["codex"]["week"]["by_model"]["gpt-6.1-sol"]["output"], 2)
        fallback = self.collect({"quotas": [{"id": "claude 7d", "resets_at": "bad"}]})
        for provider in ("claude", "codex"):
            self.assertEqual(fallback[provider]["week_start"], (NOW - dt.timedelta(days=7)).isoformat())

    def test_job_log_and_claude_fallback_metadata(self):
        self.lines(".cache/genv/g-agent-run.jsonl", [
            job(stamp="2026-09-01T10:00:00Z", role="old", usage={"output": 100}),
            job(usage={"input": 100, "output": 10}, effort="high", web=True, secs=41, label="build it"),
            job(stamp="2026-10-05T10:01:00Z", provider="claude:sonnet", usage={"input": 50, "output": 5}),
            job(role="gather", stamp="2026-10-05T10:02:00Z", provider="codex:gpt-6-luna", usage={"input": 100, "output": 10}),
            job(stamp="2026-10-05T10:03:00Z", status="failed", reason="timeout", usage={"input": 10, "output": 1}),
            job(role="gather", stamp="2026-10-05T10:04:00Z", status="skipped", reason="quota-floor"),
            'broken'])
        data = self.collect()
        codex, claude = data["codex"], data["claude"]
        self.assertEqual(codex["today"]["by_worker"]["build"], {"input": 110, "cache_write": 0, "cache_read": 0,
            "output": 11, "units": 33.0, "turns": 3, "models": {"gpt-6.1-sol": 33.0}})
        self.assertNotIn("sonnet", codex["today"]["by_model"])
        self.assertEqual(claude["today"]["by_worker"]["sol-fallback:build"]["models"], {"sonnet": 9.0})
        self.assertEqual(claude["today"]["by_model"]["sonnet"]["input"], 50)
        self.assertEqual(codex["last_by_role"]["build"], "2026-10-05T10:01:00Z")
        self.assertEqual(codex["last_by_role"]["gather"], "2026-10-05T10:02:00Z")
        self.assertEqual(codex["last_by_role"]["old"], "2026-09-01T10:00:00Z")
        self.assertEqual(codex["recent"][0]["status"], "skipped")
        recent = next(row for row in codex["recent"] if row["label"])
        self.assertEqual(recent, {"ts": "2026-10-05T10:00:00Z", "host": "h", "role": "build", "provider": "codex:gpt-6.1-sol",
            "status": "ok", "reason": None, "effort": "high", "web": True, "secs": 41, "label": "build it", "units": 30.0})

    def test_model_factors(self):
        for provider, model, expected in (("claude", "claude-fable-5-1", 2), ("claude", "claude-haiku-4-5-20251001", 0.2),
                                          ("codex", "gpt-6-astra", 5), ("codex", "gpt-6-luna", 0.05), ("codex", "unknown", 1)):
            self.assertEqual(spend.tokens(provider, model, {"output": 1})["units"], expected)

    def rollout(self, name, source="vscode", originator="codex-tui"):
        def event(stamp, total):
            return {"type": "event_msg", "timestamp": stamp, "payload": {"type": "token_count", "info": {
                "total_token_usage": total, "last_token_usage": {"input_tokens": 9999, "output_tokens": 9999}}}}
        first = {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10, "reasoning_output_tokens": 5}
        second = {"input_tokens": 150, "cached_input_tokens": 50, "output_tokens": 20, "reasoning_output_tokens": 8}
        return self.lines(f".codex/sessions/2026/10/04/{name}.jsonl", [
            {"type": "session_meta", "payload": {"originator": originator, "source": source}},
            {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
            event("2026-10-04T22:59:59Z", first),
            event("2026-10-04T23:00:00Z", first),
            {"type": "turn_context", "payload": {"model": "gpt-6-astra"}},
            event("2026-10-05T10:00:00Z", second)])

    def test_interactive_codex_cumulative_events_and_model_switch(self):
        self.rollout("interactive")
        self.rollout("exec", source="exec", originator="codex_exec")
        codex = self.collect()["codex"]
        self.assertEqual(codex["today"]["by_model"], {"gpt-6-astra": {"input": 50, "cache_write": 0, "cache_read": 10,
            "output": 10, "units": 91.0, "turns": 1}})
        self.assertEqual(codex["week"]["by_worker"]["interactive"]["input"], 150)
        self.assertEqual(codex["week"]["by_worker"]["interactive"]["turns"], 2)
        self.assertEqual(codex["week"]["by_worker"]["interactive"]["models"], {"gpt-6.1-sol": 22.8, "gpt-6-astra": 91.0})

    def test_interactive_subagents_and_unclear_events(self):
        self.rollout("child", source={"subagent": {"thread_spawn": {}}})
        self.lines(".codex/sessions/2026/10/05/unclear.jsonl", [
            {"type": "session_meta", "payload": {"originator": "codex-tui", "source": "cli"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"unknown_usage": 5}}}])
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertIn("interactive", self.collect()["codex"]["week"]["by_worker"])
        self.assertIn("unclear Codex token format", stderr.getvalue())

    def test_interactive_source_with_non_tui_originator(self):
        self.rollout("vscode", originator="Claude Code")
        worker = self.collect()["codex"]["week"]["by_worker"].get("interactive")
        self.assertIsNotNone(worker)
        self.assertEqual(worker["input"], 150)
        self.assertEqual(worker["turns"], 2)

    def test_merge_two_hosts(self):
        self.lines(".claude/projects/p/s.jsonl", [assistant("one", output_tokens=10)])
        self.lines(".cache/genv/g-agent-run.jsonl", [job(usage={"input": 100, "output": 10})])
        local = self.collect()
        remote = copy.deepcopy(local)
        remote["hosts"], remote["errors"] = ["b"], {"other": "ssh: down"}
        remote["codex"]["last_by_role"]["build"] = "2026-10-05T11:00:00+00:00"
        remote["codex"]["recent"][0]["host"] = "b"
        original = copy.deepcopy(local)
        data = spend.merge([local, remote])
        self.assertEqual(local, original)
        self.assertEqual(data["hosts"], ["h", "b"])
        self.assertEqual(data["errors"], {"other": "ssh: down"})
        for window in ("today", "week"):
            self.assertEqual(data["claude"][window]["by_model"]["claude-opus-5-5"]["output"], 20)
            self.assertEqual(data["claude"][window]["by_worker"]["main"]["models"], {"claude-opus-5-5": 20.0})
            self.assertEqual(data["codex"][window]["by_worker"]["build"]["input"], 200)
        self.assertEqual(data["claude"]["daily"][-1]["by_model"]["claude-opus-5-5"]["turns"], 2)
        self.assertEqual(data["codex"]["last_by_role"]["build"], "2026-10-05T11:00:00+00:00")
        self.assertEqual({row["host"] for row in data["codex"]["recent"]}, {"h", "b"})
        self.assertEqual(len(spend.merge([data, remote])["codex"]["recent"]), 2)

    def test_recent_is_newest_forty_and_last_ok_handles_unsorted_rows(self):
        rows = [job(stamp=(NOW - dt.timedelta(minutes=n)).isoformat(), usage={"output": 1}) for n in range(50)]
        self.lines(".cache/genv/g-agent-run.jsonl", rows)
        data = self.collect()
        self.assertEqual(len(data["codex"]["recent"]), 40)
        self.assertEqual(data["codex"]["recent"][0]["ts"], NOW.isoformat())
        self.assertEqual(data["codex"]["last_by_role"]["build"], NOW.isoformat())
        self.assertEqual(data["codex"]["today"]["by_worker"]["build"]["turns"], 50)

    def test_ssh_contract_alias_and_shared_pace(self):
        local = self.collect()
        remote = self.collect()
        remote["codex"]["recent"] = [{**job(), "host": "h", "units": 0.0}]
        self.ssh.side_effect = None
        self.ssh.return_value = subprocess.CompletedProcess([], 0, stdout=json.dumps(remote), stderr="")
        data = spend.collect_remotes(local, PACE, "b=gabriel@remote:genv/bin/compute-spend")
        args, kwargs = self.ssh.call_args
        self.assertEqual(args[0], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "gabriel@remote", "python3",
                                   "genv/bin/compute-spend", "--local", "--json", "--pace", "-"])
        self.assertEqual(json.loads(kwargs["input"]), PACE)
        self.assertEqual(kwargs["timeout"], 120)
        self.assertEqual(data["hosts"], ["h", "b"])
        self.assertEqual(data["codex"]["recent"][0]["host"], "b")

    def test_remote_failures_are_nonfatal_and_fallback_weeks_are_shared(self):
        self.ssh.side_effect = [subprocess.CalledProcessError(255, "ssh", stderr="connection refused"),
                                subprocess.TimeoutExpired("ssh", 120),
                                subprocess.CompletedProcess([], 0, stdout="{}", stderr="")]
        local = self.collect({})
        data = spend.collect_remotes(local, {}, "b=u@b:path,c=u@c:path d=u@d:path")
        self.assertEqual(data["hosts"], ["h", "b", "c", "d"])
        self.assertEqual(set(data["errors"]), {"b", "c", "d"})
        self.assertEqual(data["errors"]["b"], "ssh: connection refused")
        shared = json.loads(self.ssh.call_args.kwargs["input"])
        self.assertEqual(spend.week_starts(shared, NOW + dt.timedelta(minutes=1)), spend.week_starts({}, NOW))

    def test_cli_local_json_pace_stdin_and_atomic_cache(self):
        self.lines(".claude/projects/p/s.jsonl", [assistant("one", output_tokens=10)])
        self.ssh.side_effect = AssertionError("--local must not use ssh")
        with patch.dict(os.environ, {"G_SPEND_REMOTES": "b=u@b:path", "G_SPEND_HOST": "test-host"}):
            out = io.StringIO()
            with patch.object(spend.sys, "stdin", io.StringIO(json.dumps(PACE))), redirect_stdout(out):
                self.assertEqual(spend.main(["--local", "--json", "--pace", "-"]), 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["hosts"], ["test-host"])
            self.assertEqual(data["claude"]["week_start"], "2026-10-04T01:00:00+00:00")
            path = self.home / ".cache/genv/compute-spend.json"
            self.assertFalse(path.exists())
            path.parent.mkdir(parents=True)
            path.write_text("old")
            with redirect_stdout(out):
                self.assertEqual(spend.main(["--local"]), 0)
            self.assertEqual(json.loads(path.read_text())["hosts"], ["test-host"])
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_pace_file_and_missing_inputs(self):
        path = self.home / "pace.json"
        path.write_text(json.dumps(PACE))
        self.assertEqual(spend.load_pace(path), PACE)
        self.assertEqual(spend.load_pace(self.home / "missing"), {})
        path.write_text("invalid")
        self.assertEqual(spend.load_pace(path), {})
        data = self.collect({})
        self.assertEqual(set(data), {"computed_at", "hosts", "errors", "claude", "codex"})
        self.assertEqual(data["claude"]["today"], {"by_model": {}, "by_worker": {}})
        self.assertEqual(len(data["codex"]["daily"]), 8)

    def test_closed_output_pipe_exits_cleanly(self):
        with subprocess.Popen([str(REPO / "bin/compute-spend"), "--local", "--json"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            process.stdout.close()
            stderr = process.stderr.read().decode()
            self.assertEqual(process.wait(timeout=10), 0, stderr)
            self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
