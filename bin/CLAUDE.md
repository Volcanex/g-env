# bin/: the environment's moving parts

Scripts here are symlinked to `~/.local/bin` by `bootstrap.sh`; Swift files are
**compiled** to `~/.local/bin` (the binary is the artefact, the `.swift` is the
source). `.plist` files are **copied** into `~/Library/LaunchAgents`.

"genv" is not one app. It is AeroSpace plus three helpers, and `genv up|down`
(`../cli/env.py`) is the only thing that knows the whole set.

| file | what it is | started by |
|---|---|---|
| `ae-switch` | the workspace switcher, bound to ⌘1-9, the single hottest path here | aerospace.toml bindings |
| `ae-startup` | post-restart normalizer; collects scattered windows onto ws1 | `after-startup-command` |
| `ae-cycle-ws` / `ae-chrome` / `ae-recover` | cycle, open Chrome in place, unwedge | bindings / by hand |
| `ae-follow` | makes a Dock click / ⌘-tab pull both screens across | `exec-on-workspace-change` |
| `ae-presence` | publishes actual focused workspace to g-site on h | LaunchAgent |
| `ae-paste` | clipboard image → this workspace's host, path back on the clipboard | `alt-shift-v` |
| `ae-fade.swift` | resident crossfade daemon, talks over `/tmp/ae-fade.sock` | **AeroSpace**, via `ae-startup` |
| `ae-swipe.swift` | trackpad three-finger swipe → `ae-cycle-ws` | LaunchAgent |
| `ae-sidecar.swift` | connect/disconnect the iPad as a display | `genv ipad`, genv-bar |
| `genv-bar.swift` | menu bar item | LaunchAgent |
| `ae-veil.swift` | superseded by `ae-fade` (hard black mask vs crossfade) | nothing |
| `g-projects` | register new instance repos and report manifest drift | daily `g-projects-sync.timer` / by hand |

Not everything here is Mac-side: `gsync`, `sessions` and `gauth` run on any host
and only need the passport. `ae-deploy` ends with the instance's `push-secrets`. `g-instance` prints the instance
(gp-core) directory, passport or manifest path; `gsync` and `gauth` find their
checkouts and passport through it.

`ae-presence` independently polls AeroSpace focus every second (folding Nx to N)
and atomically publishes `g-site/data/presence.json` over SSH to h on changes and
every 30 seconds, plus whether a browser is the focused app and HID idle
seconds (no app or window names leave the Mac). Each publish is also appended to
`g-site/data/presence-log.jsonl`, which g-wages bills as browser time on the
Baseline workspaces. It never blocks or changes `ae-follow`/`ae-switch`. Its separate
LaunchAgent stays installed when genv stops; without AeroSpace it cannot sample
focus and the website expires presence to Away after 90 seconds. No sleep is
prevented, no agent activity is consulted, and failed sends retry fresh samples.
Bootstrap copies this script to `~/.local/bin`: launchd's Python cannot read a
symlink into macOS's protected Desktop directory.
`ae-presence-install` also snapshots resolved `ssh -G h` settings and identity
files into `~/.ssh/ae-presence` (0700 directory, 0600 files), retaining the shared
ControlPath. SSH's Desktop symlinks face the same restriction. Re-run the installer
after changing h's SSH settings or key; no credentials go into the repository.

**`gauth` gates a hostname behind Google login** via Cloudflare Access:
`gauth setup-google` once for the whole account, then `gauth on <target> [email…]`
per product. `<target>` may name paths (`gauth on host.com/app,/api`) to leave
the rest of the host public.

Four non-obvious constraints:

- Access only sees traffic for **proxied** records, so `gauth on` flips the DNS
  record orange. A grey-clouded host is bypassed by hitting the origin, and the
  gate would look installed while doing nothing.
- Proxying alone is not protection. The origin also needs
  `nginx/snippets/cloudflare-origin.conf` (see the `state` repo), which refuses
  connections that did not come from a Cloudflare address. That snippet keys its
  check on **`$realip_remote_addr`, never `$remote_addr`**. The realip module
  runs at POST_READ and has already rewritten `$remote_addr` to the visitor's
  own address before any access check, so an `allow <cloudflare ranges>` list
  matches nothing and 403s every real visitor. That was a live outage, not a
  hypothetical.
- **Paths of one product belong to one app.** Two Access apps on the same host
  are two audiences, so a page authenticated for `/app` is not authenticated for
  `/api`, and Access answers its XHR with a cross-origin 302 to a login page
  that `fetch` cannot follow. `gauth on` puts every path in one app's
  `destinations` for this reason.
- Every app keeps **one-time PIN allowed alongside Google**: if the OAuth client
  is deleted or its secret rotated, an emailed code is the way back in rather
  than a hostname nobody can reach.
