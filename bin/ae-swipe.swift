// ae-swipe: three-finger trackpad swipe → ae-cycle-ws prev/next, i.e. the
// same thing cmd+← / cmd+→ do (see aerospace/aerospace.toml).
//
// AeroSpace has no gesture support and macOS exposes no public API for raw
// trackpad contacts. BetterTouchTool and friends all read the same private
// framework this does: MultitouchSupport, dlopen'd at runtime because there
// are no headers to link against. Undocumented but stable since ~2008; if a
// macOS update ever moves it, MTDeviceCreateList() returns nil and we exit
// loudly rather than silently doing nothing.
//
// Runs as a LaunchAgent (bin/com.gabriel.ae-swipe.plist), compiled by
// bootstrap.sh into ~/.local/bin/ae-swipe (binary, not symlink).
import Foundation

// MARK: - MultitouchSupport ABI
// Layout must match the C structs exactly. These are memory-mapped by the
// framework, not marshalled. Do not reorder or "tidy" the fields.
struct MTPoint { var x: Float; var y: Float }
struct MTReadout { var pos: MTPoint; var vel: MTPoint }

struct MTTouch {
    var frame: Int32
    var timestamp: Double
    var identifier: Int32
    var state: Int32          // 4 = make-touch, 5 = touching; see onPad()
    var fingerID: Int32
    var handID: Int32
    var normalized: MTReadout // position in 0...1 across the pad
    var zTotal: Float
    var field9: Int32
    var angle: Float
    var majorAxis: Float
    var minorAxis: Float
    var absolute: MTReadout
    var field14: Int32
    var field15: Int32
    var zDensity: Float
}

typealias MTDeviceRef = UnsafeMutableRawPointer
// The touch array arrives as raw memory: @convention(c) only accepts C-
// representable types, and a pointer to a native Swift struct isn't one.
// Decoded by hand in the callback against the 96-byte stride asserted below.
typealias MTContactCallback = @convention(c) (
    MTDeviceRef?, UnsafeMutableRawPointer?, Int32, Double, Int32
) -> Int32

let fw = "/System/Library/PrivateFrameworks/MultitouchSupport.framework/MultitouchSupport"
guard let handle = dlopen(fw, RTLD_LAZY) else {
    FileHandle.standardError.write("ae-swipe: cannot dlopen MultitouchSupport\n".data(using: .utf8)!)
    exit(1)
}
func sym(_ name: String) -> UnsafeMutableRawPointer? { dlsym(handle, name) }
guard let pCreateList = sym("MTDeviceCreateList"),
      let pRegister = sym("MTRegisterContactFrameCallback"),
      let pStart = sym("MTDeviceStart") else {
    FileHandle.standardError.write("ae-swipe: MultitouchSupport symbols missing\n".data(using: .utf8)!)
    exit(1)
}
typealias CreateListFn = @convention(c) () -> Unmanaged<CFMutableArray>?
typealias RegisterFn = @convention(c) (MTDeviceRef, MTContactCallback) -> Void
typealias StartFn = @convention(c) (MTDeviceRef, Int32) -> Void
let MTDeviceCreateList = unsafeBitCast(pCreateList, to: CreateListFn.self)
let MTRegisterContactFrameCallback = unsafeBitCast(pRegister, to: RegisterFn.self)
let MTDeviceStart = unsafeBitCast(pStart, to: StartFn.self)

// The framework writes MTTouch structs into a flat array; if Swift ever lays
// this out differently from the C original, every reading below is garbage.
// Fail loudly instead of firing swipes off misparsed memory.
precondition(MemoryLayout<MTTouch>.stride == 96, "MTTouch layout drifted")

