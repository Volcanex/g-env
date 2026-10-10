# Shared context implementation

`shared.md` is the mandatory short policy; `reference.md` is supporting detail.
Keep permanent context code here, separate from disposable `agents/` briefs.
`session-context.py` owns source loading, import expansion and both hook outputs.
`instance.py` locates the operator's instance; `bin/g-instance` is its shell twin.

Nothing is truncated: sections render whole, sources choose their session
excerpt with SESSION markers, and reference detail sits behind pointers. Keep the
total under `TARGET_CHARS` by trimming sources, not by cutting output; the tests
enforce it. Pulse shows priority entries only; Brain one orientation line plus
actionable housekeeping. A failing section degrades to one line. Read source paths
live, without Claude.

## Value model

`value.py` ranks each model and effort with `stakes * q - lambda * c - mu * log10(max(ttft_s, 1))`.
`c` is cost relative to opus-medium or sol-medium. Prices use the tightest 5h/7d burn
over affordable burn, rescaled from the pace file's 5% landing to Claude's 15% reserve
and Codex's 0% reserve at its own reset: `lambda = BASE * r ** 2`,
`mu = 2 * max(0, 1 - r)`. FAST adds 3 to mu; SLOW multiplies lambda by 0.6 and zeros mu.
The ladders live in `models.json`, role families and quality floors in `providers.json`
under `value`, and bases, reserves, stakes and override ratios in `value.py`.
Live temperature and speed overrides come from `compute-mode.json` (the compute page or `g-temp`); measured Claude reaches BURN only with x5 wall-clock headroom on its week too. Picks stay within
two quality ranks above the normal pick. Cold Claude needs `--allow-claude`, critical
stakes, or `--claude-only` for MCP/private data jobs. `bin/g-pick --grid` shows the choices.
A role's `weight` scales its q (gather and bulk 0.15: they need throughput, not intelligence,
so Luna wins unless Codex is spare). A role's `taste` adds that many quality points per point
of model taste. `tastes()` derives taste from models.json: Design Arena overall Elo beyond what AA
predicts (a line fit of DA on AA), in AA points; `taste_note` defines it. `design` uses 2, so
Opus wins one-shot UI and layout work unless Claude is near Freezing, when Astra takes it, and
the cold gate skips taste roles. Retune by updating `scores.DA`.
Stakes buy at most two rungs up the normal pick's own provider ladder. `value_domains.ui` ranks
any role as `design`.
`explain(role, stakes, domain)` returns every point with `eligible` and the reason it sits out
(family, floor, Claude-only, cold gate, quality cap), from the same checks `_rank` uses; the
selection widget on gcommand's /compute page reads it, so the page and `g-pick` cannot
disagree. Each point also carries `parts` (quality, taste, price, speed summing to the score), and
`rivals` lists the runner-up and each provider's best. `flips(role, stakes)` finds the smallest
change of stakes, temperature or speed (singles, then pairs) that makes each rival win.
`overrides={'claude': {'temp', 'speed'}}` on `prices`, `rank`, `explain` and `flips` previews a
mode without saving it.

## Grades

`grades.py` turns the ladders in `models.json` into four-character codes for the g-deck
mod: quality letter A-F in 5-point AA bands below the best score, cost digit
`round(log2($/task / $0.01))` clamped 0-9, speed F/M/S/X at TTFT 5s/30s/2min, and a taste
mark from absolute Design Arena Elo (`∴` 1440+, `:` 1340+, `.` below). `--json` adds the
provider temperatures.

Validate with `python3 -m pytest -q scripts` from the repo root.
Use fixture homes and mocked Brain responses; no live bootstrap in tests.