- **`gauth on <target> anyone` is not the same as `gauth off`.** `anyone` still
  makes every visitor sign in, so the origin still receives their address and can
  keep each person's data apart: a public demo with accounts, and no auth code
  of our own. `off` removes the gate and with it any notion of who is asking.
  Note a Google client left in *Testing* on the consent screen refuses everyone
  not on its test-user list, so an `anyone` policy is only genuinely open once
  that screen is **published**; until then the emailed code is the public path in.

The OAuth client secret lives at Cloudflare, never in the passport and never on
a host. Access forwards the signed-in address to the origin as
`Cf-Access-Authenticated-User-Email`, which is how a gated service tells its
users apart without owning any auth code.

## The non-obvious parts

**`g-projects sync` is a dry run.** It discovers repositories under the project
root on manifest hosts, deduplicates projects and system repos by id or GitHub
URL, and reports missing directories, changed compliance and local commits with
no remote. `sync --apply` only appends entries, then stages `manifest.json`,
commits and pushes in the instance; its normal hook can stage rendered topology.
Apply refuses a manifest that does not round-trip through the existing two-space
JSON format. `--manifest` / `G_PROJECTS_MANIFEST`, `G_PROJECTS_ROOT` and
`--git-dir` / `G_PROJECTS_GIT_DIR` isolate scans and publication in tests.

**`compute-usage` separates observations from billing, and is the usage contract.**
`compute-usage --refresh` fetches Claude (OAuth usage endpoint, read-only token use,
falling back to the statusline cache), Codex (`account/rateLimits/read` over stdio,
no model turn), OpenRouter (`/api/v1/credits` and `/api/v1/key`), Jev and Webshare, and writes
`~/.cache/genv/compute-usage-latest.json`. `compute-usage.timer` (root, every 15 min,
unit files live in /etc/systemd/system, not in this repo) runs it, so freshness no
longer depends on a Claude session rendering the statusline (that cache went 3 days
stale). Plain and `--json` read the cache; `--json` is one object per provider metric
(provider, metric, remaining, used, limit, unit, resets_at, fetched_at, stale,
age_seconds, status, note). Stale means fetched_at older than 30 min, computed at read
time; a failed provider fetch keeps its last good rows, which then age into stale.
Jev has no usage API (docs.typesafe.ai exposes only POST /v1/systemone with
per-request tokens), so it reports status `unsupported`, never a number. gcommand's
`/compute` page reads this cache. `--raw` is the old live Claude/Codex snapshot.
Codex windows/buckets vary by account. Percentages are subscription allowance, not API
spend; OpenRouter is dollars.
Webshare (`WEBSHARE_API_KEY`, `Authorization: Token`) gives per active plan a
`bandwidth:<plan_id>` row in GB (limit from `subscription/plan/`, used from
`stats/aggregate/` over the subscription's current monthly period, which the row carries
as `period_start` and `resets_at`) and a `replacements:<plan_id>` row (the plan's
`proxy_replacements_used/total`). `stats/aggregate` only permission-checks `plan_id`
(a cancelled plan returns identical totals), so bandwidth is account-wide and attached
to the ISP plan; fine while one plan is active.

**`compute-pace` turns the usage cache into pace data, and decides nothing.**
`compute-pace.timer` (root, every 15 min, unit files in /etc/systemd/system) writes
`~/.cache/genv/compute-pace.json`: per quota (claude 5h/7d, codex weekly) remaining,
time to reset, burn in % per work hour, safe burn (the rate that would leave about
5% at reset over the work hours expected before it), and the projected remaining at
reset; plus active hours (today, this Claude week, usual pace), the OpenRouter
balance and `webshare` (proxy GB burn per calendar day against safe burn, projected
use at the billing-month end, replacements used; no rate under a day of data). Usual pace is the trailing 7-day mean, widening to 14 or 28 days only when
fewer than 4 days in the window had activity; `usual_pace_days` names the window.
Projections use the 28-day weekday-hour profile scaled to that pace, so they follow
the recent level without one quiet day zeroing a weekday. The 28-day fields
(`avg_daily_28d`, `avg_by_weekday`, `trailing_days`) stay for gcommand.
Active hours merge Claude prompts with Codex turns (`task_started` in top-level codex-tui rollouts; not subagents, `codex exec` or Claude-launched runs) and use the session-gap slice of the instance's collector (`ledger/sessions.py` under
`GWAGES`, default `g-wages` beside g-env: prompts from every manifest host on one
timeline, gaps capped at 30 min), kept as intervals; a test pins it to `slice_hours`.
Without a collector the import is skipped: `hours.sources` is empty, the session block
says there are no active hours, and quotas show with no burn rate. Every quota is burned per total (Claude+Codex) work hour,
measured from the earliest recorded observation (compute-usage.jsonl, last 48h, same
window) to now, so a mode change shows quickly; Codex is now driven by Claude sessions,
so its burn is not tied to his direct Codex hours. Under 3 sampled work hours there is
no rate or projection, only "used X% so far". `context/session-context.py` renders it
as `## Compute pace` (one line per quota, `burn X vs safe Y`; fails soft to one line); gcommand `/compute`
shows it. `compute-usage --pace` prints the cache. `context/models.json` (dated roster,
rendered as `## Roster`, marked stale after 45 days) is edited by hand when models change. The only
thresholds in code describe the data (low sample, safe landing, pace window); none pick an action.

