"""Lifecycle of the environment itself: start it, stop it, see what's alive.

The window manager is four cooperating processes, not one app, and before this
module there was no single place that knew that: AeroSpace and ae-swipe came up
via LaunchAgents, ae-fade was started by AeroSpace, and nothing could report or
change the set. `genv up|down|status` is that place, and genv-bar is a front end
over exactly these functions, so behaviour can't drift between the menu and the
CLI.

ae-fade needs Screen Recording, and macOS attributes that to the *responsible*
process, which is AeroSpace, since AeroSpace spawns it. Starting it any other
way gets a different verdict than it will get at login, so `genv fade` restarts
it through AeroSpace rather than launching it here (see restart_fade).
"""

import ctypes
import ctypes.util
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from rich import box
from rich.table import Table

import workspace
from theme import console, header, state_mark, state_style

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
AGENT_DIR = Path.home() / "Library/LaunchAgents"
SIDECAR = Path.home() / ".local/bin/ae-sidecar"
AE_SWITCH = Path.home() / ".local/bin/ae-switch"
# ae-switch's cache of how many monitors AeroSpace can see. It exists because
# asking cost 20ms of every switch; it is refreshed in the background *after* a
# switch, so nothing invalidates it at the moment it actually goes stale (a
# display appearing or disappearing). See resync_workspaces.
MONITOR_CACHE = Path.home() / ".cache/genv/monitors"

# Terminal home: which monitor the Ghostty tab is homed on while a second
# display is attached. Read by ae-switch on every switch, so a change lands on
# the next cmd+N with no reload and no restart. Absent file = secondary, the
# long-standing behaviour (the iPad is usually the terminal screen).
TERM_HOME_FILE = Path.home() / ".genv-term-home"
TERM_HOMES = ("main", "secondary")

# Which side of the laptop the iPad sits on. Read by ae-sidecar after every
# connect, so an iPad waking up on its own still lands on the right side without
# anyone typing anything. Absent = right: a second screen goes on the right
# unless told otherwise.
IPAD_SIDE_FILE = Path.home() / ".genv-ipad-side"
IPAD_SIDES = ("left", "right")

# name → (plist filename in bin/, process name to verify and to kill)
AGENTS = {
    "aerospace": ("com.gabriel.aerospace.plist", "AeroSpace"),
    "ae-swipe": ("com.gabriel.ae-swipe.plist", "ae-swipe"),
    "genv-bar": ("com.gabriel.genv-bar.plist", "genv-bar"),
}
LABELS = {name: f"com.gabriel.{name}" for name in AGENTS}
GUI = f"gui/{os.getuid()}"

NOTES = {
    "aerospace": "window manager",
    "ae-swipe": "trackpad gestures",
    "genv-bar": "menu bar item",
    "ae-fade": "switch crossfade, started by aerospace",
}


