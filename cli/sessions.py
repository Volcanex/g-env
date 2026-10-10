import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich import box
from rich.table import Table

from theme import console, header

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context"))
from instance import instance  # noqa: E402

MANIFEST = instance("manifest")
FMT = "#{session_name} #{session_attached} #{session_created}"
TIMEOUT = 2


def _manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except Exception:
        return {}


def workspace_names():
    return {str(w["number"]): w.get("name", "") for w in _manifest().get("workspaces", [])}


def hosts():
    return sorted({w["host"] for w in _manifest().get("workspaces", []) if w.get("host")})


def _parse(host, out):
    rows = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[0].startswith("ws"):
            continue
        name, attached, created = parts
        rows.append(
            {
                "host": host,
                "name": name,
                "attached": attached == "1",
                "created": int(created),
            }
        )
    return rows


def _local():
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", FMT],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        ).stdout
    except Exception:
        return "local", None
    return "local", _parse("local", out)


def _remote(host):
    try:
        out = subprocess.run(
            [
                "ssh",
                "-o",
                f"ConnectTimeout={TIMEOUT}",
                "-o",
                "BatchMode=yes",
                "-q",
                host,
                f"tmux list-sessions -F '{FMT}' 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT + 1,
        ).stdout
    except Exception:
        return host, None
    return host, _parse(host, out)


def collect():
    targets = [_local] + [lambda h=h: _remote(h) for h in hosts()]
    with ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
        results = list(pool.map(lambda f: f(), targets))
    rows, stale = [], []
    for host, got in results:
        if got is None:
            stale.append(host)
        else:
            rows += got
    order = {"local": 0}
    rows.sort(key=lambda r: (order.get(r["host"], 1), r["host"], r["name"]))
    return rows, stale


def _age(created):
    secs = max(int(time.time()) - created, 0)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def table(rows, stale, numbered=True):
    names = workspace_names()
    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    if numbered:
        t.add_column("#", style="faint", width=3)
    t.add_column("host", style="text", width=6)
    t.add_column("session", style="hot", width=14)
    t.add_column("workspace", style="text", width=16)
    t.add_column("att", width=4)
    t.add_column("age", style="faint", width=6)
    for i, r in enumerate(rows, 1):
        ws = r["name"].split("-")[0].replace("ws", "")
        att = "[accent]yes[/accent]" if r["attached"] else "[faint]no[/faint]"
        cells = [r["host"], r["name"], names.get(ws, "?"), att, _age(r["created"])]
        t.add_row(*([str(i)] + cells if numbered else cells))
    if not rows:
        t.add_row(*[""] * (6 if numbered else 5))
    return t


def summary(rows, stale):
    bits = [f"{len(rows)} session{'s' if len(rows) != 1 else ''}"]
    att = sum(1 for r in rows if r["attached"])
    if att:
        bits.append(f"{att} attached")
    if stale:
        bits.append("… " + ",".join(stale) + " unreachable")
    return "  ".join(bits)


def kill(host, name):
    if host == "local":
        cmd = ["tmux", "kill-session", "-t", name]
    else:
        cmd = ["ssh", "-q", host, f"tmux kill-session -t '{name}'"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        console.print(f"[faint]killed[/faint] [hot]{name}[/hot] [faint]on {host}[/faint]")
    else:
        console.print(f"[warn]failed to kill {name} on {host}[/warn]")


def run(interactive=False):
    rows, stale = collect()
    header("sessions", summary(rows, stale))
    if not rows:
        console.print("[faint]no ws* sessions running[/faint]")
    else:
        console.print(table(rows, stale))
    if not interactive or not rows:
        return
    console.print("[faint]kill which #? space-separated for several, e.g. 18 10 2 (blank to return)[/faint]")
    try:
        choice = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    picks = []
    for tok in choice.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= len(rows):
            picks.append(int(tok))
    for n in dict.fromkeys(picks):
        r = rows[n - 1]
        kill(r["host"], r["name"])
