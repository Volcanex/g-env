// ae-sidecar: connect / disconnect the iPad as a Sidecar display from the CLI.
//
// There is no public API and no `defaults` key for this. The private
// SidecarCore.framework is the only route that doesn't involve UI-scripting
// Control Center (which needs Accessibility, breaks on every macOS redesign,
// and can't be driven headlessly). Verified present on macOS 15.7 (Darwin 24.6):
//   SidecarDisplayManager  +sharedManager -devices -connectedDevices
//                          -connectToDevice:completion: -disconnectFromDevice:completion:
//   SidecarDevice          -name
// The framework is in the dyld shared cache, so it must be dlopened by bundle
// path at runtime. There's no header to link against, hence the @objc protocol
// shims below rather than a normal import.
//
// The completion block is `void(^)(NSError *)` (encoding v32@0:8@16@?24, one
// object argument), so (AnyObject?) -> Void is ABI-correct.
//
// `side` is the exception to all of that: which side of the laptop the iPad
// sits on is a *display arrangement*, not a Sidecar setting. SidecarCore brings
// the screen up; CoreGraphics decides where it lands, through public API.

import CoreGraphics
import Foundation

@objc protocol SidecarDevice {
    var name: String { get }
}

@objc protocol SidecarDisplayManager {
    func devices() -> [AnyObject]
    func connectedDevices() -> [AnyObject]
    func connectToDevice(_ device: AnyObject, completion: @escaping (AnyObject?) -> Void)
    func disconnectFromDevice(_ device: AnyObject, completion: @escaping (AnyObject?) -> Void)
}

func die(_ message: String) -> Never {
    FileHandle.standardError.write(Data(("ae-sidecar: " + message + "\n").utf8))
    exit(1)
}

guard let bundle = Bundle(path: "/System/Library/PrivateFrameworks/SidecarCore.framework"),
      bundle.load(),
      let cls = NSClassFromString("SidecarDisplayManager"),
      let shared = (cls as AnyObject).perform(NSSelectorFromString("sharedManager"))?
          .takeUnretainedValue()
else { die("SidecarCore unavailable (unsupported macOS?)") }

let manager = unsafeBitCast(shared, to: SidecarDisplayManager.self)

func name(_ device: AnyObject) -> String {
    unsafeBitCast(device, to: SidecarDevice.self).name
}

/// Devices are matched case-insensitively on a substring, so `genv ipad on pro`
/// works without quoting the full device name.
func find(_ needle: String?, in list: [AnyObject]) -> AnyObject? {
    guard let needle, !needle.isEmpty else { return list.first }
    return list.first { name($0).lowercased().contains(needle.lowercased()) }
}

/// The completion fires on an internal queue, so pump the run loop rather than
/// blocking on a semaphore: a blocked main thread deadlocks callbacks that
/// hop to main. Returns false on timeout, which is a real outcome here: an
/// iPad that's asleep or off-network never calls back at all.
func wait(_ done: () -> Bool, timeout: TimeInterval = 20) -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while !done(), Date() < deadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    return done()
}

// ── Where the screen lands ──────────────────────────────────────────────────

/// Written by `genv ipad side`, read here after every connect. Same shape as
/// `~/.genv-term-home`, and for the same reason: the preference has to outlive
/// the process that set it, and be honoured by a connect nobody typed. Absent
/// or unreadable = right, which is where a second screen belongs by default.
let sideFile = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".genv-ipad-side")

func preferredSide() -> String {
    let raw = (try? String(contentsOf: sideFile, encoding: .utf8))?
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return raw == "left" ? "left" : "right"
}

func onlineDisplays() -> [CGDirectDisplayID] {
    var count: UInt32 = 0
    guard CGGetOnlineDisplayList(0, nil, &count) == .success, count > 0 else { return [] }
    var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
    guard CGGetOnlineDisplayList(count, &ids, &count) == .success else { return [] }
    return Array(ids.prefix(Int(count)))
}

/// (laptop, pad). Online rather than Active: `CGGetActiveDisplayList` answers 0
/// from a CLI with no window server connection, the same trap `genv`'s own
/// `monitors()` documents. With more than one external screen attached, prefer
/// the one reporting vendor `'aapl'` (0x6161706C), which is what an AirPlay /
/// Sidecar display reports and a real monitor does not.
func displayPair() -> (laptop: CGDirectDisplayID, pad: CGDirectDisplayID)? {
    let all = onlineDisplays()
    guard let laptop = all.first(where: { CGDisplayIsBuiltin($0) != 0 }) else { return nil }
    let others = all.filter { $0 != laptop }
    guard !others.isEmpty else { return nil }
    return (laptop, others.first { CGDisplayVendorNumber($0) == 0x6161_706C } ?? others[0])
}

