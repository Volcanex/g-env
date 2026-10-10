// ae-fade: crossfade mask for workspace switches. Snapshots every display,
// holds the snapshot on top while the workspace changes underneath, then
// dissolves it away, so a switch reads as a soft blend instead of a hard cut.
//
// Runs as a resident LaunchAgent listening on a unix socket, NOT as a one-shot
// command: spawning a fresh process cost 70-130ms of Swift launch + AppKit init
// before the snapshot was even up, which is dead time in front of every single
// workspace switch. Warm, the same work is a few ms.
//
// Protocol: connect to /tmp/ae-fade.sock, optionally write one word, read one
// line. The line arrives once the snapshot is actually on screen. ae-switch
// blocks on it before touching the workspace, because snapshotting after the
// switch would capture the destination and dissolve to an identical image.
//
//   printf ''        cover, then dissolve on our own schedule (hold + fade)
//   printf 'hold'    cover, and stay up until told otherwise
//   printf 'release' dissolve now
//
//   ae-switch:  printf 'hold' | nc -U /tmp/ae-fade.sock   (waits for "ready")
//
// `hold` exists because a switch is not one length. An ordinary switch is over
// in ~80ms and a fixed hold covers it; the first visit to a workspace has to
// create a Ghostty tab, which takes AppleScript and polling and lands about
// 1.4s later. Covering that with a fixed hold produced *two* transitions on
// screen: one cover at the start that dissolved onto the middle of the work,
// and a second one over the re-assert at the end. That is what a first visit
// looked like: flash, jump, flash. ae-switch now holds one cover across the
// whole switch and releases it from its EXIT trap, so however long the switch
// takes there is exactly one dissolve, and it happens when the workspace is
// actually finished.
//
// A real Spaces-style slide isn't possible from outside the WindowServer: the
// destination workspace has no rendered image until after the switch lands, so
// any slide would animate stale frames of a transition already finished. A
// dissolve only needs the outgoing frame, which we can grab up front.
//
// Needs Screen Recording permission (granted to whatever process ends up
// responsible, i.e. this agent). Without it the capture yields desktop-only
// frames; switching still works, it just stops looking like a blend.
//
// Compiled by bootstrap.sh into ~/.local/bin/ae-fade, run by
// bin/com.gabriel.ae-fade.plist.
import AppKit

let sockPath = "/tmp/ae-fade.sock"
// Timings live in a file, re-read per switch, NOT baked in as constants:
// Screen Recording is granted against this binary's code hash, so every
// recompile silently revokes it. Tuning the feel must not cost a rebuild.
//   ~/.ae-fade-timing  =  "<hold> <fade>"  in seconds
// hold = how long the snapshot sits opaque while the switch lands underneath,
// only used by the bodiless request, since a held cover sits opaque for exactly
// as long as its switch takes and then dissolves on `release`;
// fade = the dissolve. Longer covers more of the window-reflow flicker (Chrome
// can take 300ms+) at the cost of every switch feeling slower.
let timingPath = NSHomeDirectory() + "/.ae-fade-timing"
let defaultHold = 0.03, defaultFade = 0.11

func timings() -> (hold: Double, fade: Double) {
    guard let raw = try? String(contentsOfFile: timingPath, encoding: .utf8) else {
        return (defaultHold, defaultFade)
    }
    let parts = raw.split(whereSeparator: { $0 == " " || $0 == "\n" }).compactMap { Double($0) }
    guard parts.count >= 2 else { return (defaultHold, defaultFade) }
    // Clamped: a fat-fingered edit shouldn't be able to paint the screen over
    // for ten seconds with a window we don't accept clicks on.
    return (min(max(parts[0], 0), 0.5), min(max(parts[1], 0.01), 1.0))
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)

// CGDisplayCreateImage was hard-obsoleted in the macOS 15 SDK (not merely
// deprecated), so it can't be called directly, but the symbol is still live in
// CoreGraphics, so resolve it at runtime. Measured at 3-4ms warm; ScreenCapture-
// Kit's async stream setup is an order of magnitude slower per capture. If Apple
// ever removes the symbol, dlsym returns nil and we degrade to a hard cut.
typealias DisplayCaptureFn = @convention(c) (UInt32) -> Unmanaged<CGImage>?
let cgHandle = dlopen("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics", RTLD_LAZY)
let captureDisplay: DisplayCaptureFn? = dlsym(cgHandle, "CGDisplayCreateImage").map {
    unsafeBitCast($0, to: DisplayCaptureFn.self)
}

