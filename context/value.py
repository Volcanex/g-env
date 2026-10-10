"""Quality less quota price and latency, shared by agents and session context."""
import datetime as dt
import json
import math
from pathlib import Path

import temperature

HERE = Path(__file__).resolve().parent
BASE = {'claude': 9.0, 'codex': 3.3}
RESERVE = {'claude': 15, 'codex': 0}
RATIOS = {'BURN': 0.3, 'HOT': 0.5, 'WARM': 0.75, 'COOL': 1.0, 'COLD': 1.5, 'FREEZING': 2.5}
STAKES = {'routine': 0.8, 'normal': 1.0, 'important': 1.4, 'critical': 2.0}
FAMILIES = {'opus-5.5': 'opus', 'sonnet-5.5': 'sonnet', 'gpt-6.1-sol': 'sol',
            'gpt-6-luna': 'luna', 'gpt-6-astra': 'astra'}


def tastes(data):
    """Design Arena Elo beyond what AA predicts, in AA points: the residual of a straight-line fit of DA on AA."""
    rated = [(m['id'], m['scores']['AA'], m['scores']['DA']) for m in data.get('models', [])
             if 'AA' in m.get('scores', {}) and 'DA' in m.get('scores', {})]
    if len(rated) < 3:
        return {}
    mx = sum(i[1] for i in rated) / len(rated)
    my = sum(i[2] for i in rated) / len(rated)
    slope = sum((i[1] - mx) * (i[2] - my) for i in rated) / sum((i[1] - mx) ** 2 for i in rated)
    return {i[0]: round((i[2] - my - slope * (i[1] - mx)) / slope, 1) for i in rated}


def points(models_path=None):
    data = json.loads(Path(models_path or HERE / 'models.json').read_text())
    taste = tastes(data)
    result = []
    for provider, ladder in (('claude', 'effort_ladder'), ('codex', 'codex_ladder')):
        for row in data[ladder]:
            if provider == 'codex' and row['effort'] in ('none', 'max'):
                continue
            family = FAMILIES[row['model']]
            model = family if provider == 'claude' else row['model']
            result.append({'id': f"{family}-{row['effort']}", 'provider': provider,
                           'spec': f'{provider}:{model}', 'effort': row['effort'], 'q': row['score'],
                           'cost_usd': row['cost_usd'], 'ttft_s': row.get('ttft_s') or row.get('ttft_estimate_s') or 1,
                           'taste': taste.get(row['model'], 0)})
    references = {p: next(i['cost_usd'] for i in result if i['id'] == name)
                  for p, name in (('claude', 'opus-medium'), ('codex', 'sol-medium'))}
    for point in result:
        point['c'] = point['cost_usd'] / references[point['provider']]
    return result


def prices(home=None, now=None, pace_path=None, mode_path=None, overrides=None):
    """Quota price per provider. `overrides` previews {provider: {'temp', 'speed'}} over the live
    mode file without saving: a key given wins, None or NORMAL clears it back to measured or normal."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(now, (int, float)):
        now = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    pace_path = Path(pace_path or temperature.cache(home) / 'compute-pace.json')
    try:
        data = json.loads(pace_path.read_text())
        quotas = data['quotas']
    except (OSError, ValueError, KeyError, TypeError):
        quotas = []
    live = temperature.overrides(home, now, mode_path)
    for provider, preview in (overrides or {}).items():
        if provider not in BASE or not isinstance(preview, dict):
            raise ValueError(f'unknown provider {provider}')
        if preview.get('temp') not in (None, *RATIOS) or preview.get('speed') not in (None, *temperature.SPEEDS):
            raise ValueError(f'bad override for {provider}')
    result = {}
    for provider, base in BASE.items():
        reserve = RESERVE[provider]
        measured = temperature.measured(provider, home, now, path=pace_path)
        windows = (f'{provider} 7d', f'{provider} 5h')
        provider_rows = [q for q in quotas if q.get('provider') == provider and q.get('id') in windows]
        ratio = measured[1] if measured else None
        if measured and measured[2] not in windows:
            try:
                rooms = [temperature.ratio(q, now) for q in provider_rows]
                ratio = min((r for r in rooms if r is not None), default=None)
            except (ValueError, KeyError, TypeError):
                ratio = None
        override = {**live.get(provider, {}), **((overrides or {}).get(provider) or {})}
        rows = []
        for row in provider_rows:
            remaining = row.get('remaining_pct')
            burn, safe = row.get('burn_pct_per_work_hour'), row.get('safe_burn_pct_per_work_hour')
            if remaining is not None and remaining <= reserve:
                r = 10.0
            elif remaining is not None and burn is not None and safe is not None and not row.get('low_sample'):
                affordable = safe * (remaining - reserve) / max(remaining - 5, 1e-9)
                r = burn / affordable if affordable > 0 else 10.0 if burn > 0 else 0.0
            elif ratio is not None:
                r = 1 / max(ratio, 1e-9)
            else:
                continue
            rows.append((r, row['id']))
        r, source = max(rows) if rows else (1.0, 'default')
        temp = override.get('temp') or (measured[0] if measured else 'COOL')
        if override.get('temp'):
            r, source = RATIOS[temp], 'preview' if (overrides or {}).get(provider, {}).get('temp') else 'override'
        price = base * r ** 2
        mu = 2 * max(0, 1 - r)
        if override.get('speed') == 'FAST':
            mu += 3
        elif override.get('speed') == 'SLOW':
            price *= 0.6
            mu = 0.0
        result[provider] = {'r': r, 'lambda': price, 'mu': mu, 'reserve': reserve, 'temp': temp, 'source': source,
                            'speed': override.get('speed') or 'NORMAL'}
    return result


def _config(providers_path=None):
    return json.loads(Path(providers_path or HERE / 'providers.json').read_text())


def domains(providers_path=None):
    return list(_config(providers_path).get('value_domains', {}))


def values_taste(role, domain=None, providers_path=None):
    """Taste is only Claude's, so a role that weighs it is never gated off Claude."""
    config = _config(providers_path)
    rule = config.get('value_domains', {}).get(domain) if domain else None
    return bool(config['value'].get(rule['as_role'] if rule else role, {}).get('taste'))


