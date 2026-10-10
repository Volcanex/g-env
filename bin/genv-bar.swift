// genv-bar: menu bar front end for the environment.
//
// Deliberately thin: every action shells out to `genv` (up/down/ipad/term), so
// the menu and the CLI can't drift apart: cli/env.py is the single
// implementation and this is one of its two faces. Nothing here knows how to
// start AeroSpace or talk to Sidecar.
//
// No .app bundle: it's a bare binary run by a LaunchAgent, with the activation
// policy set to .accessory so it gets a status item and no Dock tile. That
// keeps it in bin/ next to ae-swipe and ae-fade and out of the codesigning /
// Gatekeeper problems that made AeroSpace's own start-at-login unreliable.
//
// State is read when the menu opens, plus a slow timer for the title glyph.
// Nothing polls hard: a menu bar item that spawns processes every second is a
// battery cost you pay all day for information you look at twice.

import AppKit

let genv = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".local/bin/genv").path

@discardableResult
func shell(_ launchPath: String, _ args: [String], wait: Bool = true) -> String {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: launchPath)
    task.arguments = args
    // genv is a python3.12 script and AeroSpace's binaries live in ~/.local/bin;
    // launchd hands us a minimal PATH that has neither.
    var environment = ProcessInfo.processInfo.environment
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    environment["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    task.environment = environment
    let pipe = Pipe()
    task.standardOutput = pipe
    task.standardError = Pipe()
    do { try task.run() } catch { return "" }
    guard wait else { return "" }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    task.waitUntilExit()
    return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
}

@discardableResult
func genvRun(_ args: [String], wait: Bool = true) -> String {
    shell(genv, args, wait: wait)
}

struct State {
    var wmUp = false
    var swipeUp = false
    var fadeUp = false
    var sidecar: [(name: String, connected: Bool)] = []
    var termHome = "secondary"
    var ipadSide = "right"
    var tiling = "on"

    var connectedPad: String? { sidecar.first { $0.connected }?.name }

    static func read() -> State {
        var s = State()
        // Process checks are done in-process: pgrep per item, four times a
        // menu open, is needless when AppKit already knows what's running.
        let apps = NSWorkspace.shared.runningApplications
        s.wmUp = apps.contains { $0.bundleIdentifier == "bobko.aerospace" }
        // ae-swipe and ae-fade are bare binaries with no bundle id, so they
        // aren't in runningApplications. These two need pgrep.
        s.swipeUp = shell("/usr/bin/pgrep", ["-x", "ae-swipe"]) != ""
        s.fadeUp = shell("/usr/bin/pgrep", ["-x", "ae-fade"]) != ""
        s.termHome = genvRun(["term"]) == "main" ? "main" : "secondary"
        s.ipadSide = genvRun(["ipad", "side"]) == "left" ? "left" : "right"
        s.tiling = genvRun(["tiling"]) == "off" ? "off" : "on"
        for line in shell(
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".local/bin/ae-sidecar").path, ["list"]
        ).split(separator: "\n") {
            let parts = line.split(separator: "\t", maxSplits: 1)
            guard let name = parts.first else { continue }
            s.sidecar.append((String(name), parts.count > 1 && parts[1] == "connected"))
        }
        return s
    }
}

