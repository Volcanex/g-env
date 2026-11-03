#!/usr/bin/env python3
"""gcore compliance checker.

Scores a repo against the five gcore-compliant boxes (see gcore/conventions.md):

  1. Nervous system  - CLAUDE.md (+ AGENTS.md) where behaviour is non-obvious;
                        an auto-compiled DOCS index at the repo root.
  2. pip-installable  - pyproject.toml with [project] name/version/description.
  3. GitHub           - repo origin under the instance's GitHub owner.
  4. Self-monitoring  - [tool.gcore].depends declares external dependency ids
                        (or [] if none), so a lapse routes to the alert spine.
  5. Connections      - [[tool.gcore.connects]] declares how the repo connects
                        (to, via, flow, optional dir), or connects = [] if none.

Plus, reported as advisory: [tool.gcore] lineage, and managed:true in the
instance manifest.

The GitHub owner is G_GITHUB_OWNER, else the owner of the instance's own
origin; the instance is found through g-env's resolver (context/instance.py).

Stdlib only (works without tomllib on Python < 3.11 via a regex fallback).
Exits 0 if all five hard boxes pass, 1 otherwise.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "context"))
    from instance import instance
except (ImportError, IndexError):  # a copy of the skill, away from g-env
    instance = None

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"

SOURCE_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".sh", ".vue", ".svelte", ".html", ".css",
}
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".next", "target", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "coverage", ".idea", ".vscode", "migrations",
    "vendor", ".cache", "site-packages",
}
MAX_CANDIDATES = 15
VIAS = ("http", "sse", "websocket", "import", "path", "vendor", "file", "ssh", "db", "mcp")
DIRS = ("out", "in", "both")


def run_git(repo, *args):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def load_pyproject(path):
    """Return (data_dict_or_None, raw_text). data is None when no TOML parser
    is available: callers fall back to scanning raw_text."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for mod in ("tomllib", "tomli"):
        try:
            parser = __import__(mod)
            return parser.loads(text), text
        except ModuleNotFoundError:
            continue
        except Exception:
            return None, text
    return None, text


def check_nervous_system(repo, results, candidates):
    claude = repo / "CLAUDE.md"
    if claude.is_file():
        results.append((PASS, "Root CLAUDE.md present"))
        body = claude.read_text(encoding="utf-8", errors="replace")
        if "<!-- DOCS:START -->" in body and "<!-- DOCS:END -->" in body:
            results.append((PASS, "Root CLAUDE.md has the DOCS index block"))
        else:
            results.append((WARN, "Root CLAUDE.md missing the "
                                  "<!-- DOCS:START/END --> index block"))
    else:
        results.append((FAIL, "Root CLAUDE.md missing"))

    if (repo / "AGENTS.md").is_file():
        results.append((PASS, "Root AGENTS.md present"))
    else:
        results.append((WARN, "Root AGENTS.md missing (mirror CLAUDE.md or symlink it)"))

    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        d = Path(dirpath)
        # A dir with a SKILL.md is self-documenting; don't flag it or its internals.
        if "SKILL.md" in filenames:
            dirnames[:] = []
            continue
        if d == repo:
            continue
        documented = (d / "CLAUDE.md").is_file()
        has_source = any(Path(f).suffix in SOURCE_EXT for f in filenames)
        if has_source and not documented:
            candidates.append(str(d.relative_to(repo)))

    if candidates:
        shown = candidates[:MAX_CANDIDATES]
        extra = len(candidates) - len(shown)
        msg = "Dirs with code but no CLAUDE.md (judge which are non-obvious): " + ", ".join(shown)
        if extra:
            msg += f" (+{extra} more)"
        results.append((INFO, msg))


def check_pip_installable(repo, results):
    pp = repo / "pyproject.toml"
    if not pp.is_file():
        results.append((FAIL, "No pyproject.toml at repo root"))
        return None
    data, text = load_pyproject(pp)
    if data is not None:
        project = data.get("project", {})
        present = {k for k in ("name", "version", "description") if project.get(k)}
        has_tool_gcore = "g-core" in data.get("tool", {}) or "gcore" in data.get("tool", {})
    else:
        # No TOML parser: fall back to scanning the [project] section text.
        section = re.split(r"^\[", text, flags=re.M)
        proj = next((s for s in section if s.startswith("project]")), "")
        present = {k for k in ("name", "version", "description")
                   if re.search(rf"^\s*{k}\s*=", proj, re.M)}
        has_tool_gcore = bool(re.search(r"^\[tool\.(g-core|gcore)\]", text, re.M))

    missing = {"name", "version", "description"} - present
    if missing:
        results.append((FAIL, "pyproject.toml [project] missing: " + ", ".join(sorted(missing))))
    else:
        results.append((PASS, "pyproject.toml has [project] name/version/description"))

    if has_tool_gcore:
        results.append((PASS, "[tool.gcore] lineage declared"))
    else:
        results.append((WARN, "[tool.gcore] lineage not declared (optional but recommended)"))
    return None