def _inputs(models_path=None, providers_path=None, overrides=None, **paths):
    config = _config(providers_path)
    return points(models_path), prices(overrides=overrides, **paths), config['value'], config.get('value_domains', {})


def _apply_domain(role, stakes, claude_only, roles, domains, domain):
    """A domain ranks the job by another role's rule, e.g. ui work as design."""
    rule = domains.get(domain) if domain else None
    if not rule:
        return stakes, claude_only, roles
    return stakes, claude_only, {**roles, role: roles[rule['as_role']]}


def _q(point, rule):
    return point['q'] + rule.get('taste', 0) * point.get('taste', 0)


def _price(point, current):
    p = current[point['provider']]
    return p['lambda'] * point['c'] + p['mu'] * math.log10(max(point['ttft_s'], 1))


def _excluded(point, role, stakes, allow_claude, claude_only, current, rule, domain=None):
    """Why a point cannot run this job, or None when it can."""
    if point['id'].split('-')[0] not in rule['families']:
        return f'Not used for {domain} work' if domain else f'Not a {role} model'
    if point['q'] < rule['floor']:
        return f"Under the {domain or role} quality floor of {rule['floor']:g}"
    if claude_only and point['provider'] != 'claude':
        return 'This job runs on Claude only'
    if (point['provider'] == 'claude' and not (claude_only or allow_claude or stakes == 'critical' or rule.get('taste'))
            and current['claude']['temp'] in temperature.GATED):
        return f"Claude is {current['claude']['temp'].title()}, so it needs critical stakes or a Claude-only job"
    return None


def _rank(role, stakes, allow_claude, claude_only, all_points, current, roles):
    rule = roles[role]
    eligible = [i for i in all_points if not _excluded(i, role, stakes, allow_claude, claude_only, current, rule)]
    if not eligible:
        return []
    weight = rule.get('weight', 1.0)
    normal = max(eligible, key=lambda i: weight * _q(i, rule) - _price(i, current))
    # Stakes buy at most two rungs up the normal pick's own ladder; the other provider's interleaved points are no rungs.
    ladder = sorted((i for i in eligible if i['provider'] == normal['provider']), key=lambda i: (_q(i, rule), i['id']))
    ceiling = _q(ladder[min(ladder.index(normal) + 2, len(ladder) - 1)], rule)
    capped = [i for i in eligible if _q(i, rule) <= ceiling]
    return sorted(({**i, 'score': STAKES[stakes] * weight * _q(i, rule) - _price(i, current)} for i in capped),
                  key=lambda i: i['score'], reverse=True)


def rank(role, stakes='normal', allow_claude=False, claude_only=False, domain=None, overrides=None, **paths):
    all_points, current, roles, domains_ = _inputs(overrides=overrides, **paths)
    stakes, claude_only, roles = _apply_domain(role, stakes, claude_only, roles, domains_, domain)
    return _rank(role, stakes, allow_claude, claude_only, all_points, current, roles)


