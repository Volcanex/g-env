#!/usr/bin/env python3
"""Vendor-neutral G-System session context. Local facts remain live files.

Every section renders whole and is short by design; nothing is cut to fit a
budget. Reference material lives behind pointers in the sources themselves.
TARGET_CHARS is a soft guard enforced by the tests, not at runtime.
"""
import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import time
from zoneinfo import ZoneInfo

TARGET_CHARS = 7200
MAX_SOURCE_CHARS = 100000
MODELS_FILE = Path(__file__).resolve().parent / 'models.json'
MODELS_STALE_DAYS = 45
SESSION_START, SESSION_END = '<!-- SESSION:START -->', '<!-- SESSION:END -->'
TITLES = {'host': 'Host', 'shared': 'Shared policy', 'conventions': 'Conventions', 'topology': 'Topology', 'models': 'Roster'}
MAX_PULSE_ALERTS = 5
MAX_BRAIN_NOTES = 4
CONDENSE_OUTDATED, CONDENSE_BACKLOG = 10, 50  # below both, condensation is not worth a session's attention
STATUS_STALE_MINUTES = 15  # brain-status runs every 5; three missed runs means the board itself is stuck
PRIORITY = re.compile(r'\b(p0|p1|urgent|warning|alert|broken|overdue|safety|permissions|assent)\b', re.I)


def expand(path, seen=None, depth=0, local_first=False):
    """Expand standalone Markdown imports, once, outside fenced examples."""
    seen = set() if seen is None else seen
    try:
        path = Path(path).expanduser().resolve()
        if path in seen:
            return ''
        if depth > 10 or len(seen) >= 50:
            return f'Context imports truncated: read {path} directly.\n'
        seen.add(path)
        with path.open() as stream:
            source = stream.read(MAX_SOURCE_CHARS + 1)
    except (OSError, RuntimeError, UnicodeError) as error:
        return f'Context unavailable: {path} ({type(error).__name__})\n'
    parts = [f'\n<!-- Live source: {path} -->\n']
    imports = []
    fence = None
    for line in source[:MAX_SOURCE_CHARS].splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
        match = re.fullmatch(r'@(.+\.md)\s*', line.strip()) if fence is None else None
        if match:
            target = Path(match.group(1)).expanduser()
            (imports if local_first else parts).append(expand(target if target.is_absolute() else path.parent / target, seen, depth + 1))
        else:
            parts.append(line)
    if len(source) > MAX_SOURCE_CHARS:
        parts.append(f'\n[Source truncated; read {path} directly.]\n')
    return ''.join(parts + imports)


def session_text(text):
    """A source's session excerpt, or all of it; never a cut-down version.

    A source that marks SESSION blocks is injected as exactly those blocks (its
    author chose them; the rest is reference read on demand). A source without
    markers is injected whole, so forgetting the markers can only cost length.
    """
    text = re.sub(r'^<!-- Live source: .* -->\n', '', text, flags=re.M)
    blocks = re.findall(re.escape(SESSION_START) + r'\n?(.*?)' + re.escape(SESSION_END), text, flags=re.S)
    excerpt = bool(blocks)
    if excerpt:
        text = '\n'.join(blocks)
    text = re.sub(r'\A\s*# [^\n]*\n', '', text)  # the file's own title repeats the section label
    text = re.sub(r'^(#{2,5}) ', r'#\1 ', text, flags=re.M)  # nest source headings under the section label
    text = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text)  # runs of blank lines carry nothing
    return text.strip(), excerpt


def source_section(name, source):
    label = f'## {TITLES[name]}'
    if not isinstance(source, str):
        return f'{label}\nContext unavailable: no {name} source configured; run genv context installation.\n'
    text, excerpt = session_text(expand(source, local_first=name == 'host'))
    where = f' ({source}; rest on demand)' if excerpt else ''
    return f'{label}{where}\n{text}\n'


def pulse_section(text):
    """Priority Pulse entries only; the page itself is read on demand."""
    heading = '## Pulse priority'
    if text.startswith('Unavailable'):
        return f'{heading}\n{text}\n'
    entries = [line for line in text.splitlines() if line.startswith('- ')]
    alerts = [line for line in entries if PRIORITY.search(line)]
    lines = alerts[:MAX_PULSE_ALERTS] or ['none flagged']
    if len(alerts) > MAX_PULSE_ALERTS:
        lines.append(f'+{len(alerts) - MAX_PULSE_ALERTS} more priority entries: read them with pulse_page.')
    lines.append(f'{len(entries)} entries in all: pulse_page (or /api/pulse/page) when current state matters.')
    return heading + '\n' + '\n'.join(lines) + '\n'