**`claude-statusline` names the tmux window as a side effect.** Claude Code
auto-titles each session from the conversation and hands that title over in the
statusline payload as `session_name`, so the bar renders it **on a second line**
(it is the one variable-width field, and sharing a row with the numbers means a
narrow terminal drops whichever end loses -- multi-line output is supported, the
client measures `line_count` off the newlines itself) and also slugs it into the
tmux window name (`[ws2-beta]0:fix-login`). No title, no second line, so
an untitled session does not spend a row on nothing. That is the whole point: the "what is this session doing"
question gets answered from the session list, not just from inside the pane. It
renames only when the slug changes (the bar re-renders constantly), only when
the window holds a single pane (two claude panes in one window would otherwise
fight over the name on every render), and it forces `automatic-rename off` --
without that, tmux reclaims the name from the foreground command within seconds
and the rename looks like it never happened. The title is Claude's guess, so it
can lag a pivot mid-session; it is a reminder, not a record.

**`ae-paste` aims by workspace state, not by the window title.** SSH gives the
remote shell no access to the Mac's clipboard, and the terminal graphics
protocols Ghostty speaks are display-only: a remote program can push pixels to
the screen but cannot be handed a pasted image. So an image has to travel as a
*file*, and the only question is which host. The title bar looks like the
obvious source and is the wrong one: it is set per-shell and says nothing after
a nested `ssh`. `~/.ae-current` + `~/.ae-workspace-<N>` are what `ae-switch`
already maintains, so the aim follows a ⌘N instantly and for free, verified
mid-build, when a switch to ws1 during testing correctly landed the next paste
locally instead of on `h`. No host for the workspace (0, 1, an unbound hot one)
is not a failure: the file stays in `~/.cache/paste-img` and the local path is
what you get.

It leaves the path **on the clipboard** rather than typing it into the terminal.
Keystroke injection would need an Accessibility grant on AeroSpace and would go
wherever focus happened to be at send time; a clipboard hand-off cannot misfire,
and the ⌘V is yours to place. The cost is that the image is *consumed*: the
clipboard holds the path afterwards, not the picture.

Two things that bite: **AppleScript coerces plain text into a file url**
happily, so `the clipboard as «class furl»` "succeeds" on any string and a
text clipboard reported itself as an unreadable file. The `clipboard info`
guard is what makes "no image" say so. And the ssh runs `BatchMode=yes`: with
`ControlMaster` already up from the workspace's own session the transfer is
instant, and on the paths where there is no master a missing key fails in ten
seconds with a notification instead of hanging forever on an invisible
passphrase prompt nobody can see. Files older than 7 days are pruned from the
landing dir on each paste, so screenshots don't accumulate on the servers.

**LaunchAgents are copied, never symlinked.** launchd's login-time scan of
`~/Library/LaunchAgents` does not follow symlinks. A symlinked agent loads only
for the session that ran `launchctl bootstrap` and is silently gone after the
next reboot, which is exactly why AeroSpace and ae-swipe stopped starting at
login while ae-dash (a plain file) kept working. `bootstrap.sh`'s `agent()` and
`cli/env.py`'s `install_agent()` both copy.

**AeroSpace is started by a LaunchAgent, not by its own start-at-login.** The
fork is ad-hoc signed, so it fails Gatekeeper's `spctl` check and never
registers in `sfltool dumpbtm`. `aerospace.toml` sets `start-at-login = false`
to say so.

**`ae-fade` must be started by AeroSpace, and the grant belongs to AeroSpace.**
Screen Recording is granted to the *responsible* process; launched by launchd or
from a shell, ae-fade is judged alone, and launched by AeroSpace it is judged as
AeroSpace. So the tick that matters is on **`/Applications/AeroSpace.app`**:
ae-fade's own entry in System Settings is never consulted at login, whatever it
says. Granting AeroSpace also means the permission rides on a stable app instead
of on our binary's code hash, which every recompile invalidates.

Denied, the capture silently returns a **windowless desktop frame**, so a switch
flashes bare wallpaper instead of blending. The transition looks broken rather
than absent, which is the confusing part. That is exactly how it failed after
the 16 Aug rebuild: ae-fade had its own ticked row in Screen Recording, the row
went stale when the binary's hash changed, and the UI kept showing it as
enabled. Every switch had been flashing wallpaper since.

