import re
import subprocess
from pathlib import Path

from rich import box
from rich.table import Table

import env
from theme import console, header

REPO = Path(__file__).resolve().parent.parent
GHOSTTY = REPO / "ghostty/config.ghostty"
AEROSPACE = REPO / "aerospace/aerospace.toml"
AE_SWITCH = REPO / "bin/ae-switch"

NEW_WINDOWS = "applies to new Ghostty windows only"

TRANSPARENCY = {
    "opaque": """# Opaque: translucency+blur makes WindowServer composite every terminal frame
# (measurable CPU/power). Revert: background-opacity = 0.95, blur-radius 20.
background-opacity = 1.0
""",
    "pretty": """# Translucent + blurred: WindowServer composites every terminal frame
# (measurable CPU/power). Revert: background-opacity = 1.0, drop the blur line.
background-opacity = 0.95
background-blur-radius = 20
""",
}

TITLEBAR = {
    "hidden": """# hidden = no title bar, no tab bar, no traffic lights: full vertical bleed.
# Tabs still switch via cmd+N (ae-switch). Revert: native.
macos-titlebar-style = hidden
""",
    "native": """# native = system title bar and tab bar visible.
# Revert: hidden (no title bar, no tab bar, no traffic lights, full bleed).
macos-titlebar-style = native
""",
}

GAPS = {
    "edge": """# Edge-to-edge: no outer gaps, hairline inner gap. Accordion shows a thin slit
# of the windows behind.
accordion-padding = 10

[gaps]
inner.horizontal = 2
inner.vertical   = 2
outer.left       = 0
outer.right      = 0
outer.top        = 0
outer.bottom     = 0
""",
    "comfy": """# Comfy: even gaps all round, windows sit clear of the screen edge. Accordion
# shows a thin slit of the windows behind.
accordion-padding = 10

[gaps]
inner.horizontal = 6
inner.vertical   = 6
outer.left       = 6
outer.right      = 6
outer.top        = 6
outer.bottom     = 6
""",
}

VEIL_OFF = """# Transition veil disabled for observation. Re-enable by uncommenting.
# command -v ae-veil >/dev/null && ae-veil {d} &
"""
VEIL_ON = """# Transition veil on. Comment the line below to disable.
command -v ae-veil >/dev/null && ae-veil {d} &
"""
VEIL_RE = re.compile(r"^(#\s*)?command -v ae-veil >/dev/null && ae-veil ([\d.]+) &\s*$")

CYCLE_PAIRS = {
    "slash": ("⌘\\ / ⌘'", ("cmd-backslash", "cmd-quote")),
    "arrows": ("⌘← / ⌘→", ("cmd-left", "cmd-right")),
}


def swap(path, old, new):
    """Replace an exact block. Never write unless the block matched verbatim."""
    text = path.read_text()
    if old not in text:
        console.print(f"[warn]{path.name}: expected block not found, skipped[/warn]")
        return False
    path.write_text(text.replace(old, new, 1))
    return True


def ghostty_value(key):
    for line in GHOSTTY.read_text().splitlines():
        m = re.match(rf"{re.escape(key)}\s*=\s*(\S+)", line.strip())
        if m:
            return m.group(1)
    return None


def transparency_state():
    opacity = ghostty_value("background-opacity")
    if opacity == "1.0":
        return "opaque"
    if opacity == "0.95":
        return "pretty"
    return "?"


def titlebar_state():
    return ghostty_value("macos-titlebar-style") or "?"


def veil_state():
    for line in AE_SWITCH.read_text().splitlines():
        m = VEIL_RE.match(line.strip())
        if m:
            return ("off" if m.group(1) else "on"), m.group(2)
    return "?", "?"


def gaps_state():
    text = AEROSPACE.read_text()
    inner = re.search(r"^inner\.horizontal\s*=\s*(\d+)", text, re.M)
    outer = re.search(r"^outer\.left\s*=\s*(\d+)", text, re.M)
    if not inner or not outer:
        return "?", "?", "?"
    i, o = inner.group(1), outer.group(1)
    name = {("2", "0"): "edge", ("6", "6"): "comfy"}.get((i, o), "custom")
    return name, i, o


def binds(bare, keys):
    return any(re.match(rf"{re.escape(k)}\s*=", bare) for k in keys)


def cycle_state(pair):
    _, keys = CYCLE_PAIRS[pair]
    for line in AEROSPACE.read_text().splitlines():
        s = line.strip()
        if binds(s.lstrip("#").strip(), keys):
            return "off" if s.startswith("#") else "on"
    return "?"


def toggle_binding(keys, enable):
    lines = AEROSPACE.read_text().splitlines(keepends=True)
    hits = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not binds(s.lstrip("#").strip(), keys):
            continue
        if enable:
            lines[i] = re.sub(r"^#\s?", "", line)
        elif not s.startswith("#"):
            lines[i] = "# " + line
        hits += 1
    if hits != len(keys):
        console.print("[warn]aerospace.toml: binding lines not found, skipped[/warn]")
        return False
    AEROSPACE.write_text("".join(lines))
    return True