def check_dependencies(repo, results):
    """Box 4: the repo declares its external dependencies in [tool.gcore].depends.
    Presence of the key is the signal the author considered it; [] is a valid,
    positive 'no external deps'. Catalog ids are validated by gmap, not here."""
    pp = repo / "pyproject.toml"
    if not pp.is_file():
        results.append((FAIL, "No pyproject.toml: cannot declare [tool.gcore].depends"))
        return
    data, text = load_pyproject(pp)
    if data is not None:
        gcore = data.get("tool", {}).get("g-core") or data.get("tool", {}).get("gcore", {})
        has_depends = "depends" in gcore
        depends = gcore.get("depends")
    else:
        # No TOML parser: look for a `depends =` line anywhere in [tool.gcore].
        has_depends = bool(re.search(r"^\s*depends\s*=", text, re.M))
        depends = None

    if not has_depends:
        results.append((FAIL, "[tool.gcore].depends not declared: list external "
                              "dependency ids from the manifest catalog, or [] if none"))
    elif depends == []:
        results.append((PASS, "[tool.gcore].depends = [] (no external deps, explicitly)"))
    elif depends:
        results.append((PASS, "[tool.gcore].depends declared: " + ", ".join(map(str, depends))))
    else:
        results.append((PASS, "[tool.gcore].depends declared"))


def github_owner():
    if os.environ.get("G_GITHUB_OWNER"):
        return os.environ["G_GITHUB_OWNER"]
    found = instance() if instance else None
    url = run_git(found, "remote", "get-url", "origin") if found else None
    m = re.search(r"github\.com[:/]+(?:[^@/]*@)?([^/]+)/", url or "")
    return m.group(1) if m else None


def check_github(repo, results, owner):
    url = run_git(repo, "remote", "get-url", "origin")
    if url is None:
        results.append((FAIL, "No git remote 'origin' (not a repo or no remote)"))
        return None
    if owner is None:
        results.append((FAIL, f"No GitHub owner known: set G_GITHUB_OWNER or give the instance a GitHub origin ({url})"))
    elif re.search(rf"github\.com[:/]+{re.escape(owner)}/", url, re.I):
        results.append((PASS, f"Remote under {owner} ({url})"))
    else:
        results.append((FAIL, f"Remote not under github.com/{owner} ({url})"))
    return url


def normalize_url(url):
    if not url:
        return ""
    return re.sub(r"\.git$", "", url.strip().rstrip("/")).lower()


def check_connections(repo, results, manifest_path):
    """Box 5: [[tool.gcore.connects]] says how the repo connects to the rest of the system.
    Each entry needs `to` (a manifest id or dependency catalog id), `via` (one of VIAS) and
    `flow`; `dir` is optional. connects = [] asserts none. The graph itself is
    gcore's `python3 -m gcore_graph`, which also reports drift against the code."""
    pp = repo / "pyproject.toml"
    if not pp.is_file():
        results.append((FAIL, "No pyproject.toml: cannot declare [[tool.gcore.connects]]"))
        return
    data, text = load_pyproject(pp)
    if data is None:
        if re.search(r"^\s*(\[\[tool\.gcore\.connects\]\]|connects\s*=)", text, re.M):
            results.append((PASS, "connects declared (no TOML parser: entries not validated)"))
        else:
            results.append((FAIL, "[[tool.gcore.connects]] not declared"))
        return
    gcore = data.get("tool", {}).get("gcore", {})
    if "connects" not in gcore:
        results.append((FAIL, "[[tool.gcore.connects]] not declared: one table per connection "
                              "(to, via, flow), or connects = [] if none"))
        return
    connects = gcore["connects"]
    if connects == []:
        results.append((PASS, "connects = [] (no connections, explicitly)"))
        return
    ids = None
    mpath = find_manifest(repo, manifest_path)
    if mpath:
        try:
            m = json.loads(mpath.read_text(encoding="utf-8"))
            ids = {e.get("id") for key in ("hosts", "services", "dependencies", "projects", "system")
                   for e in (m.get(key) or []) if isinstance(e, dict)}
        except (OSError, json.JSONDecodeError):
            pass
    if ids is None:
        results.append((WARN, "manifest not found: `to` ids not checked"))
    bad = 0
    for i, c in enumerate(connects):
        where = f"connects[{i}] to={c.get('to')!r}"
        problems = []
        if not c.get("to"):
            problems.append("no `to`")
        elif ids is not None and c["to"] not in ids:
            problems.append("`to` is not a manifest or catalog id")
        if c.get("via") not in VIAS:
            problems.append(f"`via` must be one of {', '.join(VIAS)}")
        if not str(c.get("flow") or "").strip():
            problems.append("no `flow` (say what moves)")
        if c.get("dir", "out") not in DIRS:
            problems.append("`dir` must be out, in or both")
        if problems:
            bad += 1
            results.append((FAIL, f"{where}: " + "; ".join(problems)))
    if not bad:
        results.append((PASS, f"{len(connects)} connection(s) declared: "
                              + ", ".join(f"{c['to']} ({c['via']})" for c in connects)))


