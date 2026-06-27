import AppKit
import Sparkle
import SwiftUI

private extension NSUserInterfaceItemIdentifier {
    static let whatsappCollectorMainWindow = NSUserInterfaceItemIdentifier("studio.bdjben.whatsapp-collector.main-window")
}

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
    static func markMainWindow(_ window: NSWindow?) {
        guard let window else { return }
        window.identifier = .whatsappCollectorMainWindow
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
    }

    @MainActor
    static func placeMainWindow() {
        closeDuplicateMainWindows()
        guard let window = mainWindows().first(where: { $0.isVisible }) ?? mainWindows().first else { return }
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
        guard let window = mainWindows().first(where: { $0.isVisible }) else {
            return false
        }
        window.tabbingMode = .disallowed
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    @MainActor
    private static func closeDuplicateMainWindows() {
        let visibleWindows = mainWindows().filter { $0.isVisible }
        guard visibleWindows.count > 1 else { return }
        for window in visibleWindows.dropFirst() {
            window.close()
        }
    }

    @MainActor
    private static func mainWindows() -> [NSWindow] {
        let identified = NSApp.windows.filter { $0.identifier == .whatsappCollectorMainWindow }
        if identified.isEmpty == false {
            return identified
        }
        return NSApp.windows.filter { window in
            window.level == .normal &&
            window.canBecomeMain &&
            window.contentViewController != nil &&
            window.title.localizedCaseInsensitiveContains(AppMetadata.appName)
        }
    }
}

@main
struct WhatsAppCollectorNativeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @Environment(\.openWindow) private var openWindow
    @StateObject private var store = CollectorStore()
    @StateObject private var updateMonitor = UpdateMonitor()
    private let updaterController: SPUStandardUpdaterController

    init() {
        updaterController = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
    }

    var body: some Scene {
        WindowGroup("WhatsApp Collector", id: "main") {
            ContentView()
                .environmentObject(store)
                .environmentObject(updateMonitor)
                .environment(\.appActions, appActions)
                .frame(minWidth: 1080, minHeight: 700)
                .background(MainWindowAccessor(store: store))
                .task {
                    updateMonitor.startAutomaticChecks()
                    await store.bootstrap()
                }
        }
        .defaultSize(width: 1180, height: 760)

        Window("AI Prompt", id: "ai-prompt") {
            AIPromptWindow()
                .environmentObject(store)
                .frame(minWidth: 680, minHeight: 520)
        }
        .defaultSize(width: 760, height: 620)

        .commands {
            CommandGroup(after: .appInfo) {
                Button("Check for Updates...") {
                    checkForUpdates()
                }
            }

            CommandMenu("Collector") {
                Button("Show Dashboard") {
                    showSection(.dashboard)
                }
                .keyboardShortcut("0", modifiers: [.command])

                Button("Show Export Preview") {
                    showSection(.export)
                }
                .keyboardShortcut("1", modifiers: [.command])

                Button("Show Help") {
                    showSection(.help)
                }
                .keyboardShortcut("?", modifiers: [.command, .shift])

                Divider()

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

                Button("View/Copy AI Prompt") {
                    showAIPrompt()
                }
                .keyboardShortcut("p", modifiers: [.command, .shift])

                Button("Reveal Export") {
                    store.revealOutput()
                }

                Button("Open GitHub Repository") {
                    AppMetadata.openRepository()
                }

                Divider()

                Button("Check for Updates...") {
                    checkForUpdates()
                }
            }

            CommandGroup(replacing: .help) {
                Button("WhatsApp Collector Help") {
                    showSection(.help)
                }
                Button("GitHub Repository") {
                    AppMetadata.openRepository()
                }
                Button("Latest Release") {
                    AppMetadata.openLatestRelease()
                }
            }
        }

        MenuBarExtra("W↗") {
            MenuBarContent()
                .environmentObject(store)
                .environmentObject(updateMonitor)
                .environment(\.appActions, appActions)
        }
    }

    private var appActions: AppActionHandlers {
        AppActionHandlers(
            checkForUpdates: checkForUpdates,
            openRepository: AppMetadata.openRepository,
            openLatestRelease: AppMetadata.openLatestRelease,
            showAIPrompt: showAIPrompt
        )
    }

    @MainActor
    private func showSection(_ section: AppSection) {
        store.requestSectionChange(section)
        if AppDelegate.showMainWindow() == false {
            openWindow(id: "main")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                AppDelegate.placeMainWindow()
            }
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    @MainActor
    private func checkForUpdates() {
        Task { await updateMonitor.checkNow(trigger: .manual) }
        updaterController.updater.checkForUpdates()
    }

    @MainActor
    private func showAIPrompt() {
        openWindow(id: "ai-prompt")
        NSApp.activate(ignoringOtherApps: true)
    }
}

