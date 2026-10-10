---
name: g-gcore-comply
description: "Check and bring a repo into gcore compliance (managed:true): the four boxes from gcore/conventions.md: nervous system, pip-installable, GitHub under the instance's owner, and self-monitoring (tool.gcore.depends), plus lineage and the manifest entry. Use when onboarding a repo into the g-system or auditing one that should be managed."
---

# g-gcore-comply

Audits a repo against the **gcore-compliant** definition and brings it the rest of
the way. A repo is compliant (`managed: true` in `gcore/manifest.json`) only when all
four boxes are ticked. The definition lives in `gcore/conventions.md`. That file is
the source of truth; this skill operationalises it.

## The four boxes

1. **Nervous system**: `CLAUDE.md` (and `AGENTS.md`) in every directory with
   non-obvious behaviour; nested dirs get nested docs. The repo root's `CLAUDE.md`
   ends with the auto-compiled `<!-- DOCS:START --> ... <!-- DOCS:END -->` index.
2. **pip-installable**: `pyproject.toml` at root with at least `[project]`
   name, version, description.
3. **GitHub**: repo under the instance's GitHub owner (the owner of the instance's
   own origin, or `G_GITHUB_OWNER`).
4. **Self-monitoring**: `[tool.gcore].depends` lists the repo's external
   dependencies by catalog id (or `[]` if it has none), so a lapse routes to the
   alert spine. The catalog lives in `manifest.json → dependencies`.

Plus: declare lineage in `[tool.gcore]` (optional but recommended), and add the repo
to `gcore/manifest.json` with `managed: true`.

## Workflow

1. **Audit.** Run the checker against the target repo:
   ```bash
   python3 scripts/check.py /path/to/repo
   # or, from inside the repo:  python3 .../g-gcore-comply/scripts/check.py
   ```
   It reports `[PASS]/[FAIL]/[WARN]/[INFO]` per box and exits non-zero if any hard
   box fails. `[INFO]` lists dirs that contain code but no `CLAUDE.md`. These are
   *candidates*, not automatic failures: judge which ones have genuinely non-obvious
   behaviour (a hidden constraint, a gotcha, a convention) and document those.

2. **Remediate the FAILs**, in order:
   - *No pyproject.toml* → scaffold one with `[project]` name/version/description.
     Add `[tool.gcore]` lineage if a starter/design was used (e.g.
     `starter = "gseed-app@1.0.0"`). Omit lineage keys rather than inventing them.
   - *Missing CLAUDE.md* → write one per the convention: say what is **not** implicit
     in the code (owner, what it does, how to run it, gotchas). Don't repeat what the
     code already tells you. Keep root CLAUDE.md under ~300 lines and link down to
     child docs. Mirror to `AGENTS.md` (edit both, or write one and symlink). No
     standalone `.md` files, no `docs/` dir, no emoji.
   - *Remote not under the owner* → this is usually a real blocker, not a quick fix.
     Surface it; don't silently re-point a remote.
   - *`[tool.gcore].depends` not declared* → add it to `[tool.gcore]`. List the
     external dependencies this repo relies on by their catalog id (from
     `manifest.json → dependencies`), e.g. `depends = ["openrouter"]`. If the repo
     genuinely has no external dependencies, declare `depends = []`. The empty list
     is a positive assertion, not the same as omitting the key. If a dependency isn't
     in the catalog yet, add a catalog entry (with a `probe`) first, then reference it.

3. **Re-run the checker** until the four boxes pass.

4. **Register it.** Add/confirm the repo in `gcore/manifest.json` under `projects`
   with `managed: true` (system repos live under `system` and are managed by
   definition, no `managed` field). If the repo root has a `compile_docs.py`, run it
   so the DOCS index reflects any new/renamed `CLAUDE.md` files.

## Notes

- The checker is stdlib-only and works without `tomllib` (regex fallback for
  Python < 3.11), so it runs on the Ubuntu hosts and on a Mac.
- It auto-locates the manifest in `gp-core` or `gcore` next to the repo or under `~`,
  then through g-env's instance resolver. Pass `--manifest PATH` if it lives elsewhere.
- Compliance is partly judgment (which dirs are "non-obvious"). The checker flags
  candidates; the decision is yours.
