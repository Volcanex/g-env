#!/usr/bin/env python3
"""PreToolUse gate on Agent: at COLD or FREEZING, Claude-tier subagents need the operator's OK.

opus-medium-suborchestrator (Opus, but its workers are Sol) and Codex agents stay free. Approval is a line in the prompt starting
"Operator approved:" (or the older "Gabriel approved:"). Missing or stale pace data fails open.
"""
import importlib.util
import json
import os
import sys

FREE = {'opus-medium-suborchestrator', 'g-suborchestrator', 'codex:codex-rescue', 'claude-code-guide', 'statusline-setup'}
# The Haiku forwarders to Sol, dropped 2026-10-05; sessions started before then still list them.
SOL = {'g-worker': 'build', 'g-explore': 'explore', 'g-research': 'research'}


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason': reason}}))


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    tool = event.get('tool_input') or {}
    kind = tool.get('subagent_type') or 'general-purpose'
    if kind in SOL:
        return deny(f"{kind} is retired. Run Sol directly as a background Bash call (run_in_background): "
                    f"`~/.local/bin/g-agent-call --role {SOL[kind]} --block --label '<job>' --workdir <repo> "
                    f"<<'G_BRIEF_EOF'` then the self-contained brief, then `G_BRIEF_EOF`. You are notified when it ends.")
    if kind in FREE:
        return
    if any(l.strip().lower().startswith(('operator approved:', 'gabriel approved:')) for l in (tool.get('prompt') or '').splitlines()):
        return
    path = os.path.join(os.path.dirname(os.path.realpath(os.path.expanduser('~/.agents/session-context.py'))), 'temperature.py')
    spec = importlib.util.spec_from_file_location('temperature', path)
    temp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(temp)
    t = temp.claude()
    if not t or t[0] not in temp.GATED:
        return
    w, h, window = t
    reason = (f"Claude is {w} x{h:.2f} ({window}): Claude-tier subagents need the operator's OK. "
              f"Run Sol instead (background Bash: `g-agent-call --role build|explore|research --block "
              f"--label '<job>' --workdir <repo>` with the brief on stdin), or ask the operator in one line: "
              f"the job, the tier ({kind}) and why Sol won't do. Ask whenever you think the job "
              f"needs it. If they agree, add a line 'Operator approved: <tier> for <job>' to the "
              f"prompt and retry. A subagent cannot ask: carry an approval line from your own "
              f"brief, or put the request in your final report.")
    deny(reason)


main()
