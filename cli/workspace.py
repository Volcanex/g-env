"""Hot workspaces: the ones with no fixed identity.

Most workspaces are pinned in the instance manifest: 3 is Work, 2 is Home,
and the terminal that opens there always goes to the same place. Workspaces
marked `"hot": true` are a pool instead. The first shell to open on one has
nothing to connect to, so it asks. `genv ws <N>` prints the project list and
writes the answer where the shell can find it.

The binding is three files, all Mac-side, all read by `zsh/zshrc`:

    ~/.ae-workspace-<N>   host id, the same file ae-switch writes for a pinned
                          workspace, which is what makes the auto-SSH block fire
                          without knowing anything about hot workspaces
    ~/.ae-ws-path-<N>     directory to start in
    ~/.ae-ws-label-<N>    what to call it in the tab title

Deliberately files rather than a manifest edit: a hot workspace is a choice
about *this login*, not topology, and the instance is the SSOT for topology. It also
means no reload: the next shell reads the new answer. `ae-startup` deletes all
three at login, which is what makes the binding last a session and no longer.
"""

import json
import subprocess
import sys
from pathlib import Path

from rich import box
from rich.table import Table

from theme import console, header

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context"))
from instance import instance  # noqa: E402

MANIFEST = instance("manifest")
# Both hosts keep their checkouts here, root-owned on h and gabriel's on b.
PROJECT_ROOT = "/home/gabriel"


def _manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (AttributeError, OSError, ValueError):
        return {}


def workspace(n):
    for w in _manifest().get("workspaces", []):
        if str(w.get("number")) == str(n):
            return w
    return None


def is_hot(n):
    w = workspace(n)
    return bool(w and w.get("hot"))


def _f(kind, n):
    return Path.home() / f".ae-{kind}-{n}"


def binding(n):
    """(host, path, label) for a bound hot workspace, else None."""
    try:
        host = _f("workspace", n).read_text().strip()
        path = _f("ws-path", n).read_text().strip()
    except OSError:
        return None
    if not host or not path:
        return None
    try:
        label = _f("ws-label", n).read_text().strip()
    except OSError:
        label = path.rsplit("/", 1)[-1]
    return host, path, label


def bind(n, host, path, label):
    _f("workspace", n).write_text(host + "\n")
    _f("ws-path", n).write_text(path + "\n")
    _f("ws-label", n).write_text(label + "\n")


def unbind(n):
    for kind in ("workspace", "ws-path", "ws-label"):
        _f(kind, n).unlink(missing_ok=True)


def projects():
    """[(id, host)] from the manifest, deprecated and fossil ones last."""
    rank = {"active": 0, None: 0, "deprecated": 1, "fossil": 1}
    rows = [
        (p["id"], p.get("host"), rank.get(p.get("status"), 0))
        for p in _manifest().get("projects", [])
        if p.get("id") and p.get("host")
    ]
    rows.sort(key=lambda r: (r[2], r[1], r[0]))
    return [(pid, host) for pid, host, _ in rows]


def _dirs(host):
    """Directory names under PROJECT_ROOT on `host`, or None if we can't ask.

    None and empty are different answers: an unreachable host must not silently
    empty the list, it just means we can't mark anything as missing.
    """
    try:
        # stdin=DEVNULL is load-bearing, not tidiness: ssh reads stdin, and this
        # runs immediately before the picker's own input(). Without it ssh eats
        # the answer, and the prompt appears to ignore what you typed.
        r = subprocess.run(
            ["ssh", "-q", "-n", "-o", "ConnectTimeout=4", "-o", "BatchMode=yes",
             host, f"ls -1 {PROJECT_ROOT}"],
            capture_output=True, text=True, timeout=12,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {line.strip() for line in r.stdout.splitlines() if line.strip()}


def _render(rows, n):
    header(f"workspace {n}", "pick a project by number or name, q to cancel")
    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    t.add_column("", style="accent", width=3, no_wrap=True)
    t.add_column("project", style="text", width=24, no_wrap=True)
    t.add_column("host", style="label", width=6, no_wrap=True)
    t.add_column("path", style="faint", no_wrap=True, overflow="ellipsis")
    for i, (pid, host, missing) in enumerate(rows, 1):
        path = f"{PROJECT_ROOT}/{pid}"
        t.add_row(str(i), pid, host, path + ("  (not on disk)" if missing else ""))
    console.print(t)


def pick(n):
    """Ask which project workspace `n` is for now. Returns 0 once bound."""
    rows = projects()
    if not rows:
        console.print("[warn]no projects in the manifest to pick from[/warn]")
        return 1
    # One listing per host, not one per project.
    seen = {host: _dirs(host) for host in sorted({h for _, h in rows})}
    rows = [
        (pid, host, seen.get(host) is not None and pid not in seen[host])
        for pid, host in rows
    ]
    _render(rows, n)
    while True:
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 1
        if not answer:
            continue
        if answer.lower() in ("q", "quit", "exit"):
            return 1
        chosen = None
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            chosen = rows[int(answer) - 1]
        else:
            matches = [r for r in rows if answer.lower() in r[0].lower()]
            if len(matches) == 1:
                chosen = matches[0]
            elif len(matches) > 1:
                console.print(
                    "[faint]matches " + ", ".join(m[0] for m in matches) + "[/faint]"
                )
                continue
        if chosen is None:
            console.print("[faint]no such project[/faint]")
            continue
        pid, host, _ = chosen
        bind(n, host, f"{PROJECT_ROOT}/{pid}", pid)
        console.print(f"[faint]workspace {n} →[/faint] [hot]{pid}[/hot] [faint]on {host}[/faint]")
        return 0


def status():
    """What every hot workspace is currently pointed at."""
    hot = [w for w in _manifest().get("workspaces", []) if w.get("hot")]
    if not hot:
        console.print("[faint]no hot workspaces in the manifest[/faint]")
        return 0
    header("hot workspaces", "genv ws <N> to (re)bind")
    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    t.add_column("ws", style="accent", width=3, no_wrap=True)
    t.add_column("project", style="text", width=24, no_wrap=True)
    t.add_column("host", style="label", width=6, no_wrap=True)
    t.add_column("path", style="faint", no_wrap=True, overflow="ellipsis")
    for w in hot:
        n = w["number"]
        b = binding(n)
        if b:
            host, path, label = b
            t.add_row(str(n), label, host, path)
        else:
            t.add_row(str(n), "[faint]free[/faint]", "", "")
    console.print(t)
    return 0


def ws(rest):
    """genv ws              what the pool is pointed at
    genv ws <N>          pick a project for workspace N
    genv ws <N> free     hand it back to the pool
    """
    if not rest:
        return status()
    n = rest[0]
    if not n.isdigit():
        console.print("[faint]usage: genv ws \\[<N>] \\[free][/faint]")
        return 1
    if not is_hot(n):
        w = workspace(n)
        name = w.get("name", n) if w else n
        console.print(f"[warn]workspace {n} ({name}) is pinned in the manifest[/warn]")
        return 1
    if len(rest) > 1 and rest[1] in ("free", "clear", "unbind"):
        unbind(n)
        console.print(f"[faint]workspace {n} →[/faint] [hot]free[/hot]")
        return 0
    return pick(n)