`genv fade` restarts it and says which of the two states it is in. It has to
reach AeroSpace's process context to do that, and `exec-and-forget` is
config-only (the CLI rejects the subcommand), so it triggers the `alt-shift-r`
binding that exists in `aerospace.toml` for exactly this.

**`ae-fade` hard-cuts a burst.** One switch is a transition worth masking; a run
of them is a scrub, and you have to see the workspaces go by. Each cover costs
`hold + fade` (`~/.ae-fade-timing`, currently `0.08 0.10` = 180ms), so covering
every switch in a scrub caps it at under four frames a second and reads as the
machine struggling. Requests arriving within `burstWindow` (0.45s: must exceed
hold+fade, or one dissolve's tail starts the next burst) skip the snapshot
entirely and hide any overlay. Note this only became visible once Screen
Recording was granted: before that `CGDisplayCreateImage` returned an empty
desktop frame, so the fade never really covered anything. The capture itself is
~10ms warm and is not the cost.

**`ae-sidecar` uses a private framework.** There is no public API and no
`defaults` key for Sidecar. `SidecarCore.framework` lives in the dyld shared
cache, so it is dlopened by bundle path and the interface is declared as
`@objc protocol` shims: there's no header to link. Classes used:
`SidecarDisplayManager` (`+sharedManager`, `-devices`, `-connectedDevices`,
`-connectToDevice:completion:`, `-disconnectFromDevice:completion:`) and
`SidecarDevice` (`-name`), verified on macOS 15.7. The completion block is one object argument
(`v32@0:8@16@?24`), i.e. `(AnyObject?) -> Void`. If macOS ever drops it, the
fallback is UI-scripting Control Center, which needs Accessibility and breaks on
every redesign.

**`genv-bar` items are named for the verb, never the state.** A toggle labelled
with what is currently true reads backwards: "terminal on ipad" looks like a
command to put the terminal on the iPad, and it meant the terminal is already
there and clicking would send it to the Mac. Every actionable item now says what
clicking it will do (`terminal on mac`, `ipad on the left`, `tiling off`,
`connect iPad`, `stop genv`), and current state lives in the tooltip, the greyed
detail rows, and the menu bar glyph. If you add an item, follow the same rule:
the label is the destination, not the position.

**`genv-bar` is a bare binary, not a `.app`.** It sets activation policy
`.accessory` for a status item with no Dock tile. Same reasoning as everything
else here: bundling would drag in the codesigning/Gatekeeper problems that make
login items unreliable on this machine. It is deliberately thin: every action
shells out to `genv`, so `cli/env.py` stays the single implementation.

**Never `window 1` in AppleScript.** A second Ghostty window open by hand is
normal, and `window 1` is whichever was most recently front, routinely not the
group holding the workspace tabs, so tabs got created in and selected from the
wrong window. `ae-switch` records the group in `~/.ae-ghostty-group` and
addresses `first window whose id is <group>`. If the id goes stale it re-derives
the group from any tab already tracked in `~/.ae-tab-[0-9]`. The file is
deliberately *not* named `.ae-tab-*`: that glob is read elsewhere as "every
workspace tabfile".

**Probing Ghostty launches Ghostty.** An Apple event addressed to an app that
isn't running makes LaunchServices start it, hidden and windowless
(`dontSwitch hide ... preferRunning`, `LSLaunchedHidden=true`). So `ae-switch`'s
group-lookup AppleScript, whose whole job is to ask *whether* there is a Ghostty
to talk to, used to answer its own question by creating one: it got
`count windows is 0` back from the instance it had just conjured, read that as
"no Ghostty at all", and the creation path's `open -na` (`-n` = new instance,
unconditionally) then made a second one beside it. That is where the two windows
one second apart after every cold boot came from, one of them holding no tabs and
no shells. Both halves are now guarded on `pgrep -x ghostty`: never probe an
absent app (absent is already the answer), and only ask for a *new* instance when
there genuinely isn't one. This matters beyond the boot: two instances under one
bundle id make every later `tell application "Ghostty"` a coin flip, which is the
slow path picking the wrong window at random for the rest of the session.

**A workspace tab can hold a tiling slot without rendering.** All tabs in one
macOS tab group share a single frame, and only the selected one is drawn.
AeroSpace, meanwhile, tiles each tab's NSWindow into its own workspace's layout.
So every workspace tab except the front one is a real, sized, tiled window with
`kCGWindowIsOnscreen = false`: a column of layout that shows nothing. Verified
against CoreGraphics: focused on ws1, `Ghostty` 126 (ws3) and 182 (ws5) were both
tiled and both invisible, while ws3's Chrome and Obsidian were `onscreen = true`
underneath ws1's full-width terminal. This is the "windows disappear but still
take up tiling space" symptom, and it is inherent to using native tabs as the
per-workspace terminal, not a bug in `ae-switch`. Fixing it properly means
separate Ghostty windows per workspace instead of one tab group.

