import AppKit
import Foundation

@MainActor
final class CollectorStore: ObservableObject {
    @Published var configuration: CollectorConfiguration = .defaults
    @Published var exportSummary: ExportSummary = .empty
    @Published var schedule: ScheduleState?
    @Published var availableLabels: [String] = []
    @Published var export: WhatsAppExport?
    @Published var selectedThreadID: String?
    @Published var busyState: BusyState = .idle
    @Published var lastError: String?
    @Published var diagnostics: String = ""
    @Published var aiPrompt: String = DisplayFormatters.aiPrompt(path: CollectorConfiguration.defaultOutputPath)
    @Published var scheduleIntervalMinutes: Int = 15
    @Published var legacyAppCandidate: LegacyAppCandidate?
    @Published var legacyCleanupSummary: String?
    @Published var selectedSection: AppSection = .dashboard
    @Published var launchAtLoginEnabled: Bool = false
    @Published var launchAtLoginStatus: String = "Not configured"

    private let bridge = CollectorBridge()
    private let defaults = UserDefaults.standard

    var isBusy: Bool { busyState != .idle }

    var selectedThread: ExportThread? {
        guard let selectedThreadID else { return export?.threads.first }
        return export?.threads.first { $0.id == selectedThreadID }
    }

    init() {
        let loadedConfiguration = Self.loadConfiguration(from: UserDefaults.standard)
        let loadedInterval: Int
        if UserDefaults.standard.object(forKey: DefaultsKey.scheduleIntervalMinutes.rawValue) == nil {
            loadedInterval = 15
        } else {
            loadedInterval = max(1, UserDefaults.standard.integer(forKey: DefaultsKey.scheduleIntervalMinutes.rawValue))
        }
        configuration = loadedConfiguration
        scheduleIntervalMinutes = loadedInterval
        aiPrompt = DisplayFormatters.aiPrompt(path: loadedConfiguration.outputPath)
        availableLabels = Self.loadStringArray(for: .availableLabels, from: UserDefaults.standard)
        refreshLaunchAtLoginStatus()
    }

    func bootstrap() async {
        await refreshStatus(applySchedulePayloadIfNeeded: true)
        loadExportPreview()
        refreshLegacyAppCandidate()
        refreshLaunchAtLoginStatus()
    }

    func saveConfiguration() {
        var clean = configuration
        clean.cleanLabelLists()
        if clean != configuration {
            configuration = clean
        }
        defaults.set(true, forKey: DefaultsKey.hasSavedConfiguration.rawValue)
        defaults.set(configuration.outputPath, forKey: DefaultsKey.outputPath.rawValue)
        defaults.set(configuration.profileDir, forKey: DefaultsKey.profileDir.rawValue)
        defaults.set(configuration.accountLabel, forKey: DefaultsKey.accountLabel.rawValue)
        defaults.set(configuration.maxMessages, forKey: DefaultsKey.maxMessages.rawValue)
        defaults.set(configuration.maxAllChats, forKey: DefaultsKey.maxAllChats.rawValue)
        defaults.set(configuration.allowLabels, forKey: DefaultsKey.allowLabels.rawValue)
        defaults.set(configuration.excludeLabels, forKey: DefaultsKey.excludeLabels.rawValue)
        defaults.set(configuration.includeGroups.rawValue, forKey: DefaultsKey.includeGroups.rawValue)
        defaults.set(configuration.displayName, forKey: DefaultsKey.displayName.rawValue)
        defaults.set(configuration.debugPort, forKey: DefaultsKey.debugPort.rawValue)
        defaults.set(configuration.markerTitle, forKey: DefaultsKey.markerTitle.rawValue)
        defaults.set(configuration.markerUrlSubstring, forKey: DefaultsKey.markerUrlSubstring.rawValue)
        defaults.set(configuration.targetUrl, forKey: DefaultsKey.targetUrl.rawValue)
        aiPrompt = DisplayFormatters.aiPrompt(path: configuration.outputPath)
    }

    func saveScheduleInterval() {
        scheduleIntervalMinutes = max(1, min(scheduleIntervalMinutes, 24 * 60))
        defaults.set(scheduleIntervalMinutes, forKey: DefaultsKey.scheduleIntervalMinutes.rawValue)
    }