struct MainWindowAccessor: NSViewRepresentable {
    @ObservedObject var store: CollectorStore

    func makeCoordinator() -> Coordinator {
        Coordinator(store: store)
    }

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        DispatchQueue.main.async {
            AppDelegate.markMainWindow(view.window)
            context.coordinator.attach(to: view.window)
        }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {
        DispatchQueue.main.async {
            AppDelegate.markMainWindow(view.window)
            context.coordinator.attach(to: view.window)
        }
    }

    final class Coordinator: NSObject, NSWindowDelegate {
        private let store: CollectorStore
        private weak var window: NSWindow?
        private var allowNextClose = false

        init(store: CollectorStore) {
            self.store = store
        }

        @MainActor
        func attach(to window: NSWindow?) {
            guard let window, self.window !== window else { return }
            if self.window?.delegate === self {
                self.window?.delegate = nil
            }
            self.window = window
            window.delegate = self
        }

        func windowShouldClose(_ sender: NSWindow) -> Bool {
            if allowNextClose {
                allowNextClose = false
                return true
            }
            switch store.unsavedChangesDecision(context: "before closing the app window") {
            case .none:
                return true
            case .discard:
                store.discardDraftChanges()
                return true
            case .save:
                Task {
                    if await store.saveDraftChanges() {
                        await MainActor.run {
                            self.allowNextClose = true
                            sender.close()
                        }
                    }
                }
                return false
            case .cancel:
                return false
            }
        }
    }
}

struct MenuBarContent: View {
    @EnvironmentObject private var store: CollectorStore
    @EnvironmentObject private var updateMonitor: UpdateMonitor
    @Environment(\.openWindow) private var openWindow
    @Environment(\.appActions) private var appActions

    var body: some View {
        if updateMonitor.state.updateAvailable {
            Button("Update Available: \(updateMonitor.state.latestVersion ?? "New Version")") {
                appActions.checkForUpdates()
            }
            Divider()
        }
        if store.exportActivityIsVisible {
            Label(store.exportActivityTitle, systemImage: "arrow.triangle.2.circlepath")
            Divider()
        }
        Button("Open Dashboard") {
            show(.dashboard)
        }
        Button("Open Export Preview") {
            show(.export)
        }
        Button("Open Help") {
            show(.help)
        }
        Divider()
        Button("Launch / Login") {
            Task { await store.launchLogin() }
        }
        Button("Run Export") {
            Task { await store.runExport() }
        }
        Button("View/Copy AI Prompt") {
            appActions.showAIPrompt()
        }
        Button("Reveal Export") {
            store.revealOutput()
        }
        Divider()
        Button("Check for Updates...") {
            appActions.checkForUpdates()
        }
        Button("GitHub Repository") {
            appActions.openRepository()
        }
        Divider()
        Text(store.schedule?.displayState ?? "Schedule unknown")
        Text(AppMetadata.displayVersion)
        Button("Quit") {
            NSApplication.shared.terminate(nil)
        }
    }

    @MainActor
    private func show(_ section: AppSection) {
        store.requestSectionChange(section)
        if AppDelegate.showMainWindow() == false {
            openWindow(id: "main")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                AppDelegate.placeMainWindow()
            }
        }
        NSApp.activate(ignoringOtherApps: true)
    }
}