def pick(role, stakes='normal', allow_claude=False, claude_only=False, domain=None, overrides=None, **paths):
    all_points, current, roles, domains_ = inputs = _inputs(overrides=overrides, **paths)
    stakes, claude_only, roles = _apply_domain(role, stakes, claude_only, roles, domains_, domain)
    ranked = _rank(role, stakes, allow_claude, claude_only, all_points, current, roles)
    if not ranked:
        raise ValueError(f'no eligible points for {role}')
    best = ranked[0]
    why = f"{best['id']}: highest score for {role} at {stakes} stakes"
    if len(ranked) > 1:
        other = ranked[1]
        why += f"; {other['id']} scores {best['score'] - other['score']:.1f} lower after quota price and speed are counted"
    return {'role': role, 'stakes': stakes, 'domain': domain, 'pick': best, 'why': why + '.',
            'runners_up': ranked[1:3], 'prices': inputs[1]}


def explain(role, stakes='normal', domain=None, allow_claude=False, claude_only=False, overrides=None, **paths):
    """Every point for one job, eligible or not and why, beside the ranking the pick comes from."""
    if stakes not in STAKES:
        raise ValueError(f'unknown stakes {stakes}')
    all_points, current, roles, domains_ = _inputs(overrides=overrides, **paths)
    if role not in roles:
        raise ValueError(f'unknown role {role}')
    if domain and domain not in domains_:
        raise ValueError(f'unknown domain {domain}')
    acting, only, narrowed = _apply_domain(role, stakes, claude_only, roles, domains_, domain)
    rule = narrowed[role]
    ranked = _rank(role, acting, allow_claude, only, all_points, current, narrowed)
    scored = {i['id']: i for i in ranked}
    weight = rule.get('weight', 1.0)
    open_ = [i for i in all_points if not _excluded(i, role, acting, allow_claude, only, current, rule, domain)]
    normal = max(open_, key=lambda i: weight * _q(i, rule) - _price(i, current))['id'] if open_ else None
    result = []
    for i in all_points:
        reason = _excluded(i, role, acting, allow_claude, only, current, rule, domain)
        if reason is None and i['id'] not in scored:
            reason = f'More than two quality rungs above the normal pick, {normal}'
        result.append({'id': i['id'], 'provider': i['provider'], 'eligible': reason is None, 'reason': reason,
                       'sidelined': reason is not None and not _ruled_out(i, role, rule, only),
                       'score': STAKES[acting] * weight * _q(i, rule) - _price(i, current), 'parts': _parts(i, acting, rule, current)})
    pick_id = ranked[0]['id'] if ranked else None
    return {'role': role, 'stakes': acting, 'requested_stakes': stakes, 'domain': domain,
            'claude_only': only, 'weight': weight, 'taste_weight': rule.get('taste', 0), 'multiplier': STAKES[acting] * weight,
            'families': rule['families'], 'floor': rule['floor'], 'normal_pick': normal,
            'pick': pick_id, 'ranked': ranked, 'points': result, 'rivals': _rivals(pick_id, ranked, result),
            'prices': current}


def _parts(point, stakes, rule, current):
    """The score taken apart: score = quality + taste - price - speed."""
    p, gain = current[point['provider']], STAKES[stakes] * rule.get('weight', 1.0)
    return {'quality': gain * point['q'], 'taste': gain * rule.get('taste', 0) * point.get('taste', 0),
            'price': p['lambda'] * point['c'], 'speed': p['mu'] * math.log10(max(point['ttft_s'], 1))}


def _ruled_out(point, role, rule, claude_only):
    """Family, floor and Claude-only are the job's own terms; no temperature or stakes changes them."""
    return (point['id'].split('-')[0] not in rule['families'] or point['q'] < rule['floor']
            or (claude_only and point['provider'] != 'claude'))


def _rivals(pick_id, ranked, explained):
    """The runner-up, then each provider's best point, then any sidelined point that outscores those."""
    chosen = [ranked[1]['id']] if len(ranked) > 1 else []
    open_ = [i for i in explained if (i['eligible'] or i['sidelined']) and i['id'] != pick_id]
    for provider in BASE:
        mine = sorted((i for i in open_ if i['provider'] == provider), key=lambda i: i['score'], reverse=True)
        for i in [next((j for j in mine if j['eligible']), None), mine[0] if mine else None]:
            if i and i['id'] not in chosen:
                chosen.append(i['id'])
    return chosen[:4]


