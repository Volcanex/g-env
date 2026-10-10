# Agent reference

Read on demand. The injected session context (shared.md, gcore conventions) owns
the standing rules; this file holds the detail behind them and does not repeat them.

## Session start

Only report measured counts; available skills are not loaded skills. If the live
orientation is absent, report that and load relevant context through the host
fallback or Brain tools.

## Research detail

Open relevant sources; search snippets are insufficient. Prefer official docs,
maintainer repositories and primary papers. Check date, version, licence,
dependencies and integration fit. Separate measured results from vendor advice and
your inference. Reuse compatible solutions and verify them in this repo and along
the user's actual path. Purely local edits with no external dependency question
need no ritual search. Search Brain for past decisions and use `gh` for GitHub.

Duplication matters when independent copies can drift; name that coupling risk.
When pushing back on a design, explain how it could break in six months.

## Model routing and small waves

Use the cheapest available model that can do the bounded job reliably, and spend
Codex before Claude: Claude is for orchestration, integration and nuance. Most of
the week to 2026-10-05 went on Opus implementers running 100-160 turns at 200-366K
context; that shape belongs on Sol. Sol and Luna jobs run as background Bash calls, not subagents:
`~/.local/bin/g-agent-call --role explore|research|build --block --label '<job>' --workdir <repo> <<'G_BRIEF_EOF'`,
the brief, `G_BRIEF_EOF`, with run_in_background (you are notified on exit; a subagent
without that waits in chunks: drop `--block` and resume `STILL RUNNING` lines with
`g-agent-call --wait <dir>`). Every role has web search; tell builders to check current
docs and maintained libraries before writing their own. Name the Bash description
`sol <role>: <job>` so the task list reads as role and model. The Haiku forwarder agents
(g-worker, g-explore, g-research) were dropped on 2026-10-05: they cost Claude tokens
and garbled reports. Sol sees only the brief, so make it self-contained: repo path,
owned files, done test. Give concurrent builders separate worktrees or disjoint files. Sol matched
Sonnet on recon, research and a small fix (2026-10-05) at 30-60s against ~10s.
The Sonnet agents (`sonnet-medium-explorer`, `sonnet-medium-researcher`, `sonnet-medium-worker`) are
Sonnet medium: use them when the operator is waiting on the answer, when nuance decides
quality (writing, synthesis, UI), or for MCP connectors. `haiku-xhigh-worker` (Haiku 5.5
xhigh, AA 41 at $.12, added 2026-10-10) is the cheap Claude tier for bounded explore, research and
gather jobs that must stay on Claude; it is a direct worker, not a forwarder, and the
value model ranks it wherever its score clears the role floor. `opus-medium-suborchestrator`
(Opus medium) runs a swarm of Sol and Luna jobs for one objective and integrates
their reports. Claude is the protected quota and Codex should run out each week (it resets
Friday 22:50, Claude Sunday 02:00). `context/temperature.py` rates each quota as
safe burn over actual burn in work hours; Codex effort rises a level at HOT/BURN
(up to xhigh, Sol max) and drops at COLD/FREEZING, every Codex role searches the
web, and at Claude COLD/FREEZING the Agent hook denies Claude-tier subagents and
g-agent-run skips Claude fallbacks unless the operator approves. AA 2026-09-30: Sol
medium 48 ($.21), high 50, max 52, against Opus low 42 ($.55) and medium 51
($1.34). `opus-low-worker` (Opus low) only takes jobs that must stay on Claude. `opus-medium-worker` (Opus medium)
only re-does work whose code quality or understanding fell short, or takes
high-fidelity visual work: on 2026-10-05 orchestrators sent nearly every build to it
and burned ~1.7M Opus output tokens against ~0.2M on Sol. Sonnet high and xhigh are
near-dominated by Opus on score per cost, so there is no Sonnet-high tier. Never use
the built-in `general-purpose` agent: it inherits Opus or takes a bare alias. `opus-high-orchestrator` (Opus high, can spawn workers) owns a large task
whose integration decisions are hard. Above Opus high, Fable 5.1 rather than Opus
xhigh. Workers hand bulk reading to Luna via `g-agent-run --role gather` and keep
the judging. The operator overrides
each quota's temperature and speed (FAST, SLOW) on gcommand's /compute page
until the end of the London day; the session context shows what is in force.

