import json
import os
import sys
import urllib.request
from pathlib import Path

from rich import box
from rich.table import Table

from theme import console, header, state_mark, state_style, trim

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context"))
from instance import service_url  # noqa: E402

TIMEOUT = 3


def fetch():
    base = os.environ.get("BRAIN_PUBLIC") or service_url("brain-api")
    if not base:
        return {"error": "no BRAIN_PUBLIC and no brain-api domain in the instance manifest"}
    try:
        with urllib.request.urlopen(base + "/api/status", timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}


def summary(data):
    if "error" in data:
        return "[warn]brain unreachable[/warn]"
    s, d = data.get("summary", {}), data.get("dep_summary", {})
    parts = [f"services {s.get('up', 0)}/{s.get('total', 0)} up"]
    bad = s.get("down", 0) + d.get("down", 0)
    warn = d.get("warn", 0)
    parts.append(f"deps {d.get('up', 0)}/{d.get('total', 0)} up")
    if warn:
        parts.append(f"[warn]{warn} warn[/warn]")
    if bad:
        parts.append(f"[down]{bad} down[/down]")
    return ", ".join(parts)


def _mark(state):
    return f"[{state_style(state)}]{state_mark(state)} {state}[/{state_style(state)}]"


def run(interactive=False, data=None):
    data = data if data is not None else fetch()
    header("g-system", data.get("checked_at", "") if "error" not in data else "")
    if "error" in data:
        console.print(f"[warn]brain unreachable[/warn] [faint]{data['error']}[/faint]")
        return
    console.print(summary(data))

    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    t.add_column("service", style="hot", width=20, no_wrap=True, overflow="ellipsis")
    t.add_column("host", style="faint", width=5)
    t.add_column("state", width=7, no_wrap=True)
    t.add_column("detail", style="text", no_wrap=True, overflow="ellipsis")
    for s in data.get("services", []):
        t.add_row(s["id"], s.get("host") or "-", _mark(s.get("state")), trim(s.get("detail"), max(console.width - 40, 16)))
    console.print(t)

    d = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    d.add_column("dependency", style="hot", width=20, no_wrap=True, overflow="ellipsis")
    d.add_column("crit", style="faint", width=9, no_wrap=True)
    d.add_column("state", width=9, no_wrap=True)
    d.add_column("detail", style="text", no_wrap=True, overflow="ellipsis")
    for x in sorted(
        data.get("dependencies", []),
        key=lambda x: ({"down": 0, "warn": 1, "up": 2}.get(x.get("state"), 3), x["id"]),
    ):
        d.add_row(
            x["id"], x.get("criticality") or "", _mark(x.get("state")), trim(x.get("detail"), max(console.width - 46, 16))
        )
    console.print(d)
