"""Exercise the deployed hook protocol, with no model calls or private corpus."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / 'claude/hooks/ganti-gate.py'
spec = importlib.util.spec_from_file_location('ganti_gate', HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class GateTest(unittest.TestCase):
    def invoke(self, payload, response=None, error=None):
        gate = ModuleType('ganti.gate')
        def evaluate(event):
            self.assertEqual(event, payload)
            if error:
                raise error
            return response
        gate.evaluate = evaluate
        out = io.StringIO()
        with patch.dict(sys.modules, {'ganti.gate': gate}), \
             patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
             patch.object(sys, 'path', list(sys.path)), redirect_stdout(out):
            hook.main()
        return out.getvalue()

    def test_forwards_pretooluse_json_and_deny_reason(self):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Write',
                   'tool_input': {'file_path': '/tmp/pages/x/content.html', 'content': 'A — B'}}
        response = {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny', 'permissionDecisionReason': 'em_dash: split the sentence'}}
        self.assertEqual(json.loads(self.invoke(payload, response)), response)
        self.assertEqual(self.invoke(payload), '')

    def test_import_evaluation_and_json_errors_fail_open(self):
        self.assertEqual(self.invoke({}, error=RuntimeError('unavailable')), '')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'g-env/claude/hooks/ganti-gate.py'
            path.parent.mkdir(parents=True)
            path.write_text(HOOK.read_text())
            for stdin in ('broken json', '{}'):
                run = subprocess.run([sys.executable, '-I', str(path)], input=stdin,
                                     text=True, capture_output=True)
                self.assertEqual((run.returncode, run.stdout, run.stderr), (0, '', ''))

    def test_shipped_settings_register_gate_and_preserve_other_hooks(self):
        hooks = json.loads((REPO / 'claude/settings.json').read_text())['hooks']
        entries = [e for e in hooks['PreToolUse'] if e.get('matcher') == 'Write|Edit|MultiEdit']
        self.assertEqual(len(entries), 1)
        self.assertIn('ganti-gate.py', entries[0]['hooks'][0]['command'])
        self.assertTrue(any(e.get('matcher') == 'Agent|Task' for e in hooks['PreToolUse']))
        self.assertTrue(any('format-on-save.py' in h['command']
                            for e in hooks['PostToolUse'] for h in e['hooks']))


if __name__ == '__main__':
    unittest.main()