Codex roles: Luna (`gpt-6-luna`, about 1/20 of Sol's credit cost) for bulk
gathering, read-only recon and transforms; `gpt-6.1-sol` leads the explore,
research, build, verify and judge roles in `providers.json`; `gpt-6-astra` (about
5x Sol) is an occasional reviewer of big or risky diffs. Check Compute pace before choosing;
these are routing defaults, not guarantees. Escalate uncertainty rather than
retry guesses.

Keep architecture, ambiguous tradeoffs, visual judgment, nuanced writing and final
code-quality decisions with the stronger model. It may implement directly when
delegation would obscure the problem.

Start independent work with one or two cheap workers; use more when scope warrants
it. Give each a question, bounded inputs, read-only scope or exclusive files, a
done condition and an evidence contract. Return conclusions, file:line or source
links, relevant versions, verification and uncertainties, not raw logs. Send a
concise brief rather than the full conversation where supported. Read critical
original evidence before acting on a summary. Another wave needs a concrete gap.
Never duplicate a worker's investigation or run competing full test suites.

## Context and compute

Establish the visible success condition and baseline before substantial changes.
Read nearest instructions and targeted implementation, not every repo map, skill
or Brain note. Batch independent reads and cap output. Keep active context to the
constraints, decisions and evidence needed for the next step. On compaction or
handoff preserve the objective, owned files, accepted decisions, exact failure,
next discriminating check and pointers to originals. Summaries can omit details
and need checking.

Choose reasoning effort to match uncertainty; maximum effort is not the routine
default. Extra agents, inherited context and retries also consume usage. Tokens
and API cost do not determine subscription percentage. Use `compute-usage --record`
when installed to retain a quota observation without a model turn; it reads
Claude's statusline cache and local Codex's live quota. Local Codex config does
not configure hosted ChatGPT Work.

Compute pace: `burn` is quota % per hour of the operator's work; `safe` is the rate
that would leave about 0-5% at reset. Burn above safe means Claude runs out
before reset: move bulk work to Luna or lighter models. Under 3 sampled
work-hours there is no rate. Usual pace is a trailing 7-day mean (14 or 28 when
data is thin); the label names the window.

Auto-compact is a 400K backstop. Around 200-300K, at a natural task boundary
(phase done, branch pushed, topic change), tell the operator it is a good moment to
`/compact <focus>`; a model cannot trigger it. Keep going past that when many
live moving parts would not survive a summary. The prompt cache lapses an hour
after the last turn (the statusline shows `cache Nm`); a large context left idle
re-caches at full write cost, so compact or hand off before a long break. Keep
workers short-lived: one bounded job, stop when done. Long-lived agents re-read
their growing context every turn; in the week to 2026-09-29 they were ~90% of
spend and exhausted the weekly quota in three days.

Jev (TypeSafe System One, API-paid) is out of routing since 2026-10-05: Luna is
effectively free and does gathering and judging. An instance skill such as `jselect`
remains for one case, a public corpus too large or repetitive for Luna to read: it keeps only
relevant, source-linked passages; spot-check what it dropped. Skip it when the material fits one careful read, needs nuanced or
adversarial reading, is not text, or is private (it sends text to TypeSafe).

When Claude quota is tight or a second opinion helps, hand bounded jobs to Codex
through the codex plugin (`/codex:rescue --effort medium`, `/codex:review`) or
`g-codex-delegate`. Codex does well on one-repo jobs with a single acceptance
check; it did badly steering a multi-day migration. Name the URL or build the
result is judged on, the done test, and "stop and report on regression".
Give a Sol builder its own `git worktree` when another job edits the same repo.

## Skills and verification

Load only relevant skills and needed references. Beyond the mandatory ones in
shared.md: `g-gcore-comply` for compliance, `brain-condense` for requested or indicated
housekeeping, and the instance's own skills where their task comes up. Host-specific skills
exist only where their backing repo is present. For Claude review use the built-in
`/code-review`; Codex uses its own review tools. Never invent a command or agent
registration.

Run checks after the final change; repeat only for changes, failures or unresolved
evidence. For performance claims record revision, configuration, workload, timings
and failures. Visual acceptance needs the same build, data, state, viewport and
interaction path as the human sandbox; disclose differences. The parent owns
integration, diff review and the final user-path check.

## Brain and Pulse

Read injected Pulse and orientation once. Refresh with `pulse_page` when context
changes or sessions are long, not on every turn. Use `brain_init` for its
operating manual when needed; do not reload an already-read manual. Configured
MCPs are not proof of connection. Capture one durable fact per call. Do not copy
current facts into harness instructions. For supersede/edit_major, explain what
changes and why before asking. `actor` records provenance, not permission.
Structural writes stamp dependents stale; Brain's manual owns that workflow.

Official guidance: [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents),
[Anthropic agent routing](https://www.anthropic.com/engineering/building-effective-agents).