// MARK: - Tuning
// Fraction of pad width the hand must travel to count as a swipe. Low enough
// to feel light, high enough that a three-finger rest-and-shift doesn't fire.
let threshold: Float = 0.11
// A deliberate flick is unambiguous long before it has travelled the full
// distance, so let speed buy the rest: waiting out the full 11% was costing
// 30-60ms of pure lag on exactly the gestures that were most obviously swipes.
// A slow drag still has to go the whole way, which is what keeps a three-finger
// reposition from firing.
let fastThreshold: Float = 0.055
let fastSpeed: Float = 1.1   // pad-widths per second, mean of the three fingers
// Reject diagonal drift: horizontal travel must dominate vertical.
let horizontalBias: Float = 1.5
// One fire per gesture: the fingers must lift (or drop off three) before
// another switch. Re-anchoring mid-swipe instead was measured at ~2 fires per
// natural swipe, which overshoots badly given ae-cycle-ws skips empty
// workspaces. Parity with cmd+←/→ means one gesture, one step.
// Fingers move with the workspaces: swipe right to reach the workspace on the
// right (next), swipe left for the one on the left (prev). The pad reads as a
// map of the workspace strip, not as natural-scrolling content drag.
// Set AE_SWIPE_INVERT=1 for the content-drag sense instead.
let invert = ProcessInfo.processInfo.environment["AE_SWIPE_INVERT"] == "1"
let cycler = NSHomeDirectory() + "/.local/bin/ae-cycle-ws"

// MARK: - Gesture state
// A C function pointer can't capture context, so this lives at file scope.
var tracking = false
var fired = false
var anchorX: Float = 0
var anchorY: Float = 0

func fire(_ direction: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: cycler)
    p.arguments = [direction]
    try? p.run()
}

let callback: MTContactCallback = { _, touches, count, timestamp, _ in
    guard let touches = touches else { return 0 }

    var sumX: Float = 0, sumY: Float = 0, sumVX: Float = 0, fingers: Float = 0
    for i in 0..<Int(count) {
        let t = touches.load(fromByteOffset: i * MemoryLayout<MTTouch>.stride, as: MTTouch.self)
        guard t.state == 4 || t.state == 5 else { continue } // on the pad, not hovering
        sumX += t.normalized.pos.x
        sumY += t.normalized.pos.y
        sumVX += t.normalized.vel.x
        fingers += 1
    }

    guard fingers == 3 else {
        // Losing the third contact ends tracking, but only a full lift re-arms.
        // Contacts flicker through break-touch state mid-swipe, and re-arming
        // on that let one physical swipe fire twice, which skipped a whole
        // workspace, since each fire is a full ae-cycle-ws step.
        tracking = false
        if fingers == 0 { fired = false }
        return 0
    }

    let x = sumX / fingers, y = sumY / fingers
    if !tracking {
        tracking = true
        anchorX = x
        anchorY = y
        return 0
    }

    guard !fired else { return 0 }
    let dx = x - anchorX, dy = y - anchorY
    let speed = abs(sumVX / fingers)
    // Fast flick clears early; anything slower has to go the full distance.
    let needed = speed >= fastSpeed ? fastThreshold : threshold
    guard abs(dx) >= needed, abs(dx) > abs(dy) * horizontalBias else { return 0 }

    fired = true
    let goesNext = invert ? (dx < 0) : (dx > 0)
    fire(goesNext ? "next" : "prev")
    // Logged to /tmp/ae-swipe.err. A double fire here is the first thing to
    // check if swipes ever start skipping workspaces again.
    FileHandle.standardError.write(
        "fire \(goesNext ? "next" : "prev") dx=\(dx) v=\(speed)\n".data(using: .utf8)!)
    return 0
}

// MARK: - Attach
guard let list = MTDeviceCreateList()?.takeRetainedValue() else {
    FileHandle.standardError.write("ae-swipe: MTDeviceCreateList returned nil\n".data(using: .utf8)!)
    exit(1)
}
let devices = list as NSArray as [AnyObject]
guard !devices.isEmpty else {
    FileHandle.standardError.write("ae-swipe: no multitouch devices\n".data(using: .utf8)!)
    exit(1)
}
for device in devices {
    let ref = unsafeBitCast(device, to: MTDeviceRef.self)
    MTRegisterContactFrameCallback(ref, callback)
    MTDeviceStart(ref, 0)
}
FileHandle.standardError.write("ae-swipe: watching \(devices.count) device(s)\n".data(using: .utf8)!)
CFRunLoopRun()