**An AeroSpace restart floats every window that was already open.** AeroSpace
detects all existing windows as it starts, and the workspace focused at that
moment is 0, whose `on-window-detected` rule is `layout floating`. So a restart
silently floats the whole session; `ae-startup` then sweeps those windows onto
ws1 where they stay floating forever, because nothing re-detects them. It
presents as "workspace 1 stopped tiling", and looks intermittent because windows
opened *after* the restart tile normally. `ae-startup` now re-asserts
`layout tiling` on everything it sweeps, skipping the apps the config floats on
purpose, and it reads that app list back out of `~/.aerospace.toml` rather than
keeping a second copy, so the two cannot drift.

**Hiding a workspace by covering the screen does not work.** For three weeks the
AeroSpace fork hid non-visible workspaces by leaving their windows in place and
covering each screen with an opaque window (`Veil.swift`), raising the active
workspace's windows back over it with `AXRaise`. Reverted 2026-08-19: macOS
orders windows *per application*, so a window whose app was not activated never
made it above the veil (it stayed laid out, holding its tiles, invisible) while
activating one window of a multi-workspace app lifted all of that app's windows
over the veil at once. Full account in `aerospace-fork/CLAUDE.md`. Hiding is
stock upstream again: non-visible workspaces are parked off-screen.

**A Dock click only moves the monitor the window is on.** Clicking an app in
the Dock focuses whatever window it already has, wherever that lives, and
AeroSpace follows it, on that monitor. The other screen stays put and the
workspace's Ghostty tab is never selected, so you land half-moved: the app you
asked for on one screen, the workspace you left on the other. Measured with a
probe on `exec-on-workspace-change`: a Dock click on Obsidian logged
`focused=3 prev=8x`, and the secondary screen was still showing 8x afterwards.
`ae-follow` routes that event straight back into `ae-switch`, which already
knows how to move both halves at once, so the whole environment arrives
together. Two guards keep it from chasing its own tail (a switch is several
workspace changes, each of which fires the callback again):

- `/tmp/ae-switch.lock` holds the running switch's **pid**, not just existence,
  so a switch that gets killed can't leave a file that silences Dock-following
  for the rest of the session.
- `~/.ae-current` is written at the *start* of `ae-switch`, so the late events
  that arrive after the lock is released still resolve to "we are already
  there". Writing it at the end would miss every early-exit path.

