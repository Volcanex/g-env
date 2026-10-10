#!/usr/bin/env python3.12
"""Environment dashboard: keybindings, MCPs, .env passport, live configs."""

import json
import os
import re
import sys
import tomllib
from pathlib import Path

from bottle import Bottle, template, request, static_file, TEMPLATE_PATH

app = Bottle()
TEMPLATE_PATH.insert(0, str(Path(__file__).parent / "templates"))

ENV = Path(__file__).parent.parent  # ~/Desktop/genv/
sys.path.insert(0, str(ENV / "context"))
from instance import service_url  # noqa: E402
GCORE = Path.home() / "Desktop" / "gcore"
DASH = Path(__file__).parent

MANIFEST = GCORE / "manifest.json"
PRESENTATION = GCORE / "presentation.json"


def load_manifest():
    return json.loads(MANIFEST.read_text())


def save_manifest(data):
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n")


def load_presentation():
    return json.loads(PRESENTATION.read_text())


def save_presentation(data):
    PRESENTATION.write_text(json.dumps(data, indent=2) + "\n")


def find_workspace(manifest, ws):
    return next((w for w in manifest["workspaces"] if str(w["number"]) == str(ws)), None)


def load_aerospace_bindings():
    toml_path = ENV / "aerospace" / "aerospace.toml"
    if not toml_path.exists():
        return []
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    bindings = data.get("mode", {}).get("main", {}).get("binding", {})
    rows = []
    for key, action in bindings.items():
        if isinstance(action, list):
            action = " → ".join(action)
        rows.append({"key": key, "action": action})
    return rows


def load_ghostty_keybinds():
    cfg_path = ENV / "ghostty" / "config.ghostty"
    if not cfg_path.exists():
        return []
    binds = []
    for line in cfg_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("keybind"):
            m = re.match(r"keybind\s*=\s*(.+?)=(.+)", line)
            if m:
                binds.append({"key": m.group(1).strip(), "action": m.group(2).strip()})
    return binds


def load_tmux_bindings():
    cfg_path = ENV / "tmux" / "tmux.conf"
    if not cfg_path.exists():
        return []
    binds = []
    for line in cfg_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("bind ") or line.startswith("bind-key "):
            parts = line.split(None, 2)
            if len(parts) >= 3:
                binds.append({"key": f"prefix + {parts[1]}", "action": parts[2]})
            elif len(parts) == 2:
                binds.append({"key": f"prefix + {parts[1]}", "action": ""})
    return binds


def load_claude_settings():
    path = ENV / "claude" / "settings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_mcps():
    settings = load_claude_settings()
    servers = settings.get("mcpServers", {})
    if not servers:
        # try settings.local.json
        local = ENV / "claude" / "settings.local.json"
        if local.exists():
            servers = json.loads(local.read_text()).get("mcpServers", {})
    result = []
    for name, cfg in servers.items():
        result.append({
            "name": name,
            "command": cfg.get("command", ""),
            "args": " ".join(cfg.get("args", [])),
        })
    return result


def load_env_passport():
    env_path = GCORE / ".env"
    if not env_path.exists():
        return []
    entries = []
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            masked = "•" * min(len(val), 12) if val else "(empty)"
            entries.append({"key": key.strip(), "masked": masked, "set": bool(val)})
    return entries