    func refreshStatus(applySchedulePayloadIfNeeded: Bool = false) async {
        guard let response = await perform(.status, busy: .refreshing) else { return }
        apply(response)
        if applySchedulePayloadIfNeeded, defaults.bool(forKey: DefaultsKey.hasSavedConfiguration.rawValue) == false {
            configuration = configuration.withSchedulePayload(response.schedule?.payload)
            if let interval = response.schedule?.intervalMinutes {
                scheduleIntervalMinutes = interval
            }
            saveConfiguration()
            saveScheduleInterval()
        }
    }

    func launchLogin() async {
        guard let response = await perform(.ensureWindow, busy: .launching) else { return }
        apply(response)
        await refreshStatus()
    }

    func runExport() async {
        saveConfiguration()
        guard let response = await perform(.runExport, busy: .exporting) else { return }
        apply(response)
        loadExportPreview()
    }

    func loadLabels() async {
        saveConfiguration()
        guard let response = await perform(.labels, busy: .loadingLabels) else { return }
        apply(response)
        if let labels = response.labels {
            availableLabels = labels
            defaults.set(labels, forKey: DefaultsKey.availableLabels.rawValue)
        }
    }

    func installSchedule() async {
        saveConfiguration()
        saveScheduleInterval()
        guard let response = await perform(.scheduleInstall, busy: .scheduling, intervalMinutes: scheduleIntervalMinutes) else { return }
        apply(response)
    }

    func removeSchedule() async {
        guard let response = await perform(.scheduleRemove, busy: .scheduling) else { return }
        apply(response)
    }

