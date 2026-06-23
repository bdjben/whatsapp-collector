import AppKit
import Sparkle
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSWindow.allowsAutomaticWindowTabbing = false
        NSApp.setActivationPolicy(.regular)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            Self.placeMainWindow()
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    @MainActor
    private static func placeMainWindow() {
        closeDuplicateMainWindows()
        guard let window = NSApp.windows.first(where: { $0.isVisible }) else { return }
        window.tabbingMode = .disallowed
        let targetScreen = NSScreen.screens.first { $0.frame.origin.x == 0 && $0.frame.origin.y == 0 } ?? NSScreen.main
        guard let visibleFrame = targetScreen?.visibleFrame else {
            window.makeKeyAndOrderFront(nil)
            return
        }
        let width = min(max(1120, visibleFrame.width * 0.78), visibleFrame.width - 80)
        let height = min(max(700, visibleFrame.height * 0.82), visibleFrame.height - 80)
        let origin = NSPoint(
            x: visibleFrame.midX - width / 2,
            y: visibleFrame.midY - height / 2
        )
        window.setFrame(NSRect(origin: origin, size: NSSize(width: width, height: height)), display: true)
        window.makeKeyAndOrderFront(nil)
    }

    @MainActor
    static func showMainWindow() -> Bool {
        closeDuplicateMainWindows()
        guard let window = NSApp.windows.first(where: { $0.isVisible || $0.canBecomeMain }) else {
            return false
        }
        window.tabbingMode = .disallowed
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    @MainActor
    private static func closeDuplicateMainWindows() {
        let visibleWindows = NSApp.windows.filter { $0.isVisible }
        guard visibleWindows.count > 1 else { return }
        for window in visibleWindows.dropFirst() {
            window.close()
        }
    }
}

@main
struct WhatsAppCollectorNativeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = CollectorStore()
    private let updaterController: SPUStandardUpdaterController

    init() {
        updaterController = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
    }

    var body: some Scene {
        WindowGroup("WhatsApp Collector", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1080, minHeight: 700)
                .task {
                    await store.bootstrap()
                }
                .onChange(of: store.configuration) {
                    store.saveConfiguration()
                }
                .onChange(of: store.scheduleIntervalMinutes) {
                    store.saveScheduleInterval()
                }
        }
        .defaultSize(width: 1180, height: 760)
        .commands {
            CommandGroup(after: .appInfo) {
                CheckForUpdatesView(updater: updaterController.updater)
            }

            CommandMenu("Collector") {
                Button("Launch / Login") {
                    Task { await store.launchLogin() }
                }
                .keyboardShortcut("l", modifiers: [.command, .shift])

                Button("Run Export") {
                    Task { await store.runExport() }
                }
                .keyboardShortcut("r", modifiers: [.command])

                Button("Load Labels") {
                    Task { await store.loadLabels() }
                }
                .keyboardShortcut("l", modifiers: [.command])

                Divider()

                Button("Reveal Export") {
                    store.revealOutput()
                }

                Divider()

                CheckForUpdatesView(updater: updaterController.updater)
            }
        }

        MenuBarExtra("W↗") {
            MenuBarContent(updater: updaterController.updater)
                .environmentObject(store)
        }
    }
}

struct CheckForUpdatesView: View {
    private let updater: SPUUpdater

    init(updater: SPUUpdater) {
        self.updater = updater
    }

    var body: some View {
        Button("Check for Updates...") {
            updater.checkForUpdates()
        }
    }
}

struct MenuBarContent: View {
    @EnvironmentObject private var store: CollectorStore
    @Environment(\.openWindow) private var openWindow
    let updater: SPUUpdater

    var body: some View {
        Button("Open Window") {
            if AppDelegate.showMainWindow() == false {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }
        }
        Divider()
        Button("Launch / Login") {
            Task { await store.launchLogin() }
        }
        Button("Run Export") {
            Task { await store.runExport() }
        }
        Button("Copy Prompt") {
            store.copyPrompt()
        }
        Button("Reveal Export") {
            store.revealOutput()
        }
        CheckForUpdatesView(updater: updater)
        Divider()
        Text(store.schedule?.displayState ?? "Schedule unknown")
        Button("Quit") {
            NSApplication.shared.terminate(nil)
        }
    }
}
