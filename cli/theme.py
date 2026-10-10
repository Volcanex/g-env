from rich.console import Console
from rich.theme import Theme

ACCENT = "#ff4fd8"

console = Console(
    theme=Theme(
        {
            "accent": ACCENT,
            "hot": "bold white",
            "text": "#c9c9c9",
            "label": "#8a8a8a",
            "faint": "#5a5a5a",
            "ok": "#8a8a8a",
            "warn": ACCENT,
            "down": f"bold {ACCENT}",
            "unknown": "#4a4a4a",
        }
    ),
    highlight=False,
)

STATE_STYLE = {"up": "ok", "ok": "ok", "warn": "warn", "down": "down"}
STATE_MARK = {"up": " ", "ok": " ", "warn": "!", "down": "x"}


def trim(text, width):
    text = (text or "").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def state_style(state):
    return STATE_STYLE.get(state, "unknown")


def state_mark(state):
    return STATE_MARK.get(state, "?")


def header(title, note=""):
    console.print()
    line = f"[accent]genv[/accent] [faint]/[/faint] [hot]{title}[/hot]"
    if note:
        line += f"   [faint]{note}[/faint]"
    console.print(line)
    console.print("[faint]" + "─" * min(console.width, 78) + "[/faint]")
