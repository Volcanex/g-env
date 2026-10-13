"""Quota temperature per provider (Claude, Codex) from compute-pace.

The scale is safe burn over actual burn in work hours: x1 is on pace, x3 means
spend could triple before the reset, x0.4 means it runs out well before. Windows
without a usable work-hour sample fall back to wall-clock headroom (what the
statusline shows). The tightest window of a provider decides.

The operator can override each provider from gcommand's /compute page: a
temperature (what the system acts on, whatever was measured) and a speed (FAST
trades effort for latency, SLOW spends latency on quality), or from a session with
`g-temp`. Overrides live in ~/.cache/genv/compute-mode.json and lapse at the end of
the London day unless set for a number of hours.

Claude is the protected quota, so its measured BURN also needs the weekly window's
wall-clock headroom at x5 or more (about 75% left two thirds through the week),
else it reads HOT: work-hour pace swings high after a few quiet hours and would
spend quota the busy days still need. Near the reset the wall clock gets there on
its own; `g-temp claude burn` gets there any time.

EBURN sits above BURN: the weekly window resets (at the earlier of the known and
reported reset) within EBURN_HOURS with more than EBURN_LEFT percent left, so what
is left is about to be wasted, or holds more than EBURN_ABOVE percent. Spend it on
anything useful, as wide as possible. `g-temp codex eburn` forces it.
"""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

TEMPERATURES = ((2.5, 'BURN'), (1.6, 'HOT'), (1.15, 'WARM'), (0.85, 'COOL'), (0.5, 'COLD'), (None, 'FREEZING'))
GATED = {'COLD', 'FREEZING'}
WORDS = ('EBURN',) + tuple(w for _, w in TEMPERATURES)
EBURN_HOURS = 12
EBURN_LEFT = 10.0
# Above this much left a provider is EBURN outright. Codex is cancelled at the end of October 2026.
EBURN_ABOVE = {'codex': 50.0}
SPEEDS = ('FAST', 'NORMAL', 'SLOW')
PROVIDERS = ('claude', 'codex')
CLAUDE_BURN_WALL = 5.0
LONDON = ZoneInfo('Europe/London')


def cache(home=None):
    return Path(home or Path.home()) / '.cache/genv'


def end_of_day(now):
    local = now.astimezone(LONDON)
    nxt = dt.datetime.combine(local.date() + dt.timedelta(days=1), dt.time(), LONDON)
    return nxt.astimezone(dt.timezone.utc)


def overrides(home=None, now=None, path=None):
    """Live overrides per provider: {name: {'temp', 'speed', 'set_at', 'expires_at'}}; lapsed ones are dropped."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        data = json.loads(Path(path or cache(home) / 'compute-mode.json').read_text())
    except Exception:
        return {}
    live = {}
    for name in PROVIDERS:
        o = data.get(name) or {}
        try:
            if dt.datetime.fromisoformat(o['expires_at']) <= now:
                continue
        except Exception:
            continue
        o = {k: o.get(k) for k in ('temp', 'speed', 'set_at', 'expires_at', 'by')}
        if o['temp'] not in WORDS:
            o['temp'] = None
        if o['speed'] not in SPEEDS:
            o['speed'] = None
        if o['temp'] or o['speed']:
            live[name] = o
    return live


def set_override(name, temp=None, speed=None, by='gcommand', home=None, now=None, path=None, hours=None):
    """Set or clear (both None) one provider's override for `hours`, else until the end of the London day."""
    if name not in PROVIDERS or (temp and temp not in WORDS) or (speed and speed not in SPEEDS):
        raise ValueError('bad override')
    now = now or dt.datetime.now(dt.timezone.utc)
    target = Path(path or cache(home) / 'compute-mode.json')
    try:
        data = json.loads(target.read_text())
    except Exception:
        data = {}
    data[name] = {'temp': temp, 'speed': None if speed == 'NORMAL' else speed, 'by': by,
                  'set_at': now.isoformat(),
                  'expires_at': (now + dt.timedelta(hours=hours) if hours else end_of_day(now)).isoformat()} if (temp or speed) else None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=1) + '\n')
    tmp.replace(target)
    return overrides(home, now, target)


def headroom(used, seconds_left, width):
    if used is None or seconds_left is None or seconds_left <= 0:
        return None
    left = min(seconds_left, width)
    elapsed = max(width - left, 0.10 * width)
    if used <= 0:
        return 99.0
    return (max(0.0, 100.0 - used) / left) / (used / elapsed)


def word(h):
    for threshold, w in TEMPERATURES:
        if threshold is None or h >= threshold:
            return w


