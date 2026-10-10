#!/usr/bin/env python3
"""Four-character grades for every model and effort, read off the charts in models.json.

Quality letter A-F in 5-point AA bands down from the best ladder score; cost digit 0-9,
each step about double the $/task (0 is $0.01); speed F/M/S/X from time to first token
(5s, 30s, 2min), falling back to a cell's ttft_estimate_s when AA lists none; taste mark from absolute Design Arena Elo. The grade says where a
setup sits on the graph, not what it costs this week: `temps` carries that.
"""
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import temperature  # noqa: E402

SPEEDS = ((5, 'F'), (30, 'M'), (120, 'S'))
TASTE = ((1440, '∴'), (1340, ':'))
FAMILIES = {'opus-5.5': 'opus', 'sonnet-5.5': 'sonnet', 'fable-5.1': 'fable', 'gpt-6.1-sol': 'sol',
            'gpt-6-luna': 'luna', 'gpt-6-astra': 'astra'}


def quality(score, best):
    return 'ABCDEF'[min(5, max(0, (best - score) // 5))]


def cost(usd):
    return str(min(9, max(0, round(math.log2(usd / 0.01)))))


def ttft(row):
    measured = row.get('ttft_s')
    return measured if measured is not None else row.get('ttft_estimate_s')


def speed(seconds):
    if seconds is None:
        return '?'
    return next((letter for limit, letter in SPEEDS if seconds <= limit), 'X')


def taste(da):
    if da is None:
        return '?'
    return next((mark for floor, mark in TASTE if da >= floor), '.')


def table(data):
    rows = data['effort_ladder'] + data['codex_ladder']
    best = max(row['score'] for row in rows)
    da = {model['id']: model.get('scores', {}).get('DA') for model in data.get('models', [])}
    result = {}
    for row in rows:
        family = FAMILIES[row['model']]
        result[f"{family}-{row['effort']}"] = {
            'grade': quality(row['score'], best) + cost(row['cost_usd']) + speed(ttft(row))
                     + taste(da.get(row['model'])),
            'provider': 'claude' if row in data['effort_ladder'] else 'codex',
            'aa': row['score'], 'cost_usd': row['cost_usd'], 'ttft_s': ttft(row)}
    return result


def snapshot(models_path=None):
    data = json.loads(Path(models_path or HERE / 'models.json').read_text())
    temps = {}
    for name in ('claude', 'codex'):
        try:
            temps[name] = temperature.provider(name)[0]
        except Exception:
            temps[name] = None
    return {'as_of': data.get('as_of'), 'grades': table(data), 'temps': temps}


if __name__ == '__main__':
    out = snapshot()
    if '--json' in sys.argv:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for point, row in out['grades'].items():
            print(f"{row['grade']}  {point}")