FLIP_DIMS = ('stakes', 'claude temp', 'codex temp', 'claude speed', 'codex speed')


def _flip_text(change):
    stakes = change.get('stakes')
    terms = []
    for provider in BASE:
        if change.get(f'{provider} temp'):
            terms.append(f"{provider.title()} is {change[f'{provider} temp'].title()}")
        if change.get(f'{provider} speed'):
            terms.append(f"{provider.title()} is on {change[f'{provider} speed'].title()}")
    return 'Wins' + (f' at {stakes} stakes' if stakes else '') + (' if ' + ' and '.join(terms) if terms else '')


def flips(role, stakes='normal', domain=None, allow_claude=False, claude_only=False, overrides=None, ids=None, **paths):
    """The smallest change that makes each rival the pick: every single change of stakes, either
    temperature or either speed first, nearest steps first, then every pair. None when nothing in reach flips it."""
    base = explain(role, stakes, domain, allow_claude, claude_only, overrides=overrides, **paths)
    ids = list(ids if ids is not None else base['rivals'])
    models_path, providers_path = paths.pop('models_path', None), paths.pop('providers_path', None)
    config = _config(providers_path)
    all_points = points(models_path)
    roles, domains_ = config['value'], config.get('value_domains', {})
    acting, only, narrowed = _apply_domain(role, base['requested_stakes'], claude_only, roles, domains_, domain)
    now = {'stakes': acting, **{f'{p} temp': base['prices'][p]['temp'] for p in BASE},
           **{f'{p} speed': base['prices'][p]['speed'] for p in BASE}}
    levels = {'stakes': list(STAKES), 'claude temp': list(RATIOS), 'codex temp': list(RATIOS),
              'claude speed': list(temperature.SPEEDS), 'codex speed': list(temperature.SPEEDS)}

    def steps(dim):
        here = levels[dim].index(now[dim])
        return sorted(((abs(i - here), v) for i, v in enumerate(levels[dim]) if v != now[dim]), key=lambda t: t[0])

    singles = [(d, {dim: v}) for dim in FLIP_DIMS for d, v in steps(dim)]
    pairs = [(d1 + d2, {a: v1, b: v2}) for n, a in enumerate(FLIP_DIMS) for b in FLIP_DIMS[n + 1:]
             for d1, v1 in steps(a) for d2, v2 in steps(b)]
    found = {i: None for i in ids}
    for size, group in ((1, singles), (2, pairs)):
        for distance, change in sorted(group, key=lambda t: t[0]):
            if all(found[i] for i in ids):
                return found
            preview = {p: dict((overrides or {}).get(p) or {}) for p in BASE}
            for p in BASE:
                if f'{p} temp' in change:
                    preview[p]['temp'] = change[f'{p} temp']
                if f'{p} speed' in change:
                    preview[p]['speed'] = None if change[f'{p} speed'] == 'NORMAL' else change[f'{p} speed']
            current = prices(overrides=preview, **paths)
            ranked = _rank(role, change.get('stakes', acting), allow_claude, only, all_points, current, narrowed)
            top = ranked[0]['id'] if ranked else None
            if top in found and not found[top]:
                found[top] = {'id': top, 'changes': [{'what': k, 'from': now[k], 'to': v} for k, v in change.items()],
                              'size': size, 'distance': distance, 'text': _flip_text(change)}
    return found


def snapshot(overrides=None, **paths):
    now = paths.get('now') or dt.datetime.now(dt.timezone.utc)
    if isinstance(now, (int, float)):
        now = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    all_points, current, roles, domains_ = _inputs(overrides=overrides, **{**paths, 'now': now})
    roles = {r: v for r, v in roles.items() if not v.get('internal')}
    grid = {}
    for role in roles:
        grid[role] = {}
        for stakes in STAKES:
            ranked = _rank(role, stakes, False, False, all_points, current, roles)
            grid[role][stakes] = ranked[0]['id'] if ranked else None
    for name in domains_:
        for stakes in STAKES:
            s, only, narrowed = _apply_domain('build', stakes, False, roles, domains_, name)
            ranked = _rank('build', s, False, only, all_points, current, narrowed)
            grid.setdefault(f'build {name}', {})[stakes] = ranked[0]['id'] if ranked else None
    return {'computed_at': now.isoformat(), 'points': [{**i, 'price_now': _price(i, current)} for i in all_points],
            'prices': current, 'grid': grid, 'stakes': STAKES, 'roles': list(roles),
            'weights': {r: v.get('weight', 1.0) for r, v in roles.items()}, 'domains': domains_}
