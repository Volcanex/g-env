# aerospace-fork

Fork of [AeroSpace](https://github.com/nikitabobko/AeroSpace) (MIT), vendored as a
git subtree. One patch on top of upstream: **don't reset the Accessibility grant
when the trust check fails.** Hiding, layout and focus are stock upstream.

## Why the fork exists

AeroSpace needs Accessibility (`AXIsProcessTrusted`). Upstream calls
`resetAccessibility()` whenever the first trust check reads false, to cope with
macOS not clearing a stale grant after a signature change. This build is ad-hoc
signed and rebuilt often, and the check can read false for a moment *just after*
a fresh grant, so the reset deleted the permission that had only been given a
second earlier, and prompted again, forever. See
`Sources/AppBundle/util/accessibility.swift`.

That is the entire patch. Keep it that way: the point of a one-file diff is that
`git subtree pull` stays cheap.

## The veil, and why it was reverted (2026-08-19)

An earlier patch hid non-visible workspaces by *occlusion* rather than by moving
their windows off-screen: leave every window where it is, cover each screen with
an opaque `NSWindow`, and `AXRaise` the active workspace's windows back above it.
The motive was real: an off-screen window is not occluded, so upstream's parked
windows keep rendering at full frame rate (Chrome measured 39.4% → 1.3% CPU when
actually covered).

It does not work, because **macOS orders windows per application, not per
window.** The design needs `[active ws] > veil > [hidden ws]`, and the only
reliable cross-application reordering primitive available without private
SkyLight APIs is app *activation*, which moves all of that app's windows at once.
`kAXRaiseAction` reorders within an app and is advisory across apps. Observed:

- A window whose app was not activated stayed under the veil (laid out, holding
  its share of the tiles, invisible until focused by hand).
- Activating one Chrome window lifted Chrome's windows from *every* workspace
  above the veil at once.
- An empty workspace rendered as a full screen of veil. It was painted with a
  copy of the desktop picture, but it is still an opaque window: no icons, no
  clicks, no drag targets.
- `focus --window-id` on a window of a multi-workspace app bounced: activating
  the app let macOS re-key its own preferred window on another workspace, and
  `ae-follow` then chased the workspace change.

If the power cost is worth attacking again, do it with something that is
z-order-neutral, such as `NSRunningApplication.hide()` for apps whose windows are
*all* on non-visible workspaces. Do not reintroduce a veil.

## Building

Two non-obvious prerequisites, both of which fail with confusing errors:

1. **Swift 6.2+**: `/Library/Developer/Toolchains/swift-6.2.1-RELEASE.xctoolchain`.
   Command Line Tools ship 6.1, which cannot build this: `Sources/Common` uses the
   `unsafe` expression keyword (SE-0458), 6.2-only. Downgrading the manifest does
   not work.
2. **bash 5**: `generate.sh` rejects the bash 3.2 macOS ships. `brew install bash`,
   then put `/opt/homebrew/bin` first on PATH.

```sh
SW=/Library/Developer/Toolchains/swift-6.2.1-RELEASE.xctoolchain/usr/bin
PATH="/opt/homebrew/bin:$SW:$PATH" ./generate.sh --ignore-xcodeproj --ignore-cmd-help
PATH="/opt/homebrew/bin:$SW:$PATH" $SW/swift build
```

`build-release.sh` needs `xcodebuild` (full Xcode), which this machine does not
have. Install the debug binary into the existing bundle instead:

```sh
cp .build/debug/AeroSpaceApp /Applications/AeroSpace.app/Contents/MacOS/AeroSpace
codesign --force --sign - /Applications/AeroSpace.app
```

## Gotcha: Accessibility permission resets

The permission is keyed on the code signature, and this build is ad-hoc signed,
so **every rebuild invalidates the grant** and it must be re-approved in System
Settings → Privacy & Security → Accessibility. Expect this on each install, not
just the first. Until it is granted, AeroSpace does not tile: no workspace
switching, no `ae-switch`.

## Rebasing on upstream

```sh
git subtree pull --prefix=aerospace-fork https://github.com/nikitabobko/AeroSpace.git main --squash
```

Run from the genv-mac repo root, not from here.