    func loadExportPreview() {
        let url = URL(fileURLWithPath: NSString(string: configuration.outputPath).expandingTildeInPath)
        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder().decode(WhatsAppExport.self, from: data)
            let attributes = try? FileManager.default.attributesOfItem(atPath: url.path)
            let modifiedAt = (attributes?[.modificationDate] as? Date).map { Self.isoFormatter.string(from: $0) }
            let sizeBytes = (attributes?[.size] as? NSNumber)?.intValue ?? data.count
            export = WhatsAppExport(
                source: decoded.source,
                exportedAt: decoded.exportedAt,
                account: decoded.account,
                allowLabels: decoded.allowLabels,
                excludeLabels: decoded.excludeLabels,
                maxRecentMessages: decoded.maxRecentMessages,
                maxAllViewChats: decoded.maxAllViewChats,
                includeGroups: decoded.includeGroups,
                attachmentsRoot: decoded.attachmentsRoot,
                threads: decoded.threads.sortedByRecency(),
                exportWarnings: decoded.exportWarnings
            )
            exportSummary = ExportSummary(
                path: url.path,
                exists: true,
                threadCount: decoded.threads.count,
                sizeBytes: sizeBytes,
                updatedAt: modifiedAt,
                exportedAt: decoded.exportedAt,
                parseError: nil
            )
            if selectedThreadID == nil || decoded.threads.contains(where: { $0.id == selectedThreadID }) == false {
                selectedThreadID = export?.threads.first?.id
            }
            lastError = nil
        } catch {
            export = nil
            selectedThreadID = nil
            if FileManager.default.fileExists(atPath: url.path) {
                lastError = "Could not preview export: \(error.localizedDescription)"
            }
        }
    }

    func setAllow(_ label: String) {
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        configuration.excludeLabels.removeAll { $0.caseInsensitiveCompare(clean) == .orderedSame }
        if configuration.allowLabels.contains(where: { $0.caseInsensitiveCompare(clean) == .orderedSame }) == false {
            configuration.allowLabels.append(clean)
        }
        saveConfiguration()
    }

    func setExclude(_ label: String) {
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        configuration.allowLabels.removeAll { $0.caseInsensitiveCompare(clean) == .orderedSame }
        if configuration.excludeLabels.contains(where: { $0.caseInsensitiveCompare(clean) == .orderedSame }) == false {
            configuration.excludeLabels.append(clean)
        }
        saveConfiguration()
    }

    func clearLabelDecision(_ label: String) {
        configuration.allowLabels.removeAll { $0.caseInsensitiveCompare(label) == .orderedSame }
        configuration.excludeLabels.removeAll { $0.caseInsensitiveCompare(label) == .orderedSame }
        saveConfiguration()
    }

    func removeLabel(_ label: String) {
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        availableLabels.removeAll { $0.caseInsensitiveCompare(clean) == .orderedSame }
        defaults.set(availableLabels, forKey: DefaultsKey.availableLabels.rawValue)
        clearLabelDecision(clean)
    }

    func copyOutputPath() {
        copy(configuration.outputPath)
    }

    func copyPrompt() {
        copy(aiPrompt)
    }

    func defaultAIPrompt() -> String {
        DisplayFormatters.aiPrompt(path: configuration.outputPath)
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        do {
            try LoginItemManager.setEnabled(enabled)
            refreshLaunchAtLoginStatus()
        } catch {
            refreshLaunchAtLoginStatus()
            lastError = "Could not update Launch at Login: \(error.localizedDescription)"
            diagnostics = error.localizedDescription
        }
    }

    func refreshLaunchAtLoginStatus() {
        launchAtLoginEnabled = LoginItemManager.isEnabled
        launchAtLoginStatus = LoginItemManager.statusDescription
    }

    func copySelectedThreadJSON() {
        guard let selectedThread else { return }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard
            let data = try? encoder.encode(selectedThread),
            let text = String(data: data, encoding: .utf8)
        else {
            lastError = "Could not encode the selected thread as JSON."
            return
        }
        copy(text)
    }

    func refreshLegacyAppCandidate() {
        legacyAppCandidate = LegacyAppMigration.findCandidate()
    }

    func promptForLegacyCleanup() {
        refreshLegacyAppCandidate()
        guard let candidate = legacyAppCandidate else {
            legacyCleanupSummary = "No older WhatsApp Collector app or legacy export folder was found."
            return
        }
        let alert = NSAlert()
        alert.messageText = candidate.hasAppBundle ? "Older WhatsApp Collector Found" : "Existing WhatsApp Collector Exports Found"
        alert.informativeText = legacyCleanupPrompt(for: candidate)
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Not Now")
        alert.addButton(withTitle: candidate.hasAppBundle ? "Back Up Exports and Move Old App to Trash" : "Back Up Existing Exports")
        guard alert.runModal() == .alertSecondButtonReturn else {
            return
        }

        do {
            let result = try LegacyAppMigration.cleanup(candidate)
            legacyAppCandidate = nil
            let backup = result.backupURL?.path ?? "No exports folder was present to back up."
            let trashed = result.trashedURL?.path
            legacyCleanupSummary = [
                "Backed up exports: \(backup)",
                trashed.map { "Moved old app to: \($0)" },
            ].compactMap { $0 }.joined(separator: "\n")
            diagnostics = legacyCleanupSummary ?? diagnostics
            showLegacyCleanupResult(
                title: trashed == nil ? "Exports Backed Up" : "Older App Moved to Trash",
                message: legacyCleanupSummary ?? "Cleanup complete."
            )
        } catch {
            lastError = "Could not clean up the older app: \(error.localizedDescription)"
            diagnostics = error.localizedDescription
            showLegacyCleanupResult(title: "Cleanup Failed", message: error.localizedDescription)
        }
    }

    func revealOutput() {
        let url = URL(fileURLWithPath: NSString(string: configuration.outputPath).expandingTildeInPath)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    func openProfileFolder() {
        let url = URL(fileURLWithPath: NSString(string: configuration.profileDir).expandingTildeInPath)
        NSWorkspace.shared.open(url)
    }

    private func perform(_ command: BridgeCommand, busy: BusyState, intervalMinutes: Int? = nil) async -> BridgeResponse? {
        busyState = busy
        lastError = nil
        let bridge = self.bridge
        let config = configuration
        do {
            let (response, raw) = try await Task.detached(priority: .userInitiated) {
                try bridge.run(command, configuration: config, intervalMinutes: intervalMinutes)
            }.value
            diagnostics = raw
            busyState = .idle
            return response
        } catch {
            busyState = .idle
            if let bridgeError = error as? BridgeError {
                lastError = bridgeError.message
                diagnostics = [bridgeError.message, bridgeError.details].filter { !$0.isEmpty }.joined(separator: "\n\n")
            } else {
                lastError = error.localizedDescription
                diagnostics = error.localizedDescription
            }
            return nil
        }
    }

    private func apply(_ response: BridgeResponse) {
        if let export = response.export {
            exportSummary = export
        }
        if let schedule = response.schedule {
            self.schedule = schedule
            if let interval = schedule.intervalMinutes {
                scheduleIntervalMinutes = interval
            }
        }
        if let prompt = response.aiPrompt {
            aiPrompt = prompt
        } else {
            aiPrompt = DisplayFormatters.aiPrompt(path: configuration.outputPath)
        }
    }

    private func copy(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }

    private func showLegacyCleanupResult(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = title.contains("Failed") ? .warning : .informational
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func legacyCleanupPrompt(for candidate: LegacyAppCandidate) -> String {
        var lines: [String] = []
        if candidate.hasAppBundle {
            lines.append("""
            The app found the older menu-bar/web UI version at:
            \(candidate.displayPath)

            Version: \(candidate.displayVersion)
            """)
        }
        if let exportsURL = candidate.exportsURL {
            lines.append("""
            Existing export content was also found at:
            \(exportsURL.path)
            """)
        } else {
            lines.append("No existing exports were found in the legacy output folder.")
        }
        if candidate.hasAppBundle {
            lines.append("With your permission, WhatsApp Collector will copy existing exports to a timestamped backup, then move the older app to Trash. Your export JSON format and Chrome profile are not changed.")
        } else {
            lines.append("With your permission, WhatsApp Collector will copy those existing exports to a timestamped backup. No app will be removed.")
        }
        return lines.joined(separator: "\n\n")
    }

    private static func loadConfiguration(from defaults: UserDefaults) -> CollectorConfiguration {
        var config = CollectorConfiguration.defaults
        config.outputPath = defaults.string(forKey: DefaultsKey.outputPath.rawValue) ?? config.outputPath
        config.profileDir = defaults.string(forKey: DefaultsKey.profileDir.rawValue) ?? config.profileDir
        config.accountLabel = defaults.string(forKey: DefaultsKey.accountLabel.rawValue) ?? config.accountLabel
        if defaults.object(forKey: DefaultsKey.maxMessages.rawValue) != nil {
            config.maxMessages = defaults.integer(forKey: DefaultsKey.maxMessages.rawValue)
        }
        if defaults.object(forKey: DefaultsKey.maxAllChats.rawValue) != nil {
            config.maxAllChats = defaults.integer(forKey: DefaultsKey.maxAllChats.rawValue)
        }
        config.allowLabels = loadStringArray(for: .allowLabels, from: defaults)
        config.excludeLabels = loadStringArray(for: .excludeLabels, from: defaults)
        if let includeGroups = defaults.string(forKey: DefaultsKey.includeGroups.rawValue),
           let mode = GroupInclusionMode(rawValue: includeGroups) {
            config.includeGroups = mode
        }
        config.displayName = defaults.string(forKey: DefaultsKey.displayName.rawValue) ?? config.displayName
        if defaults.object(forKey: DefaultsKey.debugPort.rawValue) != nil {
            config.debugPort = defaults.integer(forKey: DefaultsKey.debugPort.rawValue)
        }
        config.markerTitle = defaults.string(forKey: DefaultsKey.markerTitle.rawValue) ?? config.markerTitle
        config.markerUrlSubstring = defaults.string(forKey: DefaultsKey.markerUrlSubstring.rawValue) ?? config.markerUrlSubstring
        config.targetUrl = defaults.string(forKey: DefaultsKey.targetUrl.rawValue) ?? config.targetUrl
        config.cleanLabelLists()
        return config
    }

    private static func loadStringArray(for key: DefaultsKey, from defaults: UserDefaults) -> [String] {
        if let values = defaults.stringArray(forKey: key.rawValue) {
            return values
        }
        return []
    }

    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}

private enum DefaultsKey: String {
    case hasSavedConfiguration = "hasSavedConfiguration"
    case outputPath = "outputPath"
    case profileDir = "profileDir"
    case accountLabel = "accountLabel"
    case maxMessages = "maxMessages"
    case maxAllChats = "maxAllChats"
    case allowLabels = "allowLabels"
    case excludeLabels = "excludeLabels"
    case includeGroups = "includeGroups"
    case displayName = "displayName"
    case debugPort = "debugPort"
    case markerTitle = "markerTitle"
    case markerUrlSubstring = "markerUrlSubstring"
    case targetUrl = "targetUrl"
    case availableLabels = "availableLabels"
    case scheduleIntervalMinutes = "scheduleIntervalMinutes"
}