**...but following it is only right when the app isn't already here.** macOS
picks the window, and with Chrome on three workspaces a Dock click goes to
whichever one it feels like, routinely the one you last used hours ago. Chasing
that unconditionally means clicking an app you are *looking at* teleports you
away from it. `ae-follow` now asks first: if the focused window's app has another
window on the workspace we were already on (either half, `N` or `Nx`), it focuses
that one and stays (⌘` semantics) and only routes into `ae-switch` when the app
genuinely has nothing here. The redirect takes the lock for the same reason
`ae-switch` does: focusing the local window is itself a workspace change and
would otherwise re-enter this callback.

**...and travelling has to say what it is travelling for.** `ae-switch` takes a
workspace number and nothing else, and it focuses that workspace's terminal tab
unless `~/.ae-focus-<N>` names another window, so a Dock click that travelled
arrived with the app on screen and the terminal in front of it, holding the
keyboard. Measured: a Dock activation of WhatsApp (ws3, none on ws2) ended
`focused=78 Ghostty`, not the app that was clicked. `ae-follow` now writes the
focused window id into the destination's focus memory before handing over, so
the switch restores onto it: same activation now ends `focused=112 WhatsApp`.
Nothing new in `ae-switch`: this is the restore path it already had, told what
to restore.

**A launch is an activation, and an activation is a workspace change.** Asking
Chrome for a new window activates Chrome, and macOS raises one of its *existing*
windows first, on whatever workspace that one lives. `ae-chrome` took no lock,
so `ae-follow` saw an ordinary outside activation of an app with nothing here
and travelled; the window then opened on the workspace we had just run away to.
Worse, `ae-chrome` identified its new window as "whichever Chrome window is
focused", which during that scramble is the *old* window, so it dragged that
one off its workspace and left the new one where it was born. It now takes
`/tmp/ae-switch.lock` for the whole launch and finds its window by diffing
Chrome's window ids before and after, moving it by `--window-id`.

**Browsers don't travel.** Every other app follows the rule above: the Dock
click goes to the window macOS picked. A browser is the exception: asking for
Chrome means "a window where I am", not a trip to the tab you left open on ws3
this morning, and macOS reliably picks that stale window. So case (2) in
`ae-follow` returns to the workspace you were on and `exec`s `ae-chrome` there,
which opens a window in that workspace's mapped profile. `exec` keeps the pid,
so the lock `ae-follow` took is the one `ae-chrome` goes on holding.

**Two `aerospace` calls where one `eval` would do is a visible flicker.** Each
CLI call is its own binary spawn and round trip, and the window server draws
what happened in between. On a two-monitor switch the ending was
`show_other` → `focus` → `settle`(`show_other` → `focus`): four workspace
changes, alternating N and Nx twice, of which the last two were an exact repeat
of the first two. That is the N↔Nx flick. The raise and the focus now go as one
`aerospace eval "workspace <other>; focus --window-id <id>"` so only the result
is drawn, and the closing `settle` runs only on the two paths that move things
after the transition (focus restored elsewhere, or a tab was created) rather
than on every ordinary switch.

**One cover per switch, held: a switch is not one length.** The
ordinary switch is over in ~80ms and a fixed `hold + fade` covered it. A *first*
visit to a workspace has to create a Ghostty tab: AppleScript, an `open`, two
poll loops, a tab reorder, a `sleep 0.35` and a re-assert, measured at 1.44s.
The fixed cover dissolved 180ms in, onto the middle of that work, and the
re-assert at the end then needed a cover of its own, so a first visit read as
**flash, jump, flash**, and after a Ghostty restart *every* workspace is a
first visit, which is why it presented as "startup is glitchy".

So the socket protocol grew two words: `hold` covers and stays up, `release`
dissolves. `ae-switch` sends `hold` at the top and releases from its **EXIT
trap**, so the cover lasts precisely as long as the switch does, whatever path
it took, and there is exactly one dissolve, at the moment the workspace is
actually finished. The trap is what makes this safe to do at all: every exit
path, including a kill, puts the screen back. ae-fade also caps a hold at 3s on
its own side, for the paths that never reach a trap. `hold` in
`~/.ae-fade-timing` now only governs the old bodiless request; a held cover sits
opaque for the length of its switch.

Also: the burst path used to remove the cover with `alphaValue = 0; orderOut`,
an instant cut. The *first* switch of a burst can't know it is one, so it has
already gone opaque, and that yank was a flash at the start of every scrub; it
now dissolves over 60ms, far inside `burstWindow`.

**`gsync`'s remotes must be SSH, not HTTPS.** Over HTTPS these private repos
authenticate off a token the interactive shell happens to export, so `gsync`
worked when you ran it and failed everywhere it actually matters (from
`ae-startup`, from a timer, from anything launchd started) with
`Invalid username or token`. Reproduce with
`env -i HOME=$HOME PATH=... gsync`. SSH keys are read straight from `~/.ssh`
and need no environment at all, so `git@github.com:Volcanex/<repo>.git` works
in every context. A fresh clone made over HTTPS reintroduces the bug silently:
it will look fine until the first non-interactive run.

**`gsync` at login must wait for the network.** `ae-startup` fires ~10s into a
boot, reliably before DNS is up: every checkout failed with `Could not resolve
host: github.com`, so the one sync that matters most, the one right after a
restart, was the one that never ran, and the session then used whatever topology
the Mac happened to have. It now polls `nc -z github.com 443` for up to a minute
first, inside the background subshell so the window layout still waits on nothing.

**Native fullscreen is the enemy.** macOS's own fullscreen moves a window into a
separate Space, which AeroSpace cannot manage: the window drops out of its
workspace, the veil spans Spaces and stays put, and the layout is left with a
hole. `alt-shift-f` (aerospace's `fullscreen`) fills the workspace in place
instead, and Ghostty is set to `macos-non-native-fullscreen`. Avoid the green
button.

**Terminal home.** `ae-switch` reads `~/.genv-term-home` (`main` | `secondary`,
absent = secondary) on every switch to decide whether this workspace's Ghostty
tab is homed on `N` or on its twin `Nx`. Set it with `genv term`. Because it's
read live there is no reload: it lands on the next ⌘N. Everything downstream in
`ae-switch` derives from `term_ws` rather than re-testing the monitor count, so
the re-homing loop and the end-of-switch `settle` follow it automatically.

**iPad side is CoreGraphics, not Sidecar.** Which side of the laptop the iPad
screen sits on is display *arrangement*: `CGConfigureDisplayOrigin`, public
API, `.permanently` so a reconnect comes back where it was put. SidecarCore only
brings the screen up. `ae-sidecar side [left|right|toggle]` moves it; the wanted
side lives in `~/.genv-ipad-side` (absent = right), same file-as-preference
pattern as `~/.genv-term-home`, and `ae-sidecar on` re-reads it and re-places the
screen so a connect nobody typed still lands on the right side. Only `x` moves:
the vertical offset is whatever the Arrangement pane was left at. The pad is
identified as the non-builtin display, preferring vendor `'aapl'` (0x6161706C),
which is what an AirPlay/Sidecar screen reports and a real monitor does not.
The wait inside `place()` is load-bearing: SidecarCore's completion fires when
the *session* is up, seconds before the display registers with the window
server, so placing immediately would find nothing to place.

**A new window is born wherever the focus is, which is the terminal.** Every
switch deliberately ends with the keyboard on the Ghostty tab, and with a second
screen attached that tab lives on the twin, i.e. on the iPad. AeroSpace puts a
newly detected window on the focused workspace, so *everything you opened*
appeared on the iPad, stacked next to the terminal, and the ones that knew
better (`ae-chrome`) then polled for up to 3s and dragged themselves back to the
Mac: the window that appears on the wrong screen and jumps. `aerospace.toml`
now ends with

```toml
[[on-window-detected]]
if = 'test %{monitor-is-main} = false'
run = 'move-node-to-monitor --focus-follows-window main'
```

which is keyed on the *monitor*, not on the `Nx` names, so it holds whichever
half the terminal is homed on. Two things depend on the rules above it: Ghostty
matches first and stops (its tab is *meant* to be born on the twin: that is how
`ae-switch` homes the terminal there), and every float rule that used to be
terminal now carries `check-further-callbacks = true` so its windows still reach
this one. Because a window can no longer be born on the second screen,
`cmd-shift-\` (`move-node-to-monitor next`) is how anything gets there
deliberately.

The same reasoning is why ⌘[ / ⌘] carry
`--boundaries all-monitors-outer-frame`: focus ends on the terminal, so without
crossing the screen edge the terminal is a dead end and reaching the workspace's
other windows meant clicking one. Focus is not simply parked on the Mac window
at the end of a switch instead, because then ⌘4 followed by typing would go into
Chrome.

**Plugging a display in invalidates a cache nothing invalidates.** `ae-switch`
caches AeroSpace's monitor count in `~/.cache/genv/monitors` (asking cost 20ms
of every switch) and refreshes it in a background subshell *after* the switch
has finished. So the first ⌘N after the iPad connects still runs the
single-monitor path (terminal on the Mac, twins empty), and the switch after
that yanks all nine terminal tabs across at once. `genv ipad on|off|toggle` now
calls `resync_workspaces()` (`cli/env.py`): rewrite the count, then replay the
current workspace through `ae-switch`, so the environment arrives in one step
and the second one isn't a surprise. It waits for `aerospace list-monitors` to
agree with CoreGraphics first: AeroSpace notices a new display a beat after the
window server does.

**Hot workspaces are bound for a login, and `ae-startup` is what ends it.**
6, 7 and 8 carry no host in the manifest; the first shell to open on one runs
`genv ws <N>`, picks a project, and writes `~/.ae-workspace-<N>` +
`~/.ae-ws-path-<N>` + `~/.ae-ws-label-<N>` (see `cli/CLAUDE.md`). `ae-startup`
deletes those three for every workspace the manifest marks `hot`, reading the
numbers from the manifest rather than globbing the files. That also clears the
dangerous leftover, a workspace that has just *become* hot while a stale
`~/.ae-workspace-N` from its pinned days is still on disk. That file alone would
send the next shell to the old host with no project and no picker, because the
picker only runs when the file is *absent*.

`zshrc` re-uses tmux letters (`ws6-alpha`, `ws6-beta`, …) across projects, so it
now checks `#{session_path}` before attaching: a session sitting in a different
project is skipped rather than resumed, since `new-session -As` would drop you
in the old directory and the swap would look like it had done nothing. And the
fields of the title/tint/path lookup are read **label last**: `read` puts the
remainder of the line in its final variable, and the workspace name is the one
field that can contain a space ("Work 2").

**Unattended jobs call `g-agent-run`, never a vendor CLI.** `g-agent-run --role
gather|bulk|explore|research|build|verify|judge|review --prompt-file F --workdir D [--web] [--tools] [--connectors] [--out F]` runs a role
through `context/value.py` using `--stakes routine|normal|important|critical` (default normal).
It tries the best point, then the best point of the other provider, up to three attempts,
each with its own effort. The old roles and efforts in `context/providers.json` are
the fallback if the value config is absent or fails. It falls back on a missing CLI, non-zero
exit, auth/quota/usage-limit message, timeout, empty answer, non-answer, sandbox
failure, or a quota below the floor in `compute-pace.json`. Short fragments,
blocked sandbox or permission reports, and refusals without a deliverable fail.
Short verdicts pass. These failures have reason `non-answer` in the log.
Failed uid map, newuidmap or setgroups setup has reason `sandbox` with the cause.
Each attempt is logged to stderr and
`~/.cache/genv/g-agent-run.jsonl` with the point id, provider and effort; `--meta F` reports the provider that answered.
A subscription lapsing or an account being banned therefore degrades a timer, not
breaks it, and a new vendor is one adapter plus one line of config. `--tools` gives a job
Bash in `--workdir` (plus file edits with `--sandbox workspace-write`; Codex also gets
network then), `--connectors` keeps Claude's account MCP connectors, and `--claude-only`
restricts MCP/private data jobs to Claude and bypasses the cold gate. `--role design` (or `--domain ui`,
which ranks any role as design) weighs model taste, so Claude wins it on price and is never gated. Critical stakes
also bypass that gate; other jobs need `--allow-claude`. The role `review`
(read-only plus Bash) is the cron reviewer. Never put
`codex exec` or `claude -p` in a timer or cron script.

`g-pick --role build --stakes important` prints the current choice and quota prices;
`--json` returns the explanation and runners-up, and `g-pick --grid` prints every role and stakes.

**`g-temp`** overrides a provider's temperature from a session: `g-temp claude hot 3h`,
`g-temp codex burn` (to the end of the London day, as on the compute page), `g-temp claude off`;
bare `g-temp` prints what the system acts on. It keeps a speed set on the page. Overrides
live on h; on b (it has `~/.config/genv/compute-token`) it relays to h over SSH. The
statusline marks an override with `*`.

**`g-agent-call` is how a Claude session runs a Codex model**, as a background Bash
call (`--block` waits to the end, so run_in_background notifies on exit). It prefixes
the role preamble, starts `g-agent-run` detached under `~/.cache/genv/agent-jobs/<id>/`,
and without `--block` waits up to 570s, because a foreground Bash call is capped at ten
minutes; past that it prints `STILL RUNNING: g-agent-call --wait <dir>`. `--label` lands in
the run log for the compute page; `--stakes`, `--claude-only` and `--domain` pass through to the runner.
The Haiku forwarder agents that used to wrap it were
dropped on 2026-10-05 (Claude tokens, garbled reports); the Agent hook redirects them.

**`g-scout` researches alongside substantial Claude subagents.** The Luna hook
reads the brief from the subagent transcript and detaches `g-scout --dir D
--brief-file F --transcript T`. Up to 40 gather rounds over 50 minutes keep checked,
scored links in D/findings.jsonl; new local struggle signals focus the next round.
Round starts are at least 60 seconds apart. The hook injects one pointer after a
failing tool call, at most six per subagent and at least two minutes apart. Only
score 5 may arrive in the first minute; later score 4-5, or 3 while struggling.
Pointers are one line and at most 200 characters. Stop touches D/stop; old scout
directories are pruned after two days. `~/.cache/genv/boost.jsonl` counts injections.
`G_SCOUT=0` disables the boost for one session and `~/.cache/genv/scout/OFF` for every session; `G_SCOUT_DEBUG=1` logs every hook decision to
D/hook.log. `G_SCOUT_RUNNER` and `G_SCOUT_INTERVAL` are test overrides.

**`refresh-models` keeps `context/models.json` current** (`refresh-models.timer`,
Mon 06:00 London; unit files in /etc/systemd/system). Gather and verify run through
`g-agent-run`; guards validate the schema (a URL on every number, `as_of` today),
commit and push on a change, and hold a candidate in
`~/.cache/genv/models.candidate.json` with an ntfy page when a score moves 5+ points
or more than 3 models change. `--dry-run` prints the diff and changes nothing. The
ladder is the non-dominated set over score, cost per task and AA time to first
token; quota weighting per model is unpublished, so $ is only an API-price proxy.

**`g-cron`** lists every scheduled job on h, b and b2 (hosts from gcore/manifest.json,
remote ones read by piping the script to `ssh -o BatchMode=yes … python3 - --collect`):
systemd timers (system and --user), crontabs and `/etc/cron.d`, and the Pulse model
jobs in `brain/.pulse/crons/*.md` with their run history from `state/cron-runs.jsonl`
(`cron-runs.json` + `cron.log` when absent). Discovery only, there is no registry to
drift. `g-cron status` prints problems only and exits 1; `--record` writes the full
JSON to `~/.cache/genv/g-cron.json` (0600), which `g-cron.timer` (root, every 15 min,
unit files in /etc/systemd/system like compute-usage) refreshes. OS-noise timers are
hidden without `--all`; the model column for non-pulse jobs is a best-effort grep.
`brain/.pulse/crons/pulse.crons.review.md` is the daily model glance at its output.

**`compute-spend`** writes `~/.cache/genv/compute-spend.json` for `/compute`: Claude
transcripts and Codex jobs/interactive sessions, by model and worker, today and
each provider's quota week, plus eight London daily buckets and recent jobs.
Units estimate relative quota cost in Opus output tokens, not billing. `--json`
prints instead; `--local` skips SSH; `--pace FILE|-` supplies week starts.
`G_SPEND_HOST` defaults to h; `G_SPEND_REMOTES` defaults to compute-spend on every other host with a workspace in the instance manifest.
`compute-spend.timer` (root on h, every 15 min) runs it; b answers over SSH.

**`compute-sync`** (b, its user's crontab, every 15 min) pulls h's compute-pace.json
and compute-mode.json from `/api/compute/sync` with the read-only token in
`~/.config/genv/compute-token`, replacing the old scp push from h. Overrides set on
the compute page therefore reach b's sessions within 15 minutes.
