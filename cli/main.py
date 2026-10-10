import sys
import termios
import tty
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import config
import env
import gsystem
import keys
import power
import sessions
from theme import console

MENU = [
    ("e", "env", env.run),
    ("s", "sessions", sessions.run),
    ("h", "keys", keys.run),
    ("p", "power", power.run),
    ("g", "g-system", gsystem.run),
    ("c", "config", config.run),
]
ALIASES = {
    "env": "e",
    "sessions": "s",
    "keys": "h",
    "power": "p",
    "sys": "g",
    "g-system": "g",
    "config": "c",
}


def getkey():
    if not sys.stdin.isatty():
        return "q"
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def dashboard():
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_sessions = pool.submit(sessions.collect)
        f_status = pool.submit(gsystem.fetch)
        f_power = pool.submit(power.strip)
        f_env = pool.submit(env.collect)
        rows, stale = f_sessions.result()
        status = f_status.result()
        power_strip = f_power.result()
        procs = f_env.result()

    console.clear()
    console.print()
    console.print(
        f"[accent]genv[/accent]  [faint]environment[/faint]"
        f"   [faint]{datetime.now().strftime('%a %d %b  %H:%M')}[/faint]"
    )
    console.print("[faint]" + "─" * min(console.width, 78) + "[/faint]")
    env_state = env.summary(procs)
    console.print(
        f"[label]env[/label]       [{'ok' if env_state == 'up' else 'warn'}]{env_state}[/]"
    )
    console.print(f"[label]sessions[/label]  [faint]{sessions.summary(rows, stale)}[/faint]")
    if rows:
        console.print(sessions.table(rows, stale))
    else:
        console.print("[faint]no ws* sessions running[/faint]\n")
    console.print(f"[label]power[/label]     [text]{power_strip}[/text]")
    console.print(f"[label]g-system[/label]  {gsystem.summary(status)}")
    console.print("[faint]" + "─" * min(console.width, 78) + "[/faint]")
    console.print(
        "  ".join(f"[accent]\\[{k}][/accent] [text]{n}[/text]" for k, n, _ in MENU)
        + "  [faint]\\[q] quit[/faint]"
    )


def loop():
    while True:
        dashboard()
        key = getkey().lower()
        if key in ("q", "\x03", "\x04"):
            console.print()
            return
        for k, _, fn in MENU:
            if key == k:
                console.clear()
                fn(interactive=True)
                console.print("\n[faint]any key to return, q to quit[/faint]")
                if getkey().lower() in ("q", "\x03", "\x04"):
                    console.print()
                    return
                break


USAGE = (
    "usage: genv \\[up|down|status|ipad|term|tiling|fade|ws|env|sessions|keys|power|sys|config]\n"
    "       genv ipad \\[on|off|toggle]   genv ipad side \\[left|right|toggle]\n"
    "       genv term \\[main|secondary|toggle]   genv fade \\[restart]\n"
    "       genv ws \\[<N>] \\[free]   (hot workspaces 6/7/8)"
)


def main(argv):
    if not argv:
        loop()
        return 0
    cmd = argv[0].lstrip("-")
    if cmd in ("help", "?"):
        console.print(USAGE)
        return 0
    # Action verbs take arguments and are checked before the single-panel menu
    # keys, so `genv up` can't be read as a one-letter menu alias.
    if cmd in env.VERBS:
        return env.VERBS[cmd](argv[1:])
    key = ALIASES.get(cmd, cmd if len(cmd) == 1 else None)
    for k, _, fn in MENU:
        if key == k:
            fn(interactive=False)
            return 0
    console.print(f"[warn]unknown command:[/warn] {argv[0]}")
    console.print(f"[faint]{USAGE}[/faint]")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
