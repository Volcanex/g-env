import json
import re
import subprocess
import sys
import time
from pathlib import Path

from rich import box
from rich.console import Group
from rich.live import Live
from rich.table import Table

from theme import console, header

CACHE = Path.home() / ".cache/genv/specs.json"


def sh(cmd, timeout=3):
    try:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        ).stdout.strip()
    except Exception:
        return ""


def specs():
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        pass
    mem = sh("sysctl -n hw.memsize")
    data = {
        "chip": sh("sysctl -n machdep.cpu.brand_string") or "?",
        "cores": sh("sysctl -n hw.ncpu") or "?",
        "ram_gb": round(int(mem) / 1024**3) if mem.isdigit() else "?",
        "ram_bytes": int(mem) if mem.isdigit() else 0,
        "macos": sh("sw_vers -productVersion") or "?",
    }
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data))
    except Exception:
        pass
    return data


def battery():
    out = sh("pmset -g batt")
    b = {"raw": out, "pct": None, "state": "?", "remaining": "", "ac": "AC" in out}
    m = re.search(r"(\d+)%; ([a-z ]+);\s*([0-9:]+|\(no estimate\))?", out)
    if m:
        b["pct"] = int(m.group(1))
        b["state"] = m.group(2).strip()
        b["remaining"] = (m.group(3) or "").strip()
    return b


def draw():
    out = sh("ioreg -rn AppleSmartBattery -w0")
    amp = re.search(r'"InstantAmperage" = (\d+)', out) or re.search(r'"Amperage" = (\d+)', out)
    volt = re.search(r'"Voltage" = (\d+)', out)
    if amp and volt:
        a = int(amp.group(1))
        if a > 2**63:
            a -= 2**64
        watts = abs(a) / 1000 * int(volt.group(1)) / 1000
        if watts > 0.2:
            return watts, "measured"
    load = cpu_total()
    return 4.0 + load * 0.12, "estimated from load"


def cpu_total():
    out = sh("ps -Ao pcpu= -r")
    total = 0.0
    for line in out.splitlines():
        try:
            total += float(line)
        except ValueError:
            pass
    return total


def top_procs(n=5):
    out = sh(f"ps -Ao pcpu,rss,comm -r | head -{n + 1}")
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        pcpu, rss, comm = parts
        rows.append((pcpu, f"{int(rss) / 1024:.0f}M", comm.rsplit("/", 1)[-1]))
    return rows


def memory():
    out = sh("memory_pressure -Q")
    free = re.search(r"free percentage: (\d+)%", out)
    pct = int(free.group(1)) if free else None
    total = specs().get("ram_bytes", 0)
    used = f"{total * (100 - pct) / 100 / 1024**3:.1f}G" if pct is not None and total else "?"
    return pct, used


def silicon():
    out = sh("sudo -n powermetrics --samplers cpu_power -i 300 -n 1 2>/dev/null", timeout=4)
    if "CPU Power" not in out:
        return None
    grab = lambda pat: (re.search(pat, out) or [None, None])[1]
    return {
        "cpu_mw": grab(r"CPU Power: (\d+) mW"),
        "gpu_mw": grab(r"GPU Power: (\d+) mW"),
        "e_act": grab(r"E-Cluster HW active residency:\s+([\d.]+)%"),
        "p_act": grab(r"P-Cluster HW active residency:\s+([\d.]+)%"),
    }


def thermal():
    out = sh("pmset -g therm")
    if not out or "No thermal warning level" in out:
        return "nominal"
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
    return f"cpu limit {m.group(1)}%" if m else out.splitlines()[0]


def strip():
    b = battery()
    w, _ = draw()
    src = "ac" if b["ac"] else "battery"
    bits = [f"{b['pct']}%" if b["pct"] is not None else "?", src, f"{w:.1f}W"]
    if b["remaining"] and ":" in b["remaining"]:
        bits.append(f"{b['remaining']} left")
    return "  ".join(bits)


def panel(s):
    b = battery()
    w, kind = draw()
    pct, used = memory()
    load = sh("sysctl -n vm.loadavg").strip("{} ").split()
    total_cpu = cpu_total()

    t = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, show_header=False)
    t.add_column(style="label", width=10)
    t.add_column(style="text")
    charge = f"[hot]{b['pct']}%[/hot]" if b["pct"] is not None else "?"
    line = f"{charge} [faint]{b['state']}[/faint]"
    if b["remaining"] and ":" in b["remaining"]:
        line += f"[faint], {b['remaining']} remaining[/faint]"
    t.add_row("battery", line)
    t.add_row("draw", f"[accent]{w:.1f} W[/accent] [faint]({kind})[/faint]")
    t.add_row("source", "ac adapter" if b["ac"] else "battery")
    t.add_row(
        "cpu",
        f"{total_cpu:.0f}% busy[faint], load {' '.join(load[:3])}, {s['cores']} cores[/faint]",
    )
    t.add_row(
        "memory",
        f"{used} used [faint]of {s['ram_gb']}G, {pct if pct is not None else '?'}% free[/faint]",
    )
    si = silicon()
    if si and si["cpu_mw"]:
        t.add_row("silicon", f"[accent]cpu {si['cpu_mw']} mW[/accent][faint], gpu {si['gpu_mw']} mW[/faint]")
        t.add_row("cores", f"E {si['e_act']}% active[faint], P {si['p_act']}% active[/faint]")
    t.add_row("thermal", thermal())

    p = Table(box=box.SIMPLE, pad_edge=False, show_edge=False, header_style="label")
    p.add_column("cpu%", style="accent", width=6, justify="right")
    p.add_column("rss", style="faint", width=7, justify="right")
    p.add_column("process", style="text")
    for pcpu, rss, comm in top_procs():
        p.add_row(pcpu, rss, comm)
    return Group(t, p)


def run(interactive=False):
    s = specs()
    header("power", f"{s['chip']}  {s['ram_gb']}GB  macOS {s['macos']}")
    if not sys.stdout.isatty():
        console.print(panel(s))
        return
    console.print("[faint]live, ctrl+c to exit[/faint]")
    try:
        with Live(panel(s), console=console, refresh_per_second=1) as live:
            while True:
                time.sleep(2)
                live.update(panel(s))
    except KeyboardInterrupt:
        pass
