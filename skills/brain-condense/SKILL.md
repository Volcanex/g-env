---
name: brain-condense
description: "Condense the brain: merge notes covering the same territory, rewrite notes that have accreted into a stack of dated appends, link orphans, fix frontmatter drift. Use when the session-start orientation reports a condensation backlog, when the spine pages about one, or when the operator says the brain has got messy. Do not use for capturing new knowledge (that is brain_note)."
user-invocable: true
---

# brain-condense

Habitual capture keeps the brain full. This keeps it *readable*. They are
opposite pressures and the second one only happens deliberately.

`brain_condense.py` runs daily and finds the candidates. It never writes:
merging notes is a judgement about what to keep and what to drop, and the
commit fence blocks unattended actors from destructive ops precisely so that
judgement happens where someone can be asked. **You are that half.**

## Read the report first

```bash
curl -s http://127.0.0.1:8766/api/condense | python3 -m json.tool
```

Or regenerate it if it looks stale:

```bash
cd /path/to/brain-tools && BRAIN_ROOT=/path/to/brain .venv/bin/python brain_condense.py -v
```

Five signals. **`stale` is the one that matters**. A note can be perfectly
well-formed, well-linked and unique while being simply wrong, and wrong is the
only failure that actively misleads the next session.

| Signal | What it means | What it costs to fix |
|---|---|---|
| `stale` | describes a fossil project, cites a path not on disk, names something retired | `mark_broken` (unfenced) or a rewrite |
| `drift` | non-canonical `kind`, missing `summary` | frontmatter `edit_minor`: destroys nothing, just fix it |
| `accreted` | ≥3 dated appends; a capture log, not prose | rewrite the body; `edit_minor` if the substance survives |
| `overlaps` | two notes on the same territory | judgement: often the answer is "leave them" |
| `orphans` | nothing links in or out | usually a missing wikilink, not a dead note |

## How to work

**Start with `stale`, and expect a lot of it.** The detector only catches what
is mechanically checkable against gcore: a fossil project named in a technical
context, a `/home/<user>/...` path that no longer exists, a retired term. Real
outdatedness is broader than that, so treat the list as an entry point, not the
full set: while you are in a cluster, read the neighbouring notes too.

`confidence: high` means old *and* contradicted. `check` means contradicted but
recent enough that it may be a deliberate mention of history. Read before
acting.

The fix depends on what's wrong:

- **Superseded by reality, content still useful as history** → `mark_broken`
  with a reason. Unfenced on purpose: flagging a note as no-longer-true
  destroys nothing, and leaving a wrong note unflagged is the costlier failure.
  Do it freely.
- **Mostly right, one section moved on** → `supersede_section`, or an
  `edit_minor` that states what changed and when.
- **Whole note is about something that no longer exists** → say so in the note
  rather than deleting it. The brain never deletes; history is the point.

A worked example of the shape: `recipes` went fossil on 2026-03-02, folded into
`kitchen`. The nine `recipes-app-*` notes aren't wrong about the scaling model.
They're wrong about where it lives and what it's called. That's a header
correction and a wikilink to the `kitchen` design, not nine `mark_broken` calls.

**Then drift.** It's mechanical, it's free, and the indexes render `summary`,
so a note without one is invisible in navigation. Fix all of them in one pass, no
permission needed, no discussion.

**Then accreted notes.** These are the specific debt `brain_note`'s append path
creates: each capture thickened the note, which is right for capture and wrong
for reading. Rewrite the body into prose that says what is true *now*, keeping
every distinct fact. If the substance is unchanged and you are only
reorganising, that is an `edit_minor`: your call, no ask. If you are dropping
material, say so.

**Overlaps need the most judgement and usually the least action.** High token
overlap means shared territory, not duplication: `recipes-app-unit-model` and
`recipes-app-batch-model` score 0.26 and are correctly two notes. Merge only when
one note genuinely subsumes the other. When in doubt, add a wikilink between
them instead: that fixes navigability without destroying anything.

**Orphans are usually a linking problem.** Before concluding a note is dead,
check whether something *should* link to it and doesn't.

## The gate

Most of this is yours to do. Permission is a rare gate, reserved for writes
that are genuinely transformative:

- **`supersede`**: always asks. It only applies to the four canonical
  folder-shaped designs and archives a major version.
- **`edit_major` on a note with ≥8 dependents**: asks. On one instance that was
  `designs/storefront`, `designs/roadmap-h2-2026`,
  `references/supplier-api`, `references/search-suite` and three others.
- **Everything else**: your judgement. Commit it and say what you did.

When you do ask, ask once with the whole batch: "these three merges, here's
what changes and why." Twelve separate questions is how a gate becomes a
rubber stamp.

Set `assent=True` on the op after the operator says yes, and keep `actor="agent"`:
assent records that they approved it, actor records that you wrote it.

## Finish

Rebuild the indexes (`rebuild_indexes()`), re-run `brain_condense.py` so the
report reflects the work, and report what you merged, rewrote and left alone,
including what you deliberately did not touch and why. "Left alone" is a real
outcome here, not a gap.