def find_manifest(repo, manifest_path):
    candidates = [Path(manifest_path)] if manifest_path else []
    candidates += [
        repo.parent / "gp-core" / "manifest.json",
        repo.parent / "gcore" / "manifest.json",
        Path.home() / "gp-core" / "manifest.json",
        Path.home() / "gcore" / "manifest.json",
        instance("manifest") if instance else None,
    ]
    return next((c for c in candidates if c and c.is_file()), None)


def check_manifest(repo, remote_url, manifest_path, results):
    mpath = find_manifest(repo, manifest_path)
    if mpath is None:
        results.append((WARN, "gcore/manifest.json not found (pass --manifest to check managed status)"))
        return

    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        results.append((WARN, f"Could not read manifest ({mpath}): {e}"))
        return

    repo_id = repo.name
    target = normalize_url(remote_url)

    for entry in manifest.get("system", []):
        if entry.get("id") == repo_id or normalize_url(entry.get("url")) == target:
            results.append((PASS, f"Listed as a system repo in manifest ({entry.get('id')}); "
                                  "system repos are managed by definition"))
            return
    for entry in manifest.get("projects", []):
        if entry.get("id") == repo_id or normalize_url(entry.get("url")) == target:
            if entry.get("managed") is True:
                results.append((PASS, f"Listed in manifest projects with managed:true ({entry.get('id')})"))
            else:
                results.append((WARN, f"In manifest projects but managed is {entry.get('managed')!r} "
                                      f"({entry.get('id')}). Flip to true once the boxes pass"))
            return
    results.append((WARN, f"Repo '{repo_id}' not found in manifest projects/system: add it, then set managed:true"))


def main():
    ap = argparse.ArgumentParser(description="Check a repo against the gcore-compliant checklist.")
    ap.add_argument("repo", nargs="?", default=".", help="Path to the repo (default: cwd)")
    ap.add_argument("--manifest", help="Path to gcore/manifest.json")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 2

    print(f"gcore compliance: {repo}\n")

    boxes = []

    ns, candidates = [], []
    check_nervous_system(repo, ns, candidates)
    boxes.append(("1. Nervous system", ns))

    pip = []
    check_pip_installable(repo, pip)
    boxes.append(("2. pip-installable", pip))

    gh = []
    owner = github_owner()
    remote = check_github(repo, gh, owner)
    boxes.append((f"3. GitHub ({owner or 'owner unknown'})", gh))

    dep = []
    check_dependencies(repo, dep)
    boxes.append(("4. Self-monitoring (depends)", dep))

    conn = []
    check_connections(repo, conn, args.manifest)
    boxes.append(("5. Connections (connects)", conn))

    mf = []
    check_manifest(repo, remote, args.manifest, mf)
    boxes.append(("manifest / managed", mf))

    hard_fail = False
    for title, results in boxes:
        print(title)
        for level, msg in results:
            print(f"  [{level}] {msg}")
            if level == FAIL and not title.startswith("manifest"):
                hard_fail = True
        print()

    if hard_fail:
        print("RESULT: NOT compliant. Fix the [FAIL] items above.")
        return 1
    print("RESULT: all five hard boxes pass. Address [WARN]s, then set managed:true in the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
