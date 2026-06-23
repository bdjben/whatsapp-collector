import AppKit
import SwiftUI

enum AppMetadata {
    static let appName = "WhatsApp Collector"
    static let repositoryURL = URL(string: "https://github.com/bdjben/whatsapp-collector")!
    static let latestReleaseURL = URL(string: "https://github.com/bdjben/whatsapp-collector/releases/latest")!

    static var shortVersionString: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development"
    }

    static var bundleVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? shortVersionString
    }

    static var displayVersion: String {
        if shortVersionString == "Development" {
            return "Development Build"
        }
        if bundleVersion.isEmpty || bundleVersion == shortVersionString {
            return "Version \(shortVersionString)"
        }
        return "Version \(shortVersionString) (\(bundleVersion))"
    }

    @MainActor
    static func openRepository() {
        NSWorkspace.shared.open(repositoryURL)
    }

    @MainActor
    static func openLatestRelease() {
        NSWorkspace.shared.open(latestReleaseURL)
    }
}

struct AppActionHandlers {
    var checkForUpdates: @MainActor () -> Void = {}
    var openRepository: @MainActor () -> Void = {}
    var openLatestRelease: @MainActor () -> Void = {}
}

private struct AppActionHandlersKey: EnvironmentKey {
    static let defaultValue = AppActionHandlers()
}

extension EnvironmentValues {
    var appActions: AppActionHandlers {
        get { self[AppActionHandlersKey.self] }
        set { self[AppActionHandlersKey.self] = newValue }
    }
}
