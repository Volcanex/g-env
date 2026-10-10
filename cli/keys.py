import re
from pathlib import Path

from rich import box
from rich.table import Table

from theme import console, header, trim

REPO = Path(__file__).resolve().parent.parent
AEROSPACE = REPO / "aerospace/aerospace.toml"
GHOSTTY = REPO / "ghostty/config.ghostty"

MODS = {"cmd": "⌘", "shift": "⇧", "alt": "⌥", "ctrl": "⌃", "opt": "⌥"}
NAMED = {
    "leftSquareBracket": "[",
    "rightSquareBracket": "]",
    "backslash": "\\",
    "quote": "'",
    "comma": ",",
    "period": ".",
    "semicolon": ";",
    "minus": "-",
    "equal": "=",
    "slash": "/",
    "space": "space",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
}


def pretty_key(combo):
    parts = re.split(r"[-+]", combo)
    out = ""
    for p in parts:
        if p in MODS:
            out += MODS[p]
        else:
            out += NAMED.get(p, p.upper() if len(p) == 1 else p)
    return out


def clean_cmd(value):
    value = value.strip()
    if value.startswith("["):
        items = re.findall(r"'([^']*)'", value)
    else:
        items = [value.strip("'\"")]
    out = []
    for it in items:
        it = it.replace("exec-and-forget ", "")
        it = re.sub(r"/\S+/", "", it)
        out.append(it)
    return " + ".join(out)


def aerospace_bindings():
    if not AEROSPACE.exists():
        return []
    groups, group, comment = [], None, ""
    inside = False
    for line in AEROSPACE.read_text().splitlines():
        s = line.strip()
        if s.startswith("["):
            if inside:
                break
            inside = s.startswith("[mode.main.binding]")
            continue
        if not inside:
            continue
        if not s:
            group, comment = None, ""
            continue
        if s.startswith("#"):
            note = s.lstrip("# ").strip("─ ").strip()
            if not comment and note:
                comment = note
            continue
        if "=" not in s:
            continue
        key, value = s.split("=", 1)
        if group is None or group["title"] != comment:
            group = {"title": comment, "rows": []}
            groups.append(group)
        group["rows"].append((key.strip(), clean_cmd(value)))
    return [collapse(g) for g in groups]


def collapse(group):
    rows, out = group["rows"], []
    for key, cmd in rows:
        shape = re.sub(r"\d+", "#", cmd)
        if out and re.sub(r"\d+", "#", out[-1][1]) == shape and re.sub(r"\d+", "#", out[-1][0][0]) == re.sub(r"\d+", "#", key):
            out[-1][0].append(key)
        else:
            out.append(([key], cmd))
    merged = []
    for combos, cmd in out:
        if len(combos) > 2:
            label = f"{pretty_key(combos[0])}…{pretty_key(combos[-1])}"
            cmd = re.sub(r"\d+", "N", cmd)
        else:
            label = " / ".join(pretty_key(c) for c in combos)
        merged.append((label, cmd))
    return {"title": group["title"], "rows": merged}


def ghostty_bindings():
    if not GHOSTTY.exists():
        return []
    rows, comment = [], ""
    for line in GHOSTTY.read_text().splitlines():
        s = line.strip()
        if s.startswith("#"):
            note = s.lstrip("# ").strip()
            if not comment:
                comment = note
            continue
        if not s.startswith("keybind"):
            comment = ""
            continue
        spec = s.split("=", 1)[1].strip()
        combo, _, action = spec.partition("=")
        rows.append((pretty_key(combo), action or "?", comment))
    return rows


def render():
    header("keys", "live from aerospace.toml + config.ghostty")
    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    t.add_column("key", style="accent", width=14, no_wrap=True)
    t.add_column("action", style="text")
    console.print("[label]aerospace[/label]")
    for g in aerospace_bindings():
        if g["title"]:
            t.add_row("", f"[faint]{g['title']}[/faint]")
        for key, cmd in g["rows"]:
            t.add_row(key, cmd)
    console.print(t)

    g = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    g.add_column("key", style="accent", width=14, no_wrap=True)
    g.add_column("action", style="text", width=20)
    g.add_column("note", style="faint", no_wrap=True, overflow="ellipsis")
    console.print("[label]ghostty[/label]")
    for key, action, note in ghostty_bindings():
        g.add_row(key, action, trim(note, max(console.width - 40, 16)))
    console.print(g)


def run(interactive=False):
    render()