def ratio(q, now):
    burn, safe = q.get('burn_pct_per_work_hour'), q.get('safe_burn_pct_per_work_hour')
    if burn and safe is not None and not q.get('low_sample'):
        return safe / burn
    left = (dt.datetime.fromisoformat(q.get('effective_resets_at') or q['resets_at']) - now).total_seconds()
    return headroom(q.get('used_pct'), left, q['window_hours'] * 3600)


def wall(quotas, name, now):
    """Wall-clock headroom of the provider's weekly window (0 when unknown)."""
    for q in quotas:
        if q.get('provider') == name and q.get('window_hours', 0) >= 168:
            left = (dt.datetime.fromisoformat(q.get('effective_resets_at') or q['resets_at']) - now).total_seconds()
            return headroom(q.get('used_pct'), left, q['window_hours'] * 3600) or 0
    return 0


def expiring(quotas, name, now):
    """True when the weekly window resets within EBURN_HOURS with more than EBURN_LEFT% left, or holds more than EBURN_ABOVE."""
    for q in quotas:
        if q.get('provider') == name and q.get('window_hours', 0) >= 168 and q.get('remaining_pct') is not None:
            left = (dt.datetime.fromisoformat(q.get('effective_resets_at') or q['resets_at']) - now).total_seconds()
            if 0 < left <= EBURN_HOURS * 3600 and q['remaining_pct'] > EBURN_LEFT:
                return True
            if q['remaining_pct'] > EBURN_ABOVE.get(name, 100):
                return True
    return False


def measured(name, home=None, now=None, max_age=3 * 3600, path=None):
    """(word, ratio, window id) as measured, or None when pace data is missing or stale."""
    try:
        data = json.loads(Path(path or cache(home) / 'compute-pace.json').read_text())
        now = now or dt.datetime.now(dt.timezone.utc)
        if (now - dt.datetime.fromisoformat(data['computed_at'])).total_seconds() > max_age:
            return None
        rooms = {}
        for q in data['quotas']:
            if q.get('provider') == name:
                r = ratio(q, now)
                if r is not None:
                    rooms[q['id']] = r
        if not rooms:
            return None
        binding = min(rooms, key=rooms.get)
        w = word(rooms[binding])
        if name == 'claude' and w == 'BURN' and wall(data['quotas'], name, now) < CLAUDE_BURN_WALL:
            w = 'HOT'
        if expiring(data['quotas'], name, now):
            w = 'EBURN'
        return w, rooms[binding], binding
    except Exception:
        return None


def state(name, home=None, now=None, path=None, mode_path=None):
    """What the system acts on for one provider: measured temperature, override, speed."""
    m = measured(name, home, now, path=path)
    o = overrides(home, now, mode_path).get(name, {})
    return {'provider': name, 'measured': m[0] if m else None, 'ratio': m[1] if m else None,
            'window': m[2] if m else None, 'temp': o.get('temp') or (m[0] if m else None),
            'temp_override': o.get('temp'), 'speed': o.get('speed') or 'NORMAL',
            'expires_at': o.get('expires_at')}


def provider(name, home=None, now=None, path=None, mode_path=None):
    """(word, ratio, window id) the system acts on: the override when set, else measured."""
    s = state(name, home, now, path, mode_path)
    if not s['temp']:
        return None
    return s['temp'], s['ratio'] if s['ratio'] is not None else 0.0, s['window'] or 'override'


def claude(home=None, now=None):
    return provider('claude', home, now)


LEVELS = ('low', 'medium', 'high', 'xhigh')


def codex_effort(base, home=None, now=None, path=None, mode_path=None):
    """Shift a Codex role's effort with Codex's temperature and speed: spare Codex buys quality.

    EBURN lifts two levels, BURN one, up to xhigh (Sol max, AA 52 against 48 at medium), HOT lifts
    one up to high, COLD and FREEZING drop one. SLOW adds a level; FAST caps at medium,
    since Sol high takes ~50s to its first token against ~5s at medium.
    """
    if base not in LEVELS:
        return base
    s = state('codex', home, now, path, mode_path)
    i = LEVELS.index(base)
    if s['temp'] == 'EBURN':
        i += 2
    elif s['temp'] == 'BURN':
        i += 1
    elif s['temp'] == 'HOT':
        i = max(i, min(i + 1, LEVELS.index('high')))
    elif s['temp'] in GATED:
        i -= 1
    if s['speed'] == 'SLOW':
        i += 1
    elif s['speed'] == 'FAST':
        i = min(i, LEVELS.index('medium'))
    return LEVELS[max(0, min(len(LEVELS) - 1, i))]
