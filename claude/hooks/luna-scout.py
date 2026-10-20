#!/usr/bin/env python3
"""Start Luna with a substantial subagent; inject rare pointers at failures."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

SKIP = {'claude-code-guide', 'statusline-setup'}
ERROR = re.compile(r'Traceback|\bError:|\bFAILED\b|command not found', re.I)
EXIT = re.compile(r'(?:exit[ _]?(?:code|status)|returncode)\s*[:=]?\s*(-?\d+)', re.I)
SCOUT = Path(__file__).resolve().parents[2] / 'bin/g-scout'


def append(path, row):
    with path.open('a') as stream:
        stream.write(json.dumps(row) + '\n')


def debug(directory, event, reason, signal=None, eligible=None):
    if os.environ.get('G_SCOUT_DEBUG') == '1':
        directory.mkdir(parents=True, exist_ok=True)
        append(directory / 'hook.log', {'ts': time.time(), 'event': event.get('hook_event_name'),
               'agent_id': event.get('agent_id'), 'signal': signal, 'eligible': eligible or [], 'reason': reason})


def read_state(directory):
    return json.loads((directory / 'state.json').read_text())


def write_state(directory, state):
    path = directory / 'state.tmp'
    path.write_text(json.dumps(state))
    path.replace(directory / 'state.json')


def transcript_path(event):
    return Path(event['transcript_path']).parent / event['session_id'] / 'subagents' / f"agent-{event['agent_id']}.jsonl"


def read_brief(transcript):
    for attempt in range(6):
        try:
            with transcript.open() as stream:
                row = json.loads(stream.readline())
            content = (row.get('message') or {}).get('content')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return '\n'.join(item['text'] for item in content if isinstance(item, dict)
                                 and item.get('type') == 'text' and isinstance(item.get('text'), str))
        except (OSError, ValueError):
            pass
        if attempt < 5:
            time.sleep(0.02)
    return ''


def start(directory, event):
    root = directory.parent
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink() and time.time() - child.stat().st_mtime > 2 * 86400:
            shutil.rmtree(child, ignore_errors=True)
    if event.get('agent_type') in SKIP:
        return debug(directory, event, 'skip type')
    directory.mkdir(parents=True, exist_ok=True)
    transcript = transcript_path(event)
    stop = directory / 'stop'
    if (directory / 'brief.txt').exists():
        if not stop.exists():
            return debug(directory, event, 'already started')
        # A resumed subagent starts again under the same id; its scout had exited, its budget carries on.
        stop.unlink()
        spawn(directory, transcript)
        return debug(directory, event, 'resumed')
    (directory / 'pending.json').write_text(json.dumps({'transcript': str(transcript), 'agent_type': event.get('agent_type')}))
    brief = read_brief(transcript)
    if not brief:
        # The transcript is often written just after SubagentStart; the first tool calls retry.
        return debug(directory, event, 'brief not written yet')
    return launch(directory, event, transcript, brief)


def launch(directory, event, transcript, brief):
    (directory / 'pending.json').unlink(missing_ok=True)
    if len(brief) < 300:
        (directory / 'skip').touch()
        return debug(directory, event, 'short brief')
    (directory / 'brief.txt').write_text(brief[:6000])
    write_state(directory, {'started': time.time(), 'delivered': [], 'count': 0, 'last_injection': 0,
                            'history': [], 'commands': {}})
    spawn(directory, transcript)
    debug(directory, event, 'spawned')


def spawn(directory, transcript):
    with (directory / 'scout.log').open('a') as stream:
        subprocess.Popen([str(SCOUT), '--dir', str(directory), '--brief-file', str(directory / 'brief.txt'),
                          '--transcript', str(transcript)], stdin=subprocess.DEVNULL, stdout=stream,
                         stderr=stream, start_new_session=True)


def struggle(directory, event, state, now):
    tool = event.get('tool_name', '')
    response = event.get('tool_response', '')
    text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
    match = ERROR.search(text) if tool == 'Bash' else None
    failed = bool(match) or (isinstance(response, dict) and bool(response.get('is_error') or response.get('error')))
    if tool == 'Bash':
        if isinstance(response, dict):
            failed = failed or any(response.get(key) not in (None, 0, '0')
                                   for key in ('exit_code', 'exitCode', 'returncode'))
        failed = failed or any(int(m.group(1)) != 0 for m in EXIT.finditer(text))
    command = (event.get('tool_input') or {}).get('command', '') if tool == 'Bash' else ''
    repeated = False
    if command:
        digest = hashlib.sha256(command.encode()).hexdigest()
        commands = state.setdefault('commands', {})
        commands[digest] = commands.get(digest, 0) + 1
        repeated = commands[digest] == 3
    history = (state.get('history', []) + [failed])[-8:]
    state['history'] = history
    excerpt = ' '.join(text[match.start() if match else 0:].split())[:300]
    signal = None
    if failed or repeated or sum(history) >= 3:
        if not failed:
            excerpt = 'command repeated 3 times: ' + ' '.join(command.split()) if repeated else '3 errors in last 8 tool calls'
        signal = {'tool': tool, 'error': excerpt[:300], 'ts': now}
        append(directory / 'signals.jsonl', signal)
    return failed, bool(signal), signal


def pointer(row):
    url, what = row.get('url'), row.get('what', 'reference')
    if not isinstance(url, str) or not isinstance(what, str) or any(c.isspace() for c in url):
        return ''
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return ''
    except ValueError:
        return ''
    if len(url) > 300:
        return ''

    def clean(text, limit):
        return ' '.join(text.split()).replace('\u2014', '-').replace('\u00b7', '-')[:limit] if isinstance(text, str) else ''
    # A bare link was opened 5 times in 96 (2026-10-20), so the finding itself is delivered.
    parts = [f"Luna boost, web research worth {row.get('score')}/5: {clean(what, 80) or 'reference'}."]
    content, why = clean(row.get('content'), 600), clean(row.get('why'), 200)
    if content:
        parts.append(content)
    if why:
        parts.append(f'Why it matters: {why}')
    parts.append(f'Source: {url}')
    return ' '.join(parts)


def eligible_findings(directory, state, now, struggling):
    eligible = []
    try:
        with (directory / 'findings.jsonl').open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        continue
                    score = row.get('score')
                    if type(score) is not int or not (3 if struggling else 4) <= score <= 5:
                        continue
                    # Early on only a strong find interrupts: 4 if the agent is already failing, else 5.
                    if now - state['started'] < 60 and score < (4 if struggling else 5):
                        continue
                    if row.get('url') in state['delivered'] or not pointer(row):
                        continue
                    eligible.append(row)
                except (ValueError, TypeError):
                    pass
    except FileNotFoundError:
        pass
    return sorted(eligible, key=lambda row: row['score'], reverse=True)


def post_tool(directory, event):
    pending = directory / 'pending.json'
    if not (directory / 'state.json').exists() and pending.exists():
        transcript = Path(json.loads(pending.read_text())['transcript'])
        brief = read_brief(transcript)
        if brief:
            launch(directory, event, transcript, brief)
        return debug(directory, event, 'started late' if brief else 'brief not written yet')
    if not (directory / 'state.json').exists() or (directory / 'stop').exists():
        return debug(directory, event, 'inactive')
    with (directory / 'state.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return debug(directory, event, 'state busy')
        state, now = read_state(directory), time.time()
        failed, struggling, signal = struggle(directory, event, state, now)
        eligible = eligible_findings(directory, state, now, struggling)
        urls = [row['url'] for row in eligible]
        # A failure is the natural seam; on success only a top find lands, and never mid-edit.
        if not failed:
            eligible = [row for row in eligible if row['score'] == 5]
        editing = event.get('tool_name') in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')
        reason = ('mid-edit' if not failed and editing else 'budget exhausted' if state['count'] >= 6
                  else 'injection spacing' if state['count'] and now - state['last_injection'] < 120
                  else 'no eligible findings' if not eligible else 'injected')
        if reason == 'injected':
            row = eligible[0]
            state['delivered'].append(row['url'])
            state['count'] += 1
            state['last_injection'] = now
        write_state(directory, state)
        debug(directory, event, reason, signal, urls)
        if reason == 'injected':
            append(directory.parent.parent / 'boost.jsonl', {'ts': now, 'agent_type': event.get('agent_type'),
                   'score': row['score'], 'url': row['url']})
            print(json.dumps({'hookSpecificOutput': {'hookEventName': event['hook_event_name'], 'additionalContext': pointer(row)}}))


def main():
    directory = event = None
    try:
        event = json.load(sys.stdin)
        agent_id = event.get('agent_id', '')
        if not isinstance(agent_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', agent_id):
            return
        directory = Path.home() / '.cache/genv/scout' / agent_id
        kind = event.get('hook_event_name')
        # OFF switches every session at once, which an env var set at launch cannot do.
        if (os.environ.get('G_SCOUT') == '0' or (directory.parent / 'OFF').exists()) and kind != 'SubagentStop':
            return debug(directory, event, 'disabled')
        if kind == 'SubagentStart':
            start(directory, event)
        elif kind == 'PostToolUse':
            post_tool(directory, event)
        elif kind == 'PostToolUseFailure':
            # Failed calls skip PostToolUse and carry the error text instead of a response.
            post_tool(directory, {**event, 'tool_response': {'is_error': True, 'error': str(event.get('error', ''))}})
        elif kind == 'SubagentStop':
            if directory.exists():
                (directory / 'stop').touch()
            debug(directory, event, 'stopped')
        else:
            debug(directory, event, 'ignored event')
    except Exception as error:
        if directory is not None:
            try:
                debug(directory, event, 'error: ' + type(error).__name__)
            except Exception:
                pass


if __name__ == '__main__':
    main()
