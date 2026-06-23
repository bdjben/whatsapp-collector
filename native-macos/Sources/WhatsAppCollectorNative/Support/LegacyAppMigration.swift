import AppKit
import Foundation

struct LegacyAppCandidate: Equatable, Sendable {
    var appURL: URL?
    var exportsURL: URL?
    var version: String?
    var executableName: String?
    var hasZipapp: Bool
    var hasGeneratedMenuSource: Bool
    var isMenuBarAgent: Bool

    var displayPath: String { appURL?.path ?? exportsURL?.path ?? "unknown" }
    var displayVersion: String { version ?? (appURL == nil ? "not installed" : "unknown") }
    var hasAppBundle: Bool { appURL != nil }
    var hasExportContent: Bool { exportsURL != nil }
}

struct LegacyCleanupResult: Sendable {
    var backupURL: URL?
    var trashedURL: URL?
}

enum LegacyAppMigration {
    static let defaultExportsURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Documents/WhatsApp Collector/Exports")

    static let backupParentURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Documents/WhatsApp Collector/Backups")

    static func findCandidate(currentBundleURL: URL = Bundle.main.bundleURL) -> LegacyAppCandidate? {
        let candidates = [
            URL(fileURLWithPath: "/Applications/WhatsApp Collector.app"),
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Applications/WhatsApp Collector.app")
        ]

        var appCandidate: LegacyAppCandidate?
        for appURL in candidates where isDifferentBundle(appURL, from: currentBundleURL) {
            guard FileManager.default.fileExists(atPath: appURL.path) else { continue }
            guard let candidate = inspect(appURL) else { continue }
            appCandidate = candidate
            break
        }

        let exportsURL = exportsFolderHasContent(defaultExportsURL) ? defaultExportsURL : nil
        if var appCandidate {
            appCandidate.exportsURL = exportsURL
            return appCandidate
        }
        if let exportsURL {
            return LegacyAppCandidate(
                appURL: nil,
                exportsURL: exportsURL,
                version: nil,
                executableName: nil,
                hasZipapp: false,
                hasGeneratedMenuSource: false,
                isMenuBarAgent: false
            )
        }
        return nil
    }

    static func cleanup(_ candidate: LegacyAppCandidate) throws -> LegacyCleanupResult {
        let backupURL = try backupExportsIfPresent()
        var trashedURL: NSURL?
        if let appURL = candidate.appURL {
            try FileManager.default.trashItem(at: appURL, resultingItemURL: &trashedURL)
        }
        return LegacyCleanupResult(backupURL: backupURL, trashedURL: trashedURL as URL?)
    }

    private static func inspect(_ appURL: URL) -> LegacyAppCandidate? {
        let contentsURL = appURL.appendingPathComponent("Contents")
        let resourcesURL = contentsURL.appendingPathComponent("Resources")
        let infoURL = contentsURL.appendingPathComponent("Info.plist")
        guard
            let info = NSDictionary(contentsOf: infoURL) as? [String: Any]
        else {
            return nil
        }

        let executableName = info["CFBundleExecutable"] as? String
        let hasZipapp = FileManager.default.fileExists(atPath: resourcesURL.appendingPathComponent("whatsapp-collector.pyz").path)
        let hasGeneratedMenuSource = FileManager.default.fileExists(atPath: resourcesURL.appendingPathComponent("WhatsAppCollectorMenu.swift").path)
        let isMenuBarAgent = (info["LSUIElement"] as? Bool) == true
        let isNativeApp = executableName == "WhatsAppCollectorNative" && hasZipapp == false && hasGeneratedMenuSource == false
        guard isNativeApp == false else { return nil }
        guard isMenuBarAgent || hasZipapp || hasGeneratedMenuSource || executableName == "WhatsApp Collector" else { return nil }

        return LegacyAppCandidate(
            appURL: appURL,
            exportsURL: nil,
            version: info["CFBundleShortVersionString"] as? String,
            executableName: executableName,
            hasZipapp: hasZipapp,
            hasGeneratedMenuSource: hasGeneratedMenuSource,
            isMenuBarAgent: isMenuBarAgent
        )
    }

    private static func backupExportsIfPresent() throws -> URL? {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: defaultExportsURL.path, isDirectory: &isDirectory) else {
            return nil
        }
        try FileManager.default.createDirectory(at: backupParentURL, withIntermediateDirectories: true)
        let timestamp = Self.timestampFormatter.string(from: Date())
        let backupRoot = backupParentURL.appendingPathComponent("legacy-app-\(timestamp)", isDirectory: true)
        try FileManager.default.createDirectory(at: backupRoot, withIntermediateDirectories: true)
        let backupExports = backupRoot.appendingPathComponent(defaultExportsURL.lastPathComponent, isDirectory: isDirectory.boolValue)
        try FileManager.default.copyItem(at: defaultExportsURL, to: backupExports)
        return backupRoot
    }

    private static func exportsFolderHasContent(_ url: URL) -> Bool {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) else {
            return false
        }
        if isDirectory.boolValue == false {
            return true
        }
        let contents = (try? FileManager.default.contentsOfDirectory(
            at: url,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )) ?? []
        return contents.isEmpty == false
    }

    private static func isDifferentBundle(_ appURL: URL, from currentBundleURL: URL) -> Bool {
        let candidate = appURL.standardizedFileURL.resolvingSymlinksInPath().path
        let current = currentBundleURL.standardizedFileURL.resolvingSymlinksInPath().path
        return candidate != current
    }

    private static let timestampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter
    }()
}
