// ae-veil: full-screen black overlay to mask workspace-switch flashing.
// Usage: ae-veil [hold-seconds]   (default 0.4; then fades out and exits)
// Compiled by bootstrap.sh into ~/.local/bin/ae-veil (binary, not symlink).
import AppKit

let hold = CommandLine.arguments.count > 1 ? (Double(CommandLine.arguments[1]) ?? 0.4) : 0.4
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)

var veils: [NSWindow] = []
for screen in NSScreen.screens {
    let w = NSWindow(contentRect: screen.frame, styleMask: .borderless,
                     backing: .buffered, defer: false)
    w.backgroundColor = .black
    w.isOpaque = true
    w.level = .screenSaver
    w.ignoresMouseEvents = true
    w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
    w.orderFrontRegardless()
    veils.append(w)
}

DispatchQueue.main.asyncAfter(deadline: .now() + hold) {
    NSAnimationContext.runAnimationGroup({ ctx in
        ctx.duration = 0.05
        veils.forEach { $0.animator().alphaValue = 0 }
    }, completionHandler: { app.terminate(nil) })
}
app.run()