func currentSide() -> String? {
    guard let (laptop, pad) = displayPair() else { return nil }
    return CGDisplayBounds(pad).midX < CGDisplayBounds(laptop).midX ? "left" : "right"
}

/// Move the pad to `side`. Returns nil when there's no second screen to move.
///
/// The wait is what makes this usable straight after `on`: SidecarCore's
/// completion fires when the *session* is up, several seconds before the
/// display is registered with the window server, so placing immediately would
/// find nothing to place and silently do nothing.
@discardableResult
func place(_ side: String, waitForDisplay: Bool = false) -> String? {
    if waitForDisplay, displayPair() == nil {
        _ = wait({ displayPair() != nil }, timeout: 10)
    }
    guard let (laptop, pad) = displayPair() else { return nil }
    let home = CGDisplayBounds(laptop)
    let screen = CGDisplayBounds(pad)
    // Only x moves. The vertical offset is whatever the Arrangement pane was
    // last left at, and "which side" has no business resetting someone's nudge.
    let x = side == "left" ? home.minX - screen.width : home.maxX
    var config: CGDisplayConfigRef?
    guard CGBeginDisplayConfiguration(&config) == .success, let config else { return nil }
    CGConfigureDisplayOrigin(config, pad, Int32(x), Int32(screen.minY))
    // .permanently, not .forSession: the arrangement is remembered per display,
    // so a reconnected iPad comes back where it was put rather than wherever
    // macOS last guessed.
    guard CGCompleteDisplayConfiguration(config, .permanently) == .success else { return nil }
    return side
}

let args = Array(CommandLine.arguments.dropFirst())
let command = args.first ?? "list"
let target = args.count > 1 ? args[1] : nil

switch command {
case "list":
    let connected = Set(manager.connectedDevices().map(name))
    let all = manager.devices()
    if all.isEmpty { exit(0) }
    for device in all {
        let n = name(device)
        print("\(n)\t\(connected.contains(n) ? "connected" : "available")")
    }

case "status":
    // Exit code is the answer, so callers can branch without parsing.
    guard let device = manager.connectedDevices().first else { exit(1) }
    print(name(device))

case "side":
    // No argument: report. Exit code says whether there was anything to report,
    // so a caller can tell "on the left" from "no second screen".
    guard let want = target else {
        guard let side = currentSide() else { exit(1) }
        print(side)
        exit(0)
    }
    var side = want
    if side == "toggle" { side = currentSide() == "left" ? "right" : "left" }
    guard side == "left" || side == "right" else { die("side: expected left|right|toggle") }
    guard let placed = place(side) else { die("side: no second display attached") }
    print(placed)

case "on", "off", "toggle":
    var action = command
    if action == "toggle" {
        action = manager.connectedDevices().isEmpty ? "on" : "off"
    }

    let pool = action == "on" ? manager.devices() : manager.connectedDevices()
    guard let device = find(target, in: pool) else {
        die(action == "on"
            ? "no Sidecar device\(target.map { " matching '\($0)'" } ?? "") available"
            : "no Sidecar device connected")
    }

    // Already in the wanted state: succeed silently so this is idempotent and
    // safe to call from a menu or a script that doesn't check first.
    let alreadyConnected = manager.connectedDevices().contains { name($0) == name(device) }
    if (action == "on") == alreadyConnected {
        // Still assert the side: `on` means "connected, and on the side I asked
        // for", so calling it twice must not leave the screen where a manual
        // drag put it.
        if action == "on" { place(preferredSide()) }
        print(name(device))
        exit(0)
    }

    var failure: AnyObject?
    var returned = false
    let completion: (AnyObject?) -> Void = { error in
        failure = error
        returned = true
    }
    if action == "on" {
        manager.connectToDevice(device, completion: completion)
    } else {
        manager.disconnectFromDevice(device, completion: completion)
    }

    if !wait({ returned }) { die("\(action): \(name(device)) did not respond") }
    if let failure { die("\(action): \(failure)") }
    // A connect nobody typed still lands on the preferred side. This is the
    // whole point of keeping the preference in a file rather than in an
    // argument. Failure to place is not failure to connect: the screen is up.
    if action == "on" { place(preferredSide(), waitForDisplay: true) }
    print(name(device))

default:
    print("usage: ae-sidecar [list|status|on|off|toggle] [device]")
    print("       ae-sidecar side [left|right|toggle]")
    exit(1)
}
