---
name: g-design
description: "The g-design language contract (the house design language, formerly g-system). Use for ANY frontend or UI work on a g project: building pages, styling, components, copy in interfaces, reskins. Covers the component hierarchy (use before modify before create), the hard voice and colour rules, and how to vendor the assets."
---

# g-design

g-design (`/home/gabriel/g-design`, `Volcanex/g-design`, formerly g-system) is the house design
language. Every g project's UI is built with it. This skill is the contract;
the repo's `apps/docs` cards are the reference; its `CLAUDE.md` has the
build gotchas.

## Getting the assets

From the g-design repo:

    node scripts/vendor.mjs <project>/static/g

Then in the page head:

    <link rel="stylesheet" href="/static/g/g.css">
    <script defer src="/static/g/g-ui.js"></script>

That is the whole install. `g.css` carries fonts (subsetted, metric-matched
fallback), tokens and every component style; `g-ui.js` carries behaviour
(theme, tooltips, tabs, menus, toasts, stateful buttons, progress, tables,
charts, the `.g-shell` responsive app shell, `g.swap` view transitions).
Declare `design = "g-system@<version>"` in the project's `[tool.g-core]` (or `[tool.gcore]`); the label keeps the old id on purpose, lineage links key on it.
The vendored files are stamped and never hand-edited; update by re-running
vendor.mjs.

## The component hierarchy (not a suggestion)

When you need a piece of UI, in strict order:

1. **Use** the existing component exactly as it ships. Check the docs cards
   first (`apps/docs/components/`, `apps/docs/guidelines/`); assume it
   exists before assuming it does not. Buttons, inputs, select, combobox,
   slider, progress, meter, dropzone, tables, pagination, context menu,
   toasts, tooltips, tabs, menus, badges, avatars, cards, charts, prose,
   the app shell: all exist.
2. **Adjust** only what it exposes: size classes (`btn-sm` etc.), density,
   label text, spacing tokens, the accent slot (`--accent-fill/-hover/-fg/
   -edge`) for a rebrand.
3. **Extend**: a new variant composed from the existing tokens and
   patterns, following a neighbouring component's structure.
4. **Create new** only when nothing close exists. Then it is built IN
   g-design (`packages/g-ui/src/components/` plus a docs card), never as a
   one-off in the consumer repo. A bespoke component living in a project
   is a defect to flag.

## Hard rules (enforced, not stylistic preference)

- **Sentence case everywhere.** A leading capital, then lowercase: labels,
  buttons, headings, badges, menu items, toasts. No Title Case, no ALL
  CAPS, no all-lowercase affectation.
- **Em dashes are banned** in all UI copy and code comments. Use a comma,
  a colon or a rewrite; plain hyphens only in technical terms.
- **Copy goes through anti-ai-voice.** Headlines, body text, empty states
  and anything else a person reads gets `ganti score`; fix the absolutes and
  generic tells, and lean toward Gabriel's voice in the page's register.
- **The middle dot is banned** on the same standing, in every reskin. It
  is a glyph doing a layout job. Separate two facts with a gap off the
  spacing scale, a second column or a new line, or join them with words.
- **Layout lands on the 4px grid.** Spacing (`--sp-*`), pixel
  line-heights (`--lh-*`, never a ratio), control and row heights
  (`--h-*`), the page gutter (`--gutter`) and the measures (`--col-*`)
  all come off it, and a slot that is usually empty keeps its size so its
  neighbours never move. The card is `guidelines/layout.card.html`; check
  a page with `node packages/g-ui/scripts/rhythm-audit.mjs <url>` from the
  g-design repo.
- **Greys only through the alias tokens** (`--bg`, `--surface`,
  `--text-primary`, `--text-secondary`, `--border`, ...). Never raw hex,
  never raw Radix scale steps. Status text and chart colours are derived
  tokens (`--ok-text`, `--chart-1..6`); never point at scale steps.
- **Dark is the default** (`:root` with no attribute). `data-theme="light"`
  and `data-theme="system"` are the other modes; theme islands work
  because aliases are declared on `:root, [data-theme]`.
- **Buttons are verb-first** ("Save changes", "Upload six assets") and
  keep the same word through a flow: a Publish button makes a Published
  toast.
- **Motion uses the shipped springs and durations.** Stateful buttons,
  toasts, entrances and `g.swap` already animate correctly; do not invent
  timings. Respect the global reduced-motion block. Do not animate
  frequent actions, typing feedback or hover-only affordances.
- **Numbers are tabular** (`.num`, `font-variant-numeric: tnum`) anywhere
  they update in place.
- **Errors say what happened and what happens next**, no apology, no
  exception strings.
- **Markdown renders only inside `.prose`.** Every other slot is plain
  text: table cells, badges, page headings, tooltips, menu items, chart
  labels. Authored text arrives carrying `**`, backticks and
  `[[wikilinks]]` far more often than you expect, because titles and
  summaries get lifted from the first line of a body, so flatten at the
  boundary with `g.plain(text)` instead of trusting the source. Watch the
  truncated case in particular: a title cut short keeps its opening `**`
  and never meets a closer.

## Looking deliberate, not generated

The guidance half of the `correct.ui.on-system` gate. "It looks vibe coded" means
a default crept in where the system had an answer.

- Every font, size, colour, radius and shadow comes from the system's tokens. A
  bigger font is not a redesign; a raw hex or a locally restyled button is drift.
- A project on its own system (Baseline's /design) follows that system the same
  way: its tokens, its components, its imagery rules.
- None of the generic AI defaults the system does not use: gradient text, purple
  or cyan glow palettes, side-tab accent borders, hairline borders with wide
  shadows, badges above a hero headline, icon tiles over every card heading,
  bounce or elastic easing, decorative pulsing dots.
- Surfaces contrast: a background the same colour as the panels on it is a fault.
- Hierarchy: the key information at a glance, the detail on demand. A one-line
  summary, then the rest behind a disclosure. Errors and logs collapse into a
  list, never across the page. Nothing said twice, nothing mirrored, no wall of
  text where a table, chart or list would do.

Check with `python3 /home/gabriel/g-levels/glevels/onsystem.py --refs <page or URL>`:
it blocks on off-system fonts, a page that loads no house system, unambiguous AI
tells and raw failure text, lists drift as leads, and renders the house reference
screens beside the work for an independent judge.

## Rebranding a project

A reskin overrides ONLY the accent slot in a scope, exactly like the
rebrand docs card. Greys, spacing, type and motion stay g-design. If a
rebrand seems to need more than the accent slot, stop and raise it.

## Verifying

Serve over local HTTP (module scripts and fonts break on file://),
screenshot with Playwright, check dark and light. Zero console errors is
the bar.