// Without Screen Recording the capture silently returns a desktop-only frame,
// so the "crossfade" becomes a flash of bare wallpaper, which looks worse than
// no fade at all. Ask up front (registers us in System Settings) and say so in
// the log, because the failure is otherwise indistinguishable from a bug.
if !CGPreflightScreenCaptureAccess() {
    FileHandle.standardError.write(
        "ae-fade: NO screen recording permission, so capture will be desktop-only. Requesting…\n"
            .data(using: .utf8)!)
    CGRequestScreenCaptureAccess()
} else {
    FileHandle.standardError.write("ae-fade: screen recording OK\n".data(using: .utf8)!)
}

// Overlay windows are created once per display and reused: allocating an
// NSWindow per switch reintroduced exactly the latency the daemon exists to
// avoid. Keyed by display id so monitor changes don't strand stale windows.
var overlays: [UInt32: NSWindow] = [:]
var fadeWork: DispatchWorkItem?
// A held cover is released by its client, and a client can die. Nothing else on
// screen can be reached while an opaque still covers it, so the hold is capped:
// worth more than the tail of a slow switch is a screen that always comes back.
var holdGuard: DispatchWorkItem?
let maxHold = 3.0

func overlay(for screen: NSScreen, id: UInt32) -> NSWindow {
    if let w = overlays[id] {
        w.setFrame(screen.frame, display: false)
        return w
    }
    let w = NSWindow(contentRect: screen.frame, styleMask: .borderless,
                     backing: .buffered, defer: false)
    w.backgroundColor = .clear
    w.isOpaque = false
    w.level = .screenSaver
    w.ignoresMouseEvents = true
    w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
    // Keep the overlay out of the next switch's snapshot, so rapid swipes
    // can't stack a stale frame on top of a stale frame.
    w.sharingType = .none
    let view = NSView(frame: screen.frame)
    view.wantsLayer = true
    view.layer?.contentsGravity = .resize
    w.contentView = view
    overlays[id] = w
    return w
}

// Consecutive switches closer together than this are a scrub, not a transition.
// Must exceed hold+fade (0.26s at the shipped timings), or the tail of one
// dissolve counts as the start of the next burst.
let burstWindow = 0.45
// How long the cover takes to get out of the way when a burst is detected.
// Short enough that a scrub still runs at the rate the swipes arrive (the
// whole reason a burst goes uncovered) but not instant, see raiseSnapshot.
let burstUncoverFade = 0.06
var lastRequest: CFAbsoluteTime = 0
var skipped = false