final class Bar: NSObject, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var state = State()

    override init() {
        super.init()
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        refreshTitle()
        // Slow on purpose: this only has to catch a crash or an iPad waking up
        // between glances, and the menu itself refreshes on open.
        Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            self?.refreshTitle()
        }
    }

    /// An SF Symbol, as a template image so it inherits the menu bar's own
    /// colour and stays legible in light mode, dark mode and under a tinted
    /// wallpaper, which a literal text glyph does not.
    ///
    /// The symbol says what genv actually is day to day: workspaces. Filled
    /// while the window manager is running, outline while it isn't, so the two
    /// states differ in weight rather than in shape and it reads at a glance
    /// without having to identify which icon it is.
    func refreshTitle() {
        state = State.read()
        let name = state.wmUp ? "rectangle.3.group.fill" : "rectangle.3.group"
        if let image = NSImage(systemSymbolName: name, accessibilityDescription: "genv") {
            image.isTemplate = true
            item.button?.image = image
            item.button?.title = ""
        } else {
            // Older macOS without the symbol: never leave the item blank.
            item.button?.image = nil
            item.button?.title = state.wmUp ? "◼" : "◻"
        }
        var bits = [state.wmUp ? "genv: up" : "genv: down"]
        if let pad = state.connectedPad { bits.append("iPad: \(pad)") }
        if state.tiling == "off" { bits.append("tiling off") }
        item.button?.toolTip = bits.joined(separator: ", ")
    }

    func menuWillOpen(_ menu: NSMenu) {
        state = State.read()
        menu.removeAllItems()

        // Every item here is named for what clicking it DOES, never for what is
        // currently true. A toggle labelled with its own state reads backwards:
        // "terminal on ipad" looks like a command to put the terminal on the
        // iPad, and it meant the terminal is already there and this would send
        // it to the Mac. State is not lost: the menu bar glyph carries up/down,
        // the greyed detail rows carry the daemons, and every item's tooltip
        // says what is true now. The label is reserved for the verb.
        add(menu, state.wmUp ? "stop genv" : "start genv",
            action: #selector(toggleEnv), enabled: true)
        addDetail(menu, "aerospace", state.wmUp)
        addDetail(menu, "ae-swipe", state.swipeUp)
        addDetail(menu, "ae-fade", state.fadeUp)

        menu.addItem(.separator())

        if state.sidecar.isEmpty {
            addDetail(menu, "no iPad paired", false)
        } else {
            for device in state.sidecar {
                let entry = add(
                    menu, "\(device.connected ? "disconnect" : "connect")  \(device.name)",
                    action: #selector(toggleSidecar(_:)), enabled: true
                )
                entry.representedObject = device.name
                entry.toolTip = device.connected
                    ? "Connected as a display now. This disconnects it"
                    : "Not connected. This brings it up as a display"
            }
        }

        menu.addItem(.separator())

        // Only actionable with a second screen attached; greyed out rather than
        // hidden so the setting doesn't appear to vanish when you undock.
        let docked = state.sidecar.contains { $0.connected }
        let termNow = state.termHome == "main" ? "mac" : "ipad"
        let termNext = termNow == "mac" ? "ipad" : "mac"
        let term = add(menu, "terminal on \(termNext)",
                       action: #selector(toggleTerm), enabled: docked)
        term.toolTip = docked
            ? "On the \(termNow) now. Homes the Ghostty tab on the other screen, "
                + "applies on the next ⌘N"
            : "Needs a second display"

        let sideNow = state.ipadSide
        let side = add(menu, "ipad on the \(sideNow == "left" ? "right" : "left")",
                       action: #selector(toggleSide), enabled: docked)
        side.toolTip = docked
            ? "On the \(sideNow) now. Moves the iPad screen to the other side of the "
                + "laptop immediately, and that is where it lands on the next connect"
            : "Needs the iPad connected"

        let tiling = add(menu, "tiling \(state.tiling == "on" ? "off" : "on")",
                         action: #selector(toggleTiling), enabled: state.wmUp)
        tiling.toolTip = "Tiling is \(state.tiling) now. Off: windows float like normal "
            + "macOS. Workspaces, ⌘N and the terminal's screen keep working."

        menu.addItem(.separator())
        add(menu, "Quit genv-bar", action: #selector(quit), enabled: true)
    }

    @discardableResult
    func add(_ menu: NSMenu, _ title: String, action: Selector, enabled: Bool) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: enabled ? action : nil, keyEquivalent: "")
        entry.target = self
        entry.isEnabled = enabled
        menu.addItem(entry)
        return entry
    }

    func addDetail(_ menu: NSMenu, _ name: String, _ up: Bool) {
        let entry = NSMenuItem(title: "    \(name)\(up ? "" : " (down)")", action: nil, keyEquivalent: "")
        entry.isEnabled = false
        menu.addItem(entry)
    }

    /// `genv up` starts genv-bar too, which would kickstart us mid-click, but
    /// launchctl kickstart of an already-running agent is a no-op, so this is
    /// safe. `genv down` deliberately does stop us: down means down.
    @objc func toggleEnv() {
        let wasUp = state.wmUp
        DispatchQueue.global().async {
            genvRun([wasUp ? "down" : "up"])
            DispatchQueue.main.async { self.refreshTitle() }
        }
    }

    @objc func toggleSidecar(_ sender: NSMenuItem) {
        guard let name = sender.representedObject as? String else { return }
        // Off the main thread: connecting an asleep iPad can take seconds, and
        // a blocked main thread freezes the whole menu bar, not just this item.
        DispatchQueue.global().async {
            genvRun(["ipad", "toggle", name])
            DispatchQueue.main.async { self.refreshTitle() }
        }
    }

    @objc func toggleTerm() {
        genvRun(["term", "toggle"])
        refreshTitle()
    }

    /// Off the main thread: reconfiguring displays blocks until the window
    /// server has re-laid everything out, which is long enough to stall the
    /// menu bar itself if run inline.
    @objc func toggleSide() {
        DispatchQueue.global().async {
            genvRun(["ipad", "side", "toggle"])
            DispatchQueue.main.async { self.refreshTitle() }
        }
    }

    /// Re-laying out every existing window takes an aerospace call each, so this
    /// is slow enough to freeze the menu bar if run on the main thread.
    @objc func toggleTiling() {
        DispatchQueue.global().async {
            genvRun(["tiling", "toggle"])
            DispatchQueue.main.async { self.refreshTitle() }
        }
    }

    @objc func quit() {
        // Bootout as well as exit: KeepAlive would otherwise restart us
        // immediately and "Quit" would look broken.
        shell("/bin/launchctl", ["bootout", "gui/\(getuid())/com.gabriel.genv-bar"])
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let bar = Bar()
app.run()