def brain_section(text):
    """One line of Brain orientation, housekeeping only when there is work to do.

    Parses /api/orient. If its shape is unrecognised the whole response is kept,
    so a format change costs length, never content.
    """
    heading = '## Brain'
    total = re.search(r'^(\d+) notes\b', text, re.M)
    if not total:
        return f'{heading}\n{text.strip()}\n'
    def count(pattern):
        found = re.search(pattern, text)
        return int(found.group(1)) if found else 0
    broken = count(r'(\d+) note\(s\) flagged broken')
    lines = [f"{total.group(1)} notes" + (f', {broken} flagged broken' if broken else '')
             + '. Retrieve only task-relevant notes; brain_init has the manual.']
    relevant = re.search(r'^\*\*(On `[^`]+`|For this task)\*\* \((\d+) notes', text, re.M)
    if relevant:
        slugs = [s + (' [BROKEN]' if b else '') for s, b in re.findall(r'^- `([^`]+)`( \[BROKEN\])?', text, re.M)]
        shown = ', '.join(f'`{s}`' for s in slugs[:MAX_BRAIN_NOTES])
        more = int(relevant.group(2)) - min(len(slugs), MAX_BRAIN_NOTES)
        lines.append(f'{relevant.group(1)}: {shown}' + (f' (+{more} more: /api/orient)' if more > 0 else ''))
    stale = count(r'(\d+) note\(s\) stand on a dependency')
    if stale:
        lines.append(f'Housekeeping: {stale} notes stand on a moved dependency (/api/stale).')
    backlog, outdated = count(r'(\d+) condensation candidate'), count(r'(\d+) possibly outdated')
    if outdated >= CONDENSE_OUTDATED or backlog >= CONDENSE_BACKLOG:
        lines.append(f'Housekeeping: {backlog} condensation candidates, {outdated} possibly outdated; '
                     'brain-condense when the operator asks or work is light.')
    return heading + '\n' + '\n'.join(lines) + '\n'


def health_section(text, now=None):
    """What the status board says is broken right now; nothing at all when it is green.

    Reflexes in brain-status have already tried a restart by the time a service
    shows here, so whatever remains needs an agent or the operator.
    """
    if text.startswith('Unavailable'):
        return ''
    heading = '## Health'
    try:
        board = json.loads(text)
        checked = dt.datetime.fromisoformat(board['checked_at'])
        rows = board.get('services', []) + board.get('dependencies', [])
    except (ValueError, KeyError, TypeError):
        return f'{heading}\nStatus board unreadable: /api/status.\n'
    lines = []
    age = ((now or dt.datetime.now(dt.timezone.utc)) - checked).total_seconds() / 60
    if age > STATUS_STALE_MINUTES:
        lines.append(f'Status board last ran {age:.0f} min ago: brain-status.timer is stuck.')
    for state in ('down', 'warn'):
        bad = [f"{r['id']} ({r.get('detail', '')[:60]})" for r in rows if r.get('state') == state]
        if bad:
            lines.append(f'{state.title()}: ' + ', '.join(bad) + '.')
    if not lines:
        return ''
    lines.append('Tell the operator at the start; fix what is in your scope.')
    return heading + '\n' + '\n'.join(lines) + '\n'


def public_brain(environ=os.environ):
    """BRAIN_PUBLIC, else https:// and the brain-api domain in the instance manifest."""
    if environ.get('BRAIN_PUBLIC'):
        return environ['BRAIN_PUBLIC']
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from instance import service_url
    return service_url('brain-api', environ)


def fetch(path, *, bases=None):
    bases = bases or tuple(b for b in (os.environ.get('BRAIN_LOCAL', 'http://127.0.0.1:8766'),
                                       public_brain()) if b)
    for base, timeout in zip(bases, (2, 4)):
        try:
            with urllib.request.urlopen(base.rstrip('/') + path, timeout=timeout) as response:
                data = response.read(32001)
            text = data[:32000].decode('utf-8', errors='replace')
            if text.strip():
                if len(data) > 32000:
                    text += '\n[Brain response truncated at 32000 bytes.]'
                return text
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return 'Unavailable: Brain HTTP endpoint could not be read; use Brain tools if connected.'