def reload_aerospace():
    try:
        r = subprocess.run(
            ["aerospace", "reload-config"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        console.print("[warn]aerospace reload-config failed to run[/warn]")
        return
    if r.returncode != 0:
        console.print(f"[warn]aerospace reload-config: {r.stderr.strip() or r.stdout.strip()}[/warn]")
    else:
        console.print("[faint]aerospace config reloaded[/faint]")


def rows():
    veil, duration = veil_state()
    gaps, inner, outer = gaps_state()
    return [
        ("1", "transparency", transparency_state(), "opaque ↔ pretty (0.95 + blur 20)"),
        ("2", "titlebar", titlebar_state(), "hidden ↔ native"),
        ("3", "veil", f"{veil}, {duration}s", "ae-switch transition flash"),
        ("4", "gaps", f"{gaps}, in {inner} out {outer}", "edge ↔ comfy"),
        ("5", "cycle ⌘\\ / ⌘'", cycle_state("slash"), "ae-cycle-ws prev/next"),
        ("6", "cycle ⌘← / ⌘→", cycle_state("arrows"), "steals cmd+arrow globals"),
        # Not a file-rewrite like the others: ae-switch reads this one live, so
        # it takes effect on the next ⌘N with no reload.
        ("7", "terminal home", env.term_home(), "which screen the Ghostty tab lives on"),
        ("8", "tiling", env.tiling_state(), "off = windows float, workspaces still work"),
    ]


def render():
    header("config", "live from the genv source files")
    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    t.add_column("#", style="accent", width=3, no_wrap=True)
    t.add_column("setting", style="label", width=16, no_wrap=True)
    t.add_column("state", style="hot", width=18, no_wrap=True)
    t.add_column("note", style="faint", no_wrap=True, overflow="ellipsis")
    for num, name, state, note in rows():
        t.add_row(num, name, state, note)
    console.print(t)


def toggle(choice):
    if choice == "1":
        state = transparency_state()
        if state == "?":
            console.print("[warn]config.ghostty: background-opacity not recognised[/warn]")
            return
        target = "pretty" if state == "opaque" else "opaque"
        if swap(GHOSTTY, TRANSPARENCY[state], TRANSPARENCY[target]):
            console.print(f"[faint]transparency →[/faint] [hot]{target}[/hot][faint], {NEW_WINDOWS}[/faint]")
    elif choice == "2":
        state = titlebar_state()
        if state not in TITLEBAR:
            console.print("[warn]config.ghostty: macos-titlebar-style not recognised[/warn]")
            return
        target = "native" if state == "hidden" else "hidden"
        if swap(GHOSTTY, TITLEBAR[state], TITLEBAR[target]):
            console.print(f"[faint]titlebar →[/faint] [hot]{target}[/hot][faint], {NEW_WINDOWS}[/faint]")
    elif choice == "3":
        state, duration = veil_state()
        if state == "?":
            console.print("[warn]ae-switch: veil line not found[/warn]")
            return
        old = (VEIL_ON if state == "on" else VEIL_OFF).format(d=duration)
        new = (VEIL_OFF if state == "on" else VEIL_ON).format(d=duration)
        if swap(AE_SWITCH, old, new):
            console.print(f"[faint]veil →[/faint] [hot]{'off' if state == 'on' else 'on'}[/hot]")
    elif choice == "4":
        state, _, _ = gaps_state()
        if state not in GAPS:
            console.print("[warn]aerospace.toml: [gaps] block not in a known preset[/warn]")
            return
        target = "comfy" if state == "edge" else "edge"
        if swap(AEROSPACE, GAPS[state], GAPS[target]):
            console.print(f"[faint]gaps →[/faint] [hot]{target}[/hot]")
            reload_aerospace()
    elif choice in ("5", "6"):
        pair = "slash" if choice == "5" else "arrows"
        label, keys = CYCLE_PAIRS[pair]
        state = cycle_state(pair)
        if state == "?":
            console.print("[warn]aerospace.toml: cycle bindings not found[/warn]")
            return
        if toggle_binding(keys, state == "off"):
            console.print(f"[faint]cycle {label} →[/faint] [hot]{'off' if state == 'on' else 'on'}[/hot]")
            reload_aerospace()
    elif choice == "7":
        value = env.toggle_term_home()
        note = "" if env.monitors() > 1 else "  single monitor, no effect yet"
        console.print(f"[faint]terminal home →[/faint] [hot]{value}[/hot][faint]{note}[/faint]")
    elif choice == "8":
        env.tiling(["toggle"])
    elif choice == "d":
        state, duration = veil_state()
        if state == "?":
            console.print("[warn]ae-switch: veil line not found[/warn]")
            return
        try:
            value = input("veil duration in seconds > ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not re.fullmatch(r"\d+(\.\d+)?", value):
            console.print("[warn]not a number, skipped[/warn]")
            return
        block = VEIL_ON if state == "on" else VEIL_OFF
        if swap(AE_SWITCH, block.format(d=duration), block.format(d=value)):
            console.print(f"[faint]veil duration →[/faint] [hot]{value}s[/hot]")


def run(interactive=False):
    while True:
        render()
        if not interactive:
            return
        console.print(
            "[faint]toggle which #? \\[d] veil duration, blank to return[/faint]"
        )
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if choice in ("", "q"):
            return
        if choice not in ("1", "2", "3", "4", "5", "6", "7", "8", "d"):
            continue
        toggle(choice)
        console.print()