// Runs on the main thread; returns once the snapshot is on screen.
// `holding`: don't schedule the dissolve, wait for a `release` (or the cap).
func raiseSnapshot(holding: Bool) {
    guard let captureDisplay else { return }

    // Hard-cut a burst. One switch is a transition worth masking; a run of them
    // is the user scrubbing through workspaces to find one, and they need to SEE
    // the workspaces go by. Covering each with an opaque still for hold+fade
    // turns a fast scrub into a slideshow at 1/0.26s (under four frames a
    // second), which reads as the machine struggling rather than as a fade.
    // Uncovered, a scrub runs at whatever rate the swipes arrive.
    let now = CFAbsoluteTimeGetCurrent()
    let sinceLast = now - lastRequest
    lastRequest = now
    if sinceLast < burstWindow {
        skipped = true
        fadeWork?.cancel()
        fadeWork = nil
        // Whatever the superseded switch was holding, it isn't holding it now.
        holdGuard?.cancel()
        holdGuard = nil
        // Removing the cover instantly is itself a visible seam. The FIRST
        // switch of a burst has no way of knowing it is one (nothing has
        // arrived yet to make it a burst), so it has already snapshotted and
        // gone opaque, and this is where that still gets yanked, mid-hold, on
        // the very next swipe. That yank is the flash at the start of every
        // scrub: cover, then a hard cut to live screen. Dissolve it out
        // instead. At 60ms it costs a scrub nothing (the switches themselves
        // arrive no faster than the fingers move) and no request can collide
        // with it: a non-burst request is by definition at least burstWindow
        // away, seven times longer than this animation takes to finish.
        let covering = overlays.values.filter { $0.isVisible }
        if !covering.isEmpty {
            NSAnimationContext.runAnimationGroup({ ctx in
                ctx.duration = burstUncoverFade
                covering.forEach { $0.animator().alphaValue = 0 }
            }, completionHandler: {
                covering.forEach { if $0.alphaValue == 0 { $0.orderOut(nil) } }
            })
        }
        return
    }
    skipped = false

    // A switch arriving mid-dissolve supersedes it. Cancel before re-snapping,
    // or the old fade would drive the new overlay straight to transparent.
    fadeWork?.cancel()
    fadeWork = nil

    for screen in NSScreen.screens {
        guard let num = screen.deviceDescription[
                NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else { continue }
        let id = num.uint32Value
        guard let shot = captureDisplay(id)?.takeRetainedValue() else { continue }
        let w = overlay(for: screen, id: id)
        w.contentView?.layer?.contents = shot
        w.alphaValue = 1
        w.orderFrontRegardless()
    }
    // Push the snapshot to the compositor before we report ready, so ae-switch
    // can't change workspace while we're still a frame behind.
    CATransaction.flush()

    holdGuard?.cancel()
    holdGuard = nil
    if holding {
        let guardWork = DispatchWorkItem { dissolve(after: 0) }
        holdGuard = guardWork
        DispatchQueue.main.asyncAfter(deadline: .now() + maxHold, execute: guardWork)
    }
}

// Drop a held cover. Harmless when nothing is covering, which is the common
// case for the release that follows a switch the burst rule chose not to mask.
func release() {
    holdGuard?.cancel()
    holdGuard = nil
    dissolve(after: 0)
}

func dissolve(after hold: Double) {
    // Nothing was raised: there is nothing to dissolve, and scheduling one
    // would order the overlays back in mid-scrub.
    if skipped { return }
    let fade = timings().fade
    let work = DispatchWorkItem {
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = fade
            ctx.timingFunction = CAMediaTimingFunction(name: .easeOut)
            overlays.values.forEach { $0.animator().alphaValue = 0 }
        }, completionHandler: {
            // Order out only if still transparent. A switch landing during the
            // animation will have re-raised these to opaque.
            overlays.values.forEach { if $0.alphaValue == 0 { $0.orderOut(nil) } }
        })
    }
    fadeWork = work
    DispatchQueue.main.asyncAfter(deadline: .now() + hold, execute: work)
}

// MARK: - Socket
unlink(sockPath)
let listener = socket(AF_UNIX, SOCK_STREAM, 0)
guard listener >= 0 else {
    FileHandle.standardError.write("ae-fade: socket() failed\n".data(using: .utf8)!)
    exit(1)
}
var addr = sockaddr_un()
addr.sun_family = sa_family_t(AF_UNIX)
_ = withUnsafeMutablePointer(to: &addr.sun_path) {
    $0.withMemoryRebound(to: CChar.self, capacity: MemoryLayout.size(ofValue: addr.sun_path)) {
        strcpy($0, sockPath)
    }
}
let addrLen = socklen_t(MemoryLayout<sockaddr_un>.size)
guard withUnsafePointer(to: &addr, {
    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(listener, $0, addrLen) }
}) == 0, listen(listener, 8) == 0 else {
    FileHandle.standardError.write("ae-fade: bind/listen failed on \(sockPath)\n".data(using: .utf8)!)
    exit(1)
}
chmod(sockPath, 0o600)

/// The request word, or "" for the original bodiless protocol.
///
/// Read with a timeout rather than blocking: the reply is on a switch's
/// critical path, so a client that connects and then says nothing must not be
/// able to wedge the daemon, and with it every later switch.
func readRequest(_ client: Int32) -> String {
    var tv = timeval(tv_sec: 0, tv_usec: 150_000)
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    var buf = [UInt8](repeating: 0, count: 32)
    let n = read(client, &buf, buf.count)
    guard n > 0 else { return "" }
    return String(decoding: buf[0 ..< n], as: UTF8.self)
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

DispatchQueue.global(qos: .userInteractive).async {
    while true {
        let client = accept(listener, nil, nil)
        if client < 0 { continue }
        let request = readRequest(client)
        if request == "release" {
            DispatchQueue.main.sync { release() }
        } else {
            // Snapshot synchronously on the main thread, so "ready" is only
            // written after the overlay is genuinely up. That ordering is the
            // whole point.
            let holding = request == "hold"
            DispatchQueue.main.sync { raiseSnapshot(holding: holding) }
            if !holding {
                DispatchQueue.main.async { dissolve(after: timings().hold) }
            }
        }
        _ = "ready\n".withCString { write(client, $0, strlen($0)) }
        close(client)
    }
}

FileHandle.standardError.write("ae-fade: listening on \(sockPath)\n".data(using: .utf8)!)
app.run()