def _span(seconds):
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, minutes = divmod(rest // 60, 60)
    return f'{days}d{hours}h' if days else f'{hours}h{minutes:02d}m'


def _age(seconds):
    return f'{round(seconds / 86400)}d' if seconds >= 172800 else f'{round(seconds / 3600)}h' if seconds >= 5400 else f'{round(seconds / 60)}m'


def pace_section(home, now=None):
    """Compute pace as data, rendered from the cache `compute-pace.timer` writes.

    Never raises: a missing, stale beyond use or malformed cache is one line.
    """
    heading = '## Compute pace'
    unavailable = f'{heading}\npace unavailable (no usable ~/.cache/genv/compute-pace.json)\n'
    now = time.time() if now is None else now
    try:
        data = json.loads((Path(home) / '.cache/genv/compute-pace.json').read_text())
        computed = dt.datetime.fromisoformat(data['computed_at']).timestamp()
        pace_age = max(0, now - computed)
        if pace_age > 7 * 86400:
            return unavailable
        london = ZoneInfo('Europe/London')
        lines = []
        samples = set()
        for q in data['quotas']:
            reset = dt.datetime.fromisoformat(q['resets_at'])
            when = reset.astimezone(london).strftime('%H:%M' if q['window_hours'] <= 24 else '%a %H:%M')
            line = f"{q['id']}: {q['remaining_pct']:g}% left, resets {when} ({_span(reset.timestamp() - now)})"
            known = q.get('known_reset')
            known_text = ''
            if known:
                point = dt.datetime.fromisoformat(known['at']).astimezone(london)
                known_text = f", known reset {point:%a %d %b %H:%M}"
                if known.get('note'):
                    known_text += f" ({known['note']})"
            line += known_text
            burn = q.get('burn_pct_per_work_hour')
            if burn is None:
                line = f"{q['id']}: {q['remaining_pct']:g}% left"
                if q['window_hours'] > 24:
                    line += f', resets {when}'
                line += known_text
                sample = (q['provider'], q['used_pct'])
                if sample not in samples:
                    line += f"; used {q['used_pct']:g}% so far (<3 work-h sampled)"
                    samples.add(sample)
            else:
                safe = q.get('safe_burn_pct_per_work_hour')
                line += f"; burn {burn:.1f}%/work-h" + (f" vs safe {safe:.1f}%" if safe is not None else '')
                line += f" ({q['sampled_work_hours']:.0f} work-h sampled)"
                if q.get('runs_out_at'):
                    out = dt.datetime.fromisoformat(q['runs_out_at']).astimezone(london)
                    line += f" -> runs out ~{out:%a %H:%M} (~{q['work_hours_to_run_out']:.0f} work-h)"
                else:
                    line += f" -> ~{q['projected_remaining_pct']:.0f}% left at reset"
            if q.get('reset_risk'):
                line += f" [reset risk: {q['reset_risk']}]"
            fetched = max(0, now - dt.datetime.fromisoformat(q['usage_fetched_at']).timestamp())
            if fetched > 1800:
                line += f' [usage {_age(fetched)} old]'
            lines.append(line)
        h = data['hours']
        if h.get('sources') == []:
            line = 'work: no active hours (no session collector on this host)'
        else:
            line = (f"work ({'+'.join(h.get('sources') or ['claude'])}, {','.join(h['hosts'])}): {h['today']:.1f}h today"
                    + f"; usual pace {h.get('usual_pace_days', 28)}d avg {h.get('usual_pace_daily', h['avg_daily_28d']):.1f}h/day")
            if h.get('missing_hosts'):
                line += f", no data from {','.join(h['missing_hosts'])}"
        lines.append(line)
        o = data.get('openrouter')
        if o and o.get('remaining_usd') is not None:
            lines.append(f"openrouter: {'-' if o['remaining_usd'] < 0 else ''}${abs(o['remaining_usd']):.2f} left of ${o['limit_usd']:.0f}")
        if pace_age > 1800:
            lines.append(f'[pace data {_age(pace_age)} old]')
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import value
        model = value.snapshot(home=home, now=now)
        prices = model['prices']
        picks = {}
        for role, cells in model['grid'].items():
            picks.setdefault(cells['normal'], []).append(role)
        lines[:0] = ['price: ' + ', '.join(f"{name} {p['lambda']:.1f} ({p['temp'].title()}"
                                          + (f", r {p['r']:.1f}" if name == 'claude' else '') + ')'
                                          for name, p in prices.items()),
                     'normal picks: ' + ', '.join(f"{'/'.join(roles)} {point}" for point, roles in picks.items()),
                     'Orchestrators: set --stakes per job (routine to critical), --role design for UI or taste '
                     'work; past critical, ask the operator for Burn+Fast on the compute page.']
        return heading + '\n' + '\n'.join(lines) + '\n'
    except Exception:
        return unavailable


EFFORT_SHORT = {'medium': 'med'}


def _cost(usd):
    """Two significant figures, no leading zero: 0.42 -> .42, 1.34 -> 1.3, 5.98 -> 6."""
    text = f'{usd:.2g}'
    return text[1:] if text.startswith('0.') else text


def models_section(path=MODELS_FILE, today=None):
    """Compact model roster from the dated data file; one line if it is unusable."""
    heading = '## Roster'
    try:
        data = json.loads(Path(path).read_text())
        as_of = dt.date.fromisoformat(data['as_of'])
        age = ((today or dt.date.today()) - as_of).days
        stale = ' (stale, refresh)' if age > MODELS_STALE_DAYS else ''
        lines, group = [], None
        for m in data['models']:
            prefix = f"{m['access']}: " if m['access'] != group else ''
            group = m['access']
            scores = ' '.join(f'{k}{v:g}' if k == 'AA' else f'{k} {v:g}' for k, v in (m.get('scores') or {}).items())
            note = f"; {m['note']}" if m.get('note') else ''
            lines.append(prefix + f"{m['id']} " + (f'{scores}, ' if scores else '') + (f"{m['cost']}: " if m.get('cost') else '') + f"{m['role']}{note}")
        ladder = data.get('effort_ladder')
        if ladder:
            cells = [f"{c['model'].split('-')[0]}-{EFFORT_SHORT.get(c['effort'], c['effort'])} {_cost(c['cost_usd'])}/{c['score']:g}/{c['ttft_s']:g}s"
                               for c in ladder]
            line = f"effort ladder (AA $/task/score/ttft): {' → '.join(cells)}"
            lines.append(line if len(line) <= 220 else line.replace(' → ', '→'))  # the 220-char cap
        swe = data.get('swe_bench_pro')
        if swe:
            cells = ', '.join(f"{c['effort']} {_cost(c['cost_per_solved_usd'])} {c['solved_pct']:g}%" for c in swe['cells'])
            lines.append(f"SWE-bench Pro {swe['model']} $/solved: {cells}")
        return f'{heading} (as of {as_of}{stale}; AA index, TB4 bench)\n' + '\n'.join(lines) + '\n'
    except Exception:
        return f'{heading}\nmodel roster unavailable (context/models.json missing or unreadable)\n'


def guarded(name, build):
    """A section that fails renders as one line naming what to read instead."""
    try:
        return build()
    except Exception as error:
        return f'## {TITLES.get(name, name.title())}\n{name} unavailable ({type(error).__name__}); read its source directly.\n'


def context(home, cwd, pulse=True, project='', task=''):
    root = Path(home) / '.agents'
    try:
        config = json.loads((root / 'context.json').read_text())
        sources = config.get('sources', {})
        if not isinstance(sources, dict):
            sources = {}
    except (OSError, ValueError, AttributeError):
        sources = {}
    sources = {'host': str(root / 'host.md'), **sources}
    parts = ['# G-System session context\n']
    for name in ('host', 'shared', 'conventions', 'topology'):
        parts.append(guarded(name, lambda: source_section(name, sources.get(name))))
    parts.append(guarded('pace', lambda: pace_section(home)))
    parts.append(guarded('models', models_section))
    if pulse:
        query = urllib.parse.urlencode({'cwd': cwd[:4096], 'project': project[:100], 'task': task[:1000]})
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            pulse_future = pool.submit(fetch, '/api/pulse/page')
            orientation_future = pool.submit(fetch, '/api/orient?' + query)
            status_future = pool.submit(fetch, '/api/status')
            pulse_text = pulse_future.result()
            orientation_text = orientation_future.result()
            status_text = status_future.result()
        parts.append(guarded('health', lambda: health_section(status_text)))
        parts.append(guarded('pulse', lambda: pulse_section(pulse_text)))
        parts.append(guarded('brain', lambda: brain_section(orientation_text)))
    return '\n'.join(part for part in parts if part)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hook', action='store_true', help='Read a SessionStart event and emit hook JSON')
    parser.add_argument('--no-pulse', action='store_true', help='Read local instructions only')
    parser.add_argument('--cwd', default=os.getcwd())
    parser.add_argument('--project', default=os.environ.get('G_SYSTEM_PROJECT', ''))
    parser.add_argument('--task', default=os.environ.get('G_SYSTEM_TASK', ''))
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin) if args.hook else {}
    except (ValueError, OSError):
        event = {}
    if not isinstance(event, dict):
        event = {}
    cwd = event.get('cwd') if isinstance(event.get('cwd'), str) else args.cwd
    output = context(Path.home(), cwd, not args.no_pulse, args.project, args.task)
    if args.hook:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': output}}))
    else:
        print(output)


if __name__ == '__main__':
    main()
