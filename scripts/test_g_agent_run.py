"""g-agent-run fallback order, with fake vendor CLIs: no tokens are spent."""
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_loader('g_agent_run', SourceFileLoader('g_agent_run', str(REPO / 'bin/g-agent-run')))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

CODEX_OK = '''#!/bin/sh
cat >/dev/null
out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
printf 'codex answer' > "$out"
echo '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}'
'''
CLAUDE_OK = '''#!/bin/sh
cat >/dev/null
echo '{"result":"claude answer","usage":{"input_tokens":5,"output_tokens":3},"total_cost_usd":0.01}'
'''
ARGV_CLAUDE = '''#!/bin/sh
cat >/dev/null
printf '%s\\n' "$@" > "$ARGV_OUT"
echo '{"result":"claude answer","usage":{}}'
'''
ARGV_CODEX = '''#!/bin/sh
cat >/dev/null
printf '%s\\n' "$@" > "$ARGV_OUT"
out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
printf 'codex answer' > "$out"
'''
STDIN_LEN = '''#!/bin/sh
wc -c > "$ARGV_OUT"
echo '{"result":"ok"}'
'''
FAIL_LIMIT = '#!/bin/sh\ncat >/dev/null\necho "You have hit your usage limit" >&2\nexit 1\n'
FAIL_PLAIN = '#!/bin/sh\ncat >/dev/null\necho boom >&2\nexit 7\n'
CLAUDE_ERR_JSON = '#!/bin/sh\ncat >/dev/null\necho \'{"is_error":true,"result":"Invalid API key. Please run /login"}\'\n'
HANG = '#!/bin/sh\nsleep 30\n'


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        for tool in ('cat', 'sleep', 'wc'):  # PATH holds only the fakes plus these, so a real vendor CLI can never run
            (self.bin / tool).symlink_to(next(Path(d) / tool for d in ('/usr/bin', '/bin') if (Path(d) / tool).exists()))
        self.work = self.tmp / 'work'
        (self.tmp / 'prompt.txt').write_text('do the thing')
        self.config = {'roles': {'gather': ['codex:gpt-6-luna', 'claude:sonnet'], 'verify': ['claude:sonnet', 'codex:gpt-6.1-sol']},
                       'timeout_s': {'gather': 2, 'verify': 2}, 'quota_floor_pct': 3,
                       'quota_ids': {'claude': ['claude 5h'], 'codex': ['codex 7d']}}
        self.write_config()
        env = {'PATH': str(self.bin), 'G_AGENT_PROVIDERS': str(self.tmp / 'providers.json'),
               'G_AGENT_PACE': str(self.tmp / 'pace.json'), 'G_AGENT_MODE': str(self.tmp / 'mode.json'), 'G_AGENT_LOG': str(self.tmp / 'log.jsonl')}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self):
        (self.tmp / 'providers.json').write_text(json.dumps(self.config))

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def run_role(self, role='gather'):
        out, err = io.StringIO(), io.StringIO()
        meta = self.tmp / 'meta.json'
        with redirect_stdout(out), redirect_stderr(err):
            code = runner.main(['--role', role, '--prompt-file', str(self.tmp / 'prompt.txt'), '--workdir', str(self.work),
                                '--web', '--meta', str(meta)])
        return code, out.getvalue().strip(), json.loads(meta.read_text()), err.getvalue()

    def test_first_provider_wins_when_healthy(self):
        self.fake('codex', CODEX_OK)
        self.fake('claude', CLAUDE_OK)
        code, text, meta, _ = self.run_role()
        self.assertEqual((code, text, meta['provider']), (0, 'codex answer', 'codex:gpt-6-luna'))
        self.assertEqual(meta['usage'], {'input': 10, 'output': 2})

    def test_codex_usage_limit_falls_back_to_claude_and_logs_why(self):
        self.fake('codex', FAIL_LIMIT)
        self.fake('claude', CLAUDE_OK)
        code, text, meta, err = self.run_role()
        self.assertEqual((code, text, meta['provider']), (0, 'claude answer', 'claude:sonnet'))
        self.assertEqual(meta['attempts'][0]['reason'], 'auth-or-quota')
        self.assertIn('usage limit', meta['attempts'][0]['detail'])
        lines = [json.loads(line) for line in (self.tmp / 'log.jsonl').read_text().splitlines()]
        self.assertEqual([line['status'] for line in lines], ['failed', 'ok'])
        self.assertIn('auth-or-quota', err)

    def test_verify_order_is_claude_then_codex(self):
        self.fake('claude', CLAUDE_ERR_JSON)
        self.fake('codex', CODEX_OK)
        code, text, meta, _ = self.run_role('verify')
        self.assertEqual((code, text, meta['provider']), (0, 'codex answer', 'codex:gpt-6.1-sol'))
        self.assertEqual(meta['attempts'][0]['reason'], 'auth-or-quota')

    def test_both_clis_failing_exits_3_with_every_reason(self):
        self.fake('codex', FAIL_PLAIN)
        self.fake('claude', FAIL_LIMIT)
        code, text, meta, err = self.run_role()
        self.assertEqual((code, text, meta['provider']), (3, '', None))
        self.assertEqual([(a['provider'], a['reason']) for a in meta['attempts']],
                         [('codex:gpt-6-luna', 'exit'), ('claude:sonnet', 'auth-or-quota')])
        self.assertIn('every provider failed', err)

    def test_timeout_falls_back(self):
        self.fake('codex', HANG)
        self.fake('claude', CLAUDE_OK)
        code, text, meta, _ = self.run_role()
        self.assertEqual((code, meta['attempts'][0]['reason'], meta['provider']), (0, 'timeout', 'claude:sonnet'))

    def test_missing_cli_falls_back(self):
        self.fake('claude', CLAUDE_OK)
        code, _, meta, _ = self.run_role()
        self.assertEqual((code, meta['attempts'][0]['reason'], meta['provider']), (0, 'missing-cli', 'claude:sonnet'))

    def test_quota_below_floor_skips_provider_without_running_it(self):
        self.fake('codex', CODEX_OK)
        self.fake('claude', CLAUDE_OK)
        now = runner.dt.datetime.now(runner.dt.timezone.utc).isoformat()
        (self.tmp / 'pace.json').write_text(json.dumps({'computed_at': now, 'quotas': [{'id': 'codex 7d', 'remaining_pct': 1.0}, {'id': 'claude 5h', 'remaining_pct': 90}]}))
        code, text, meta, _ = self.run_role()
        self.assertEqual((code, text, meta['provider']), (0, 'claude answer', 'claude:sonnet'))
        self.assertEqual((meta['attempts'][0]['status'], meta['attempts'][0]['reason']), ('skipped', 'quota-floor'))

    def test_empty_answer_falls_back(self):
        self.fake('codex', '#!/bin/sh\ncat >/dev/null\nexit 0\n')
        self.fake('claude', CLAUDE_OK)
        code, _, meta, _ = self.run_role()
        self.assertEqual((code, meta['attempts'][0]['reason']), (0, 'empty'))

    def run_flags(self, role, *flags):
        (self.tmp / 'argv.txt').unlink(missing_ok=True)
        with patch.dict(os.environ, {'ARGV_OUT': str(self.tmp / 'argv.txt')}), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = runner.main(['--role', role, '--prompt-file', str(self.tmp / 'prompt.txt'), '--workdir', str(self.work), *flags])
        return code, (self.tmp / 'argv.txt').read_text().split()

    def test_claude_without_tools_has_no_bash_and_no_connectors(self):
        self.fake('claude', ARGV_CLAUDE)
        code, argv = self.run_flags('verify')
        self.assertEqual(code, 0)
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read')
        self.assertIn('--strict-mcp-config', argv)

    def test_claude_tools_mode_gets_bash_and_edits_only_when_writable(self):
        self.fake('claude', ARGV_CLAUDE)
        _, argv = self.run_flags('verify', '--tools', '--sandbox', 'workspace-write')
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Bash,Write,Edit')
        _, argv = self.run_flags('verify', '--tools', '--sandbox', 'read-only')
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Bash')

    def test_connectors_flag_drops_strict_mcp_config(self):
        self.fake('claude', ARGV_CLAUDE)
        self.config['connectors'] = ['mcp__claude_ai_Era_Context', 'mcp__claude_ai_Brain']
        self.write_config()
        _, argv = self.run_flags('verify', '--tools', '--connectors')
        self.assertNotIn('--strict-mcp-config', argv)
        allowed = argv[argv.index('--allowedTools') + 1].split(',')
        self.assertEqual(allowed[-2:], ['mcp__claude_ai_Era_Context', 'mcp__claude_ai_Brain'])
        _, argv = self.run_flags('verify', '--tools')
        self.assertNotIn('mcp__claude_ai_Brain', argv[argv.index('--allowedTools') + 1])

    def test_role_config_can_force_tools(self):
        self.config['tools'] = {'verify': True}
        self.write_config()
        self.fake('claude', ARGV_CLAUDE)
        _, argv = self.run_flags('verify', '--sandbox', 'read-only')
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Bash')

    def test_codex_tools_mode_gets_network_only_when_writable(self):
        self.fake('codex', ARGV_CODEX)
        _, argv = self.run_flags('gather', '--tools', '--sandbox', 'workspace-write')
        self.assertIn('sandbox_workspace_write.network_access=true', argv)
        self.assertEqual(argv[argv.index('--sandbox') + 1], 'workspace-write')
        _, argv = self.run_flags('gather', '--tools', '--sandbox', 'read-only')
        self.assertNotIn('sandbox_workspace_write.network_access=true', argv)

    def test_full_access_sandbox_is_passed_to_codex_and_makes_claude_writable(self):
        self.fake('codex', ARGV_CODEX)
        _, argv = self.run_flags('gather', '--tools', '--sandbox', 'danger-full-access')
        self.assertEqual(argv[argv.index('--sandbox') + 1], 'danger-full-access')
        self.fake('claude', ARGV_CLAUDE)
        _, argv = self.run_flags('verify', '--tools', '--sandbox', 'danger-full-access')
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Bash,Write,Edit')

    def test_long_prompt_goes_over_stdin_not_argv(self):
        self.fake('claude', STDIN_LEN)
        (self.tmp / 'prompt.txt').write_text('x' * 500_000)
        self.run_flags('verify', '--tools')
        self.assertEqual(int((self.tmp / 'argv.txt').read_text()), 500_000)

    def test_shipped_providers_define_review_with_tools(self):
        config = json.loads((REPO / 'context/providers.json').read_text())
        self.assertEqual(config['roles']['review'], ['claude:opus', 'codex:gpt-6.1-sol'])
        self.assertEqual((config['effort']['review'], config['tools']['review']), ('medium', True))

    def test_unknown_role_is_a_usage_error(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(['--role', 'nope', '--prompt-file', str(self.tmp / 'prompt.txt'), '--workdir', str(self.work)]), 2)


if __name__ == '__main__':
    unittest.main()