def sh(*args, timeout=15):
    """Run a command, returning None rather than raising when it can't run."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def alive(process):
    r = sh("pgrep", "-x", process, timeout=5)
    return bool(r and r.returncode == 0)


def install_agent(name):
    """Copy the plist into ~/Library/LaunchAgents. Never symlink it.

    launchd's login-time scan of that directory does not follow symlinks, so a
    symlinked agent loads only for the session that ran `launchctl bootstrap`
    and is silently absent after the next reboot. That is exactly why AeroSpace
    and ae-swipe stopped starting at login while ae-dash (a real file) kept
    working. Re-copied whenever the repo copy differs, so editing the plist in
    the repo and running `genv up` is enough.
    """
    src = BIN / AGENTS[name][0]
    dst = AGENT_DIR / AGENTS[name][0]
    if not src.exists():
        return None
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or not dst.exists() or dst.read_bytes() != src.read_bytes():
        dst.unlink(missing_ok=True)
        shutil.copyfile(src, dst)
    return dst


def start(name):
    plist = install_agent(name)
    if plist is None:
        return False
    # bootstrap fails with "already bootstrapped" once registered, which is not
    # an error here; kickstart is what actually guarantees a running process,
    # covering both the fresh-register and the registered-but-stopped case.
    sh("launchctl", "bootstrap", GUI, str(plist))
    sh("launchctl", "kickstart", "-k", f"{GUI}/{LABELS[name]}")
    return alive(AGENTS[name][1])


def stop(name):
    sh("launchctl", "bootout", f"{GUI}/{LABELS[name]}")
    # A hand-launched AeroSpace (open -a, or its own start-at-login) is not
    # launchd's child, so bootout alone leaves it running.
    sh("pkill", "-x", AGENTS[name][1])
    if name == "aerospace":
        # ae-fade is AeroSpace's child but does not die with it. It would sit
        # holding /tmp/ae-fade.sock and answer the next switch with a stale frame.
        sh("pkill", "-x", "ae-fade")


def term_home():
    try:
        value = TERM_HOME_FILE.read_text().strip()
    except OSError:
        return "secondary"
    return value if value in TERM_HOMES else "secondary"


def set_term_home(value):
    TERM_HOME_FILE.write_text(value + "\n")
    return value


def toggle_term_home():
    return set_term_home("main" if term_home() == "secondary" else "secondary")


def ipad_side():
    """The wanted side, not the live one: ae-sidecar answers for the screen."""
    try:
        value = IPAD_SIDE_FILE.read_text().strip()
    except OSError:
        return "right"
    return value if value in IPAD_SIDES else "right"


def set_ipad_side(value):
    IPAD_SIDE_FILE.write_text(value + "\n")
    return value


def toggle_ipad_side():
    return set_ipad_side("left" if ipad_side() == "right" else "right")


AEROSPACE_TOML = REPO / "aerospace/aerospace.toml"
# The TILING_BLOCK lines following this marker are a catch-all float rule.
# Commented = tiling on. A marker rather than a matched block because the rule
# is a few short lines that would be far too easy to match somewhere else in the
# file. Keep TILING_BLOCK in step with the rule: it grew a
# `check-further-callbacks` line so that windows still fall through to the
# secondary-screen rule at the bottom of the config while tiling is off.
TILING_MARKER = "# TILING-OFF"
TILING_BLOCK = 3


def tiling_state():
    lines = AEROSPACE_TOML.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip() == TILING_MARKER:
            block = lines[i + 1 : i + 1 + TILING_BLOCK]
            if len(block) < TILING_BLOCK:
                return "?"
            return "on" if all(b.strip().startswith("#") for b in block) else "off"
    return "?"


def set_tiling(on):
    """Comment the catch-all float rule (tiling on) or uncomment it (tiling off)."""
    lines = AEROSPACE_TOML.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() != TILING_MARKER:
            continue
        if i + TILING_BLOCK >= len(lines):
            return False
        for j in range(i + 1, i + 1 + TILING_BLOCK):
            if on:
                if not lines[j].lstrip().startswith("#"):
                    lines[j] = "# " + lines[j]
            else:
                lines[j] = re.sub(r"^(\s*)#\s?", r"\1", lines[j])
        AEROSPACE_TOML.write_text("".join(lines))
        return True
    return False


def tab_windows():
    """AeroSpace window ids of the Ghostty tabs ae-switch owns."""
    ids = set()
    for path in Path.home().glob(".ae-tab-[0-9]"):
        try:
            parts = path.read_text().split()
        except OSError:
            continue
        if len(parts) > 1:
            ids.add(parts[1])
    return ids


def apply_tiling(on):
    """Re-layout the windows that already exist.

    The config rule only fires on *new* windows, so without this a toggle
    appears to do nothing until you open something. The terminal tabs are
    skipped: ae-switch re-asserts `layout tiling` on them every switch, so
    floating one here would be undone a moment later and, worse, would leave the
    terminal a floating rectangle instead of filling its workspace.
    """
    r = sh("aerospace", "list-windows", "--all", "--format", "%{window-id}", timeout=10)
    if not r or r.returncode != 0:
        return 0
    skip = tab_windows()
    target = "tiling" if on else "floating"
    count = 0
    for line in r.stdout.split():
        if line in skip:
            continue
        sh("aerospace", "layout", target, "--window-id", line, timeout=5)
        count += 1
    return count


def reload_aerospace():
    sh("aerospace", "reload-config", timeout=10)


def tiling(rest):
    state = tiling_state()
    if state == "?":
        console.print("[warn]aerospace.toml: TILING-OFF marker not found[/warn]")
        return 1
    if not rest:
        console.print(f"[text]{state}[/text]")
        return 0
    if rest[0] == "toggle":
        want = state == "off"
    elif rest[0] in ("on", "off"):
        want = rest[0] == "on"
    else:
        console.print("[faint]usage: genv tiling \\[on|off|toggle][/faint]")
        return 1
    if not set_tiling(want):
        console.print("[warn]aerospace.toml: could not rewrite the rule, skipped[/warn]")
        return 1
    reload_aerospace()
    moved = apply_tiling(want)
    console.print(
        f"[faint]tiling →[/faint] [hot]{'on' if want else 'off'}[/hot]"
        f"[faint], {moved} window{'' if moved == 1 else 's'} re-laid out[/faint]"
    )
    return 0


def monitors():
    """Active displays, asked of CoreGraphics rather than of AeroSpace.

    `aerospace list-monitors` returns nothing when the window manager is down,
    which would report a docked iPad as "single monitor" on precisely the screen
    that exists to tell you the environment isn't running. ae-switch keeps its
    own AeroSpace-based count. It needs AeroSpace's view of the world, we need
    the machine's.

    Online, not Active: CGGetActiveDisplayList returns 0 displays from a plain
    CLI process (no window server connection), so it would report every genv
    invocation as having no screens at all. Online counts what's attached and
    powered, which is the question being asked.
    """
    try:
        path = ctypes.util.find_library("CoreGraphics")
        if not path:
            return 0
        cg = ctypes.cdll.LoadLibrary(path)
        cg.CGGetOnlineDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        cg.CGGetOnlineDisplayList.restype = ctypes.c_int32
        count = ctypes.c_uint32(0)
        displays = (ctypes.c_uint32 * 16)()
        if cg.CGGetOnlineDisplayList(16, displays, ctypes.byref(count)) != 0:
            return 0
        return count.value
    except (OSError, AttributeError, TypeError):
        return 0


def sidecar_devices():
    """[(name, connected)]: empty when nothing has paired, or on failure."""
    if not SIDECAR.exists():
        return []
    r = sh(str(SIDECAR), "list", timeout=10)
    if not r or r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        name, _, state = line.partition("\t")
        if name:
            out.append((name, state == "connected"))
    return out


def sidecar(action, target=None):
    if not SIDECAR.exists():
        return False, "ae-sidecar not built: run bootstrap.sh"
    r = sh(str(SIDECAR), action, *([target] if target else []), timeout=30)
    if not r:
        return False, "ae-sidecar failed to run"
    if r.returncode != 0:
        return False, (r.stderr.strip() or r.stdout.strip() or "failed")
    return True, r.stdout.strip()


def resync_workspaces():
    """Re-read the monitor count and replay the current workspace.

    ae-switch caches AeroSpace's monitor count and only refreshes it in the
    background once a switch has already finished, so the first cmd+N after the
    iPad connects still runs the one-monitor path: the terminal stays on the
    Mac and the twins stay empty. The switch after that then yanks all nine
    terminal tabs across at once. Connecting or dropping a screen is precisely
    the moment the count is known to have changed, so refresh it here and
    switch again. The whole environment arrives in one step instead of two,
    and the second one isn't a surprise.

    AeroSpace notices a new display a beat after the window server does, so wait
    for its count to agree with CoreGraphics before trusting it.
    """
    count = 0
    for _ in range(12):
        r = sh("aerospace", "list-monitors", timeout=10)
        if not r or r.returncode != 0:
            return False
        count = len([line for line in r.stdout.splitlines() if line.strip()])
        if count == monitors():
            break
        time.sleep(0.5)
    if not count:
        return False
    MONITOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    MONITOR_CACHE.write_text(f"{count}\n")
    r = sh("aerospace", "list-workspaces", "--focused", timeout=10)
    ws = (r.stdout.strip() if r and r.returncode == 0 else "")
    # N and Nx are two halves of one workspace; ae-switch takes the number.
    ws = ws[:-1] if ws.endswith("x") else ws
    if not ws.isdigit() or not AE_SWITCH.exists():
        return False
    sh(str(AE_SWITCH), ws, timeout=60)
    return True


def restart_fade():
    """Restart ae-fade with AeroSpace as its parent, and say what it got.

    Screen Recording is granted to the responsible process, which for anything
    AeroSpace spawns is AeroSpace, so ae-fade's own tick in System Settings is
    never consulted at login, and starting it from this process would test a
    permission it will never actually run under. `exec-and-forget` is
    config-only (the CLI rejects the subcommand), so the only way into
    AeroSpace's process context from outside is to trigger a binding it already
    holds: alt-shift-r, which exists for this.
    """
    log = Path("/tmp/ae-fade.err")
    try:
        log.write_text("")
    except OSError:
        pass
    sh("pkill", "-x", "ae-fade")
    time.sleep(1)
    r = sh("aerospace", "trigger-binding", "--mode", "main", "alt-shift-r", timeout=10)
    if not r or r.returncode != 0:
        return False, "aerospace could not trigger alt-shift-r"
    time.sleep(2)
    if not alive("ae-fade"):
        return False, "ae-fade did not start"
    try:
        denied = "NO screen recording" in log.read_text()
    except OSError:
        denied = False
    if denied:
        return True, "up, but NO screen recording: grant it to /Applications/AeroSpace.app"
    return True, "up, screen recording OK"


def collect():
    procs = {name: ("up" if alive(proc) else "down") for name, (_, proc) in AGENTS.items()}
    procs["ae-fade"] = "up" if alive("ae-fade") else "down"
    return procs


def summary(procs=None):
    procs = procs or collect()
    down = [name for name, state in procs.items() if state == "down"]
    if not down:
        return "up"
    if len(down) == len(procs):
        return "down"
    return f"partial: {', '.join(down)} down"


def render():
    procs = collect()
    connected = [name for name, on in sidecar_devices() if on]

    header("environment", "genv up, genv down")
    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    t.add_column("", style="accent", width=1, no_wrap=True)
    t.add_column("process", style="label", width=16, no_wrap=True)
    t.add_column("state", style="hot", width=10, no_wrap=True)
    t.add_column("note", style="faint", no_wrap=True, overflow="ellipsis")
    for name, state in procs.items():
        t.add_row(state_mark(state), name, f"[{state_style(state)}]{state}[/]", NOTES[name])
    console.print(t)

    console.print(f"[label]sidecar[/label]    [text]{connected[0] if connected else 'not connected'}[/text]")
    console.print(f"[label]tiling[/label]     [text]{tiling_state()}[/text]")
    # Only meaningful with a second screen. Say so rather than showing a
    # setting that currently changes nothing.
    scope = "" if monitors() > 1 else "  [faint]single monitor, no effect[/faint]"
    console.print(f"[label]terminal[/label]   [text]{term_home()}[/text]{scope}")
    console.print(f"[label]ipad side[/label]  [text]{ipad_side()}[/text]{scope}\n")


def up():
    for name in AGENTS:
        console.print(f"[faint]{name} →[/faint] [hot]{'up' if start(name) else 'failed'}[/hot]")


def down():
    for name in AGENTS:
        stop(name)
        console.print(f"[faint]{name} →[/faint] [hot]down[/hot]")


def ipad(rest):
    if not rest:
        devices = sidecar_devices()
        if not devices:
            console.print("[faint]no Sidecar devices paired[/faint]")
        for name, on in devices:
            state = "connected" if on else "available"
            console.print(
                f"[accent]{state_mark('up' if on else 'down')}[/accent] "
                f"[text]{name}[/text] [faint]{state}[/faint]"
            )
        return 0
    if rest[0] == "side":
        arg = rest[1] if len(rest) > 1 else None
        if arg is None:
            console.print(f"[text]{ipad_side()}[/text]")
            return 0
        if arg == "toggle":
            value = toggle_ipad_side()
        elif arg in IPAD_SIDES:
            value = set_ipad_side(arg)
        else:
            console.print("[faint]usage: genv ipad side \\[left|right|toggle][/faint]")
            return 1
        # The file alone only decides where the *next* connect lands, so move
        # the screen now too. Otherwise the menu item looks like it did nothing
        # while the iPad is sitting right there.
        ok, _ = sidecar("side", value)
        note = "" if ok else "  [faint]applies on next connect[/faint]"
        console.print(f"[faint]ipad side →[/faint] [hot]{value}[/hot]{note}")
        return 0
    if rest[0] not in ("on", "off", "toggle"):
        console.print("[faint]usage: genv ipad \\[on|off|toggle|side] \\[device][/faint]")
        return 1
    ok, message = sidecar(rest[0], rest[1] if len(rest) > 1 else None)
    if not ok:
        console.print(f"[warn]{message}[/warn]")
        return 1
    console.print(f"[faint]ipad {rest[0]} →[/faint] [hot]{message}[/hot]")
    # The screen count just changed. Nothing else tells ae-switch that.
    if resync_workspaces():
        console.print("[faint]workspaces re-homed[/faint]")
    return 0


def term(rest):
    if not rest:
        console.print(f"[text]{term_home()}[/text]")
        return 0
    if rest[0] == "toggle":
        value = toggle_term_home()
    elif rest[0] in TERM_HOMES:
        value = set_term_home(rest[0])
    else:
        console.print("[faint]usage: genv term \\[main|secondary|toggle][/faint]")
        return 1
    console.print(f"[faint]terminal home →[/faint] [hot]{value}[/hot]")
    return 0


def fade(rest):
    if rest and rest[0] not in ("restart",):
        console.print("[faint]usage: genv fade \\[restart][/faint]")
        return 1
    ok, message = restart_fade()
    style = "hot" if ok else "warn"
    console.print(f"[faint]ae-fade →[/faint] [{style}]{message}[/{style}]")
    return 0 if ok else 1


VERBS = {
    "ws": workspace.ws,
    "up": lambda rest: (up(), 0)[1],
    "down": lambda rest: (down(), 0)[1],
    "status": lambda rest: (render(), 0)[1],
    "ipad": ipad,
    "term": term,
    "tiling": tiling,
    "fade": fade,
}


def run(interactive=False):
    render()
    if not interactive:
        return
    console.print(
        "[faint]\\[u] up  \\[d] down  \\[i] ipad  \\[s] ipad side  \\[t] terminal home  "
        "\\[l] tiling  blank to return[/faint]"
    )
    try:
        choice = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if choice == "u":
        up()
    elif choice == "d":
        down()
    elif choice == "i":
        ipad(["toggle"])
    elif choice == "s":
        ipad(["side", "toggle"])
    elif choice == "t":
        term(["toggle"])
    elif choice == "l":
        tiling(["toggle"])