def tint(accent: str, base: str = "#0d0d0d", alpha: float = 0.05) -> str:
    """Blend accent colour into a dark base at low opacity."""
    def parse(h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    ar, ag, ab = parse(accent)
    br, bg, bb = parse(base)
    r = int(br * (1 - alpha) + ar * alpha)
    g = int(bg * (1 - alpha) + ag * alpha)
    b = int(bb * (1 - alpha) + ab * alpha)
    return f"#{r:02x}{g:02x}{b:02x}"


def load_workspaces():
    import subprocess
    manifest = load_manifest()
    presentation = load_presentation()
    by_num = {str(w["number"]): w for w in manifest["workspaces"]}
    ws_pres = presentation.get("workspaces", {})
    host_pres = presentation.get("hosts", {})
    host_display = {h["id"]: host_pres.get(h["id"], {}).get("display_name", h["id"]) for h in manifest["hosts"]}

    workspaces = []
    for ws in [str(i) for i in range(10)]:
        entry = by_num.get(ws, {})
        ws_file = Path.home() / f".ae-workspace-{ws}"
        ssh_host = ws_file.read_text().strip() if ws_file.exists() else entry.get("host")

        try:
            result = subprocess.run(
                ["/opt/homebrew/bin/aerospace", "list-windows", "--workspace", ws],
                capture_output=True, text=True, timeout=2
            )
            win_count = len([l for l in result.stdout.splitlines() if l.strip()])
        except Exception:
            win_count = 0

        accent = ws_pres.get(ws, {}).get("accent_color", "#000000")
        workspaces.append({
            "ws": ws,
            "label": entry.get("name", "Local"),
            "ssh_host": ssh_host,
            "ssh_name": host_display.get(ssh_host, ssh_host) if ssh_host else None,
            "start_path": entry.get("start_path", ""),
            "accent": accent,
            "tint": tint(accent),
            "windows": win_count,
            "active": win_count > 0,
        })
    return workspaces


def load_ssh_config():
    """Parse ~/.ssh/config for Host blocks."""
    config_path = Path.home() / ".ssh" / "config"
    if not config_path.exists():
        return []
    hosts = []
    current = {}
    for line in config_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("host ") and not line.lower().startswith("hostname"):
            if current:
                hosts.append(current)
            current = {"host": line.split(None, 1)[1], "options": {}}
        elif current and " " in line:
            k, _, v = line.partition(" ")
            current["options"][k.lower()] = v.strip()
    if current:
        hosts.append(current)
    return hosts


@app.route("/static/<filename>")
def serve_static(filename):
    return static_file(filename, root=str(DASH / "static"))


@app.route("/")
def index():
    manifest = load_manifest()
    presentation = load_presentation()
    host_pres = presentation.get("hosts", {})
    ssh_names = {h["id"]: host_pres.get(h["id"], {}).get("display_name", h["id"]) for h in manifest["hosts"]}
    return template(
        "index",
        aerospace=load_aerospace_bindings(),
        ghostty=load_ghostty_keybinds(),
        tmux=load_tmux_bindings(),
        mcps=load_mcps(),
        env_passport=load_env_passport(),
        workspaces=load_workspaces(),
        ssh_config=load_ssh_config(),
        ssh_names=ssh_names,
        claude_settings=load_claude_settings(),
        command_url=service_url("gcommand"),
    )


@app.route("/api/env")
def get_env():
    env_path = ENV / ".env"
    if not env_path.exists():
        return {}
    vals = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        vals[key.strip()] = val.strip()
    return vals


@app.route("/api/workspace", method="POST")
def update_workspace():
    data = request.json
    ws = str(data.get("ws"))
    manifest = load_manifest()
    presentation = load_presentation()
    entry = find_workspace(manifest, ws)
    if entry is None:
        entry = {"number": int(ws), "name": "Local", "host": None, "start_path": ""}
        manifest["workspaces"].append(entry)
        manifest["workspaces"].sort(key=lambda w: w["number"])
    if "label" in data:
        entry["name"] = data["label"]
    if "ssh_host" in data:
        host = data["ssh_host"].strip()
        entry["host"] = host or None
        ae_file = Path.home() / f".ae-workspace-{ws}"
        if host:
            ae_file.write_text(host)
        elif ae_file.exists():
            ae_file.unlink()
    if "start_path" in data:
        entry["start_path"] = data["start_path"].strip()
    if "accent" in data:
        presentation.setdefault("workspaces", {}).setdefault(ws, {})["accent_color"] = data["accent"].strip()
    save_manifest(manifest)
    save_presentation(presentation)
    return {"ok": True}


if __name__ == "__main__":
    app.run(debug=True, port=5555)
