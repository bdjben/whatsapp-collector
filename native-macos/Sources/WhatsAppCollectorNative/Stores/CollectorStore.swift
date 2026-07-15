import AppKit
import Foundation

@MainActor
final class CollectorStore: ObservableObject {
    private static let scheduleRunnerImplementationVersion = 1

    @Published var configuration: CollectorConfiguration = .defaults
    @Published var draftConfiguration: CollectorConfiguration = .defaults
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
    @Published var draftScheduleIntervalMinutes: Int = 15
    @Published var legacyAppCandidate: LegacyAppCandidate?
    @Published var legacyCleanupSummary: String?
    @Published var selectedSection: AppSection = .dashboard
    @Published var launchAtLoginEnabled: Bool = false
    @Published var launchAtLoginStatus: String = "Not configured"

    private let bridge = CollectorBridge()
    private let defaults = UserDefaults.standard
    private var schedulePollingTask: Task<Void, Never>?
    private var isRefreshingScheduledRunner = false

    var isBusy: Bool { busyState != .idle }

    var hasUnsavedChanges: Bool {
        draftConfiguration != configuration
            || draftScheduleIntervalMinutes != scheduleIntervalMinutes
    }

    var scheduledExportIsRunning: Bool { schedule?.isCurrentRunActive == true }

    var exportActivityIsVisible: Bool {
        busyState.isExportActivity || scheduledExportIsRunning
    }

    var exportActivityTitle: String {
        if busyState.isExportActivity {
            return busyState.title
        }
        if scheduledExportIsRunning {
            return "Scheduled Export Running"
        }
        return "Ready"
    }

    var scheduledExportStatusText: String {
        guard let schedule, schedule.isCurrentRunActive else {
            return "Automatic exports are idle."
        }
        if let startedAt = schedule.currentRunStartedAt {
            return "Scheduled export running since \(DisplayFormatters.relativeDate(startedAt))."
        }
        return "Scheduled export running."
    }

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
        draftConfiguration = loadedConfiguration
        scheduleIntervalMinutes = loadedInterval
        draftScheduleIntervalMinutes = loadedInterval
        aiPrompt = DisplayFormatters.aiPrompt(path: loadedConfiguration.outputPath)
        availableLabels = Self.loadStringArray(for: .availableLabels, from: UserDefaults.standard)
        refreshLaunchAtLoginStatus()
    }

    func bootstrap() async {
        startScheduleStatusPolling()
        await refreshStatus(applySchedulePayloadIfNeeded: true)
        await refreshScheduledRunnerIfNeeded()
        loadExportPreview()
        refreshLegacyAppCandidate()
        refreshLaunchAtLoginStatus()
    }

    deinit {
        schedulePollingTask?.cancel()
    }

    func saveDraftChanges(updateConfiguredSchedule: Bool = true) async -> Bool {
        configuration = normalized(configuration: draftConfiguration)
        draftConfiguration = configuration
        scheduleIntervalMinutes = boundedScheduleInterval(draftScheduleIntervalMinutes)
        draftScheduleIntervalMinutes = scheduleIntervalMinutes
        persistConfiguration()
        persistScheduleInterval()
        guard updateConfiguredSchedule, schedule?.enabled == true else {
            return true
        }
        guard let response = await perform(.scheduleInstall, busy: .scheduling, intervalMinutes: scheduleIntervalMinutes) else {
            return false
        }
        apply(response)
        markScheduledRunnerCurrent()
        return true
    }

    func discardDraftChanges() {
        draftConfiguration = configuration
        draftScheduleIntervalMinutes = scheduleIntervalMinutes
    }

    func requestSectionChange(_ section: AppSection) {
        guard section != selectedSection else { return }
        switch unsavedChangesDecision(context: "before leaving \(selectedSection.title)") {
        case .none, .discard:
            discardDraftChanges()
            selectedSection = section
        case .save:
            Task {
                if await saveDraftChanges() {
                    selectedSection = section
                }
            }
        case .cancel:
            break
        }
    }

    func unsavedChangesDecision(context: String) -> UnsavedChangesDecision {
        guard hasUnsavedChanges else { return .none }
        let alert = NSAlert()
        alert.messageText = "Save Changes?"
        alert.informativeText = "You have unsaved WhatsApp Collector settings changes. Save them \(context), discard them, or cancel and keep editing."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Save Changes")
        alert.addButton(withTitle: "Discard Changes")
        alert.addButton(withTitle: "Cancel")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            return .save
        case .alertSecondButtonReturn:
            return .discard
        default:
            return .cancel
        }
    }

    func saveConfiguration() {
        persistConfiguration()
    }

    private func persistConfiguration() {
        defaults.set(true, forKey: DefaultsKey.hasSavedConfiguration.rawValue)
        defaults.set(configuration.outputPath, forKey: DefaultsKey.outputPath.rawValue)
        defaults.set(configuration.profileDir, forKey: DefaultsKey.profileDir.rawValue)
        defaults.set(configuration.accountLabel, forKey: DefaultsKey.accountLabel.rawValue)
        defaults.set(configuration.maxMessages, forKey: DefaultsKey.maxMessages.rawValue)
        defaults.set(configuration.maxAllChats, forKey: DefaultsKey.maxAllChats.rawValue)
        defaults.set(configuration.allowLabels, forKey: DefaultsKey.allowLabels.rawValue)
        defaults.set(configuration.excludeLabels, forKey: DefaultsKey.excludeLabels.rawValue)
        defaults.set(configuration.includeGroups.rawValue, forKey: DefaultsKey.includeGroups.rawValue)
        defaults.set(configuration.downloadAttachments, forKey: DefaultsKey.downloadAttachments.rawValue)
        defaults.set(configuration.attachmentStorageLimitBytes, forKey: DefaultsKey.attachmentStorageLimitBytes.rawValue)
        defaults.set(configuration.displayName, forKey: DefaultsKey.displayName.rawValue)
        defaults.set(configuration.debugPort, forKey: DefaultsKey.debugPort.rawValue)
        defaults.set(configuration.markerTitle, forKey: DefaultsKey.markerTitle.rawValue)
        defaults.set(configuration.markerUrlSubstring, forKey: DefaultsKey.markerUrlSubstring.rawValue)
        defaults.set(configuration.targetUrl, forKey: DefaultsKey.targetUrl.rawValue)
        aiPrompt = DisplayFormatters.aiPrompt(path: configuration.outputPath)
    }

    func saveScheduleInterval() {
        scheduleIntervalMinutes = boundedScheduleInterval(scheduleIntervalMinutes)
        draftScheduleIntervalMinutes = scheduleIntervalMinutes
        persistScheduleInterval()
    }

    private func persistScheduleInterval() {
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
            draftConfiguration = configuration
            draftScheduleIntervalMinutes = scheduleIntervalMinutes
            persistConfiguration()
            persistScheduleInterval()
        }
    }

    func launchLogin() async {
        guard let response = await perform(.ensureWindow, busy: .launching) else {
            showChromeMissingAlertIfNeeded()
            return
        }
        apply(response)
        await refreshStatus()
    }

    func runExport() async {
        guard let response = await perform(.runExport, busy: .exporting) else { return }
        apply(response)
        loadExportPreview()
    }

    func loadLabels() async {
        guard let response = await perform(.labels, busy: .loadingLabels) else { return }
        apply(response)
        if let labels = response.labels {
            availableLabels = labels
            defaults.set(labels, forKey: DefaultsKey.availableLabels.rawValue)
        }
    }

    func installSchedule() async {
        guard await saveDraftChanges(updateConfiguredSchedule: false) else { return }
        guard let response = await perform(.scheduleInstall, busy: .scheduling, intervalMinutes: scheduleIntervalMinutes) else { return }
        apply(response)
        markScheduledRunnerCurrent()
    }

    func removeSchedule() async {
        guard let response = await perform(.scheduleRemove, busy: .scheduling) else { return }
        apply(response)
    }

    func refreshScheduleStatusQuietly() async {
        let bridge = self.bridge
        let config = configuration
        let wasRunning = scheduledExportIsRunning
        do {
            let (response, _) = try await Task.detached(priority: .utility) {
                try bridge.run(.scheduleStatus, configuration: config)
            }.value
            if let schedule = response.schedule {
                apply(schedule)
                if wasRunning && schedule.isCurrentRunActive == false {
                    loadExportPreview()
                }
                await refreshScheduledRunnerIfNeeded()
            }
        } catch {
            // Quiet polling should never interrupt the user's current task.
        }
    }

    private func refreshScheduledRunnerIfNeeded() async {
        guard isRefreshingScheduledRunner == false else { return }
        guard let schedule, schedule.enabled == true else { return }
        guard schedule.isCurrentRunActive == false else { return }
        let installedVersion = defaults.integer(forKey: DefaultsKey.scheduleRunnerImplementationVersion.rawValue)
        let needsRefresh = schedule.isLegacyWebSchedule || installedVersion < Self.scheduleRunnerImplementationVersion
        guard needsRefresh else { return }
        isRefreshingScheduledRunner = true
        defer { isRefreshingScheduledRunner = false }
        guard let response = await perform(.scheduleInstall, busy: .scheduling, intervalMinutes: scheduleIntervalMinutes) else { return }
        apply(response)
        markScheduledRunnerCurrent()
    }

    private func markScheduledRunnerCurrent() {
        defaults.set(Self.scheduleRunnerImplementationVersion, forKey: DefaultsKey.scheduleRunnerImplementationVersion.rawValue)
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
                attachmentPolicy: decoded.attachmentPolicy,
                attachmentSummary: decoded.attachmentSummary,
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
        draftConfiguration.excludeLabels.removeAll { $0.caseInsensitiveCompare(clean) == .orderedSame }
        if draftConfiguration.allowLabels.contains(where: { $0.caseInsensitiveCompare(clean) == .orderedSame }) == false {
            draftConfiguration.allowLabels.append(clean)
        }
    }

    func setExclude(_ label: String) {
        let clean = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        draftConfiguration.allowLabels.removeAll { $0.caseInsensitiveCompare(clean) == .orderedSame }
        if draftConfiguration.excludeLabels.contains(where: { $0.caseInsensitiveCompare(clean) == .orderedSame }) == false {
            draftConfiguration.excludeLabels.append(clean)
        }
    }

    func clearLabelDecision(_ label: String) {
        draftConfiguration.allowLabels.removeAll { $0.caseInsensitiveCompare(label) == .orderedSame }
        draftConfiguration.excludeLabels.removeAll { $0.caseInsensitiveCompare(label) == .orderedSame }
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

    func copyDraftOutputPath() {
        copy(draftConfiguration.outputPath)
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

    func openDraftProfileFolder() {
        let url = URL(fileURLWithPath: NSString(string: draftConfiguration.profileDir).expandingTildeInPath)
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
            apply(schedule)
        }
        if let prompt = response.aiPrompt {
            aiPrompt = prompt
        } else {
            aiPrompt = DisplayFormatters.aiPrompt(path: configuration.outputPath)
        }
    }

    private func apply(_ schedule: ScheduleState) {
        let hadUnsavedChanges = hasUnsavedChanges
        self.schedule = schedule
        if let interval = schedule.intervalMinutes {
            scheduleIntervalMinutes = interval
            if hadUnsavedChanges == false {
                draftScheduleIntervalMinutes = interval
            }
        }
    }

    private func startScheduleStatusPolling() {
        guard schedulePollingTask == nil else { return }
        schedulePollingTask = Task { [weak self] in
            while Task.isCancelled == false {
                try? await Task.sleep(nanoseconds: 10_000_000_000)
                await self?.refreshScheduleStatusQuietly()
            }
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

    private func showChromeMissingAlertIfNeeded() {
        guard let lastError, lastError.localizedCaseInsensitiveContains("Google Chrome is not installed") else { return }
        let alert = NSAlert()
        alert.messageText = "Google Chrome Required"
        alert.informativeText = lastError
        alert.alertStyle = .warning
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
        if defaults.object(forKey: DefaultsKey.downloadAttachments.rawValue) != nil {
            config.downloadAttachments = defaults.bool(forKey: DefaultsKey.downloadAttachments.rawValue)
        }
        if let value = defaults.object(forKey: DefaultsKey.attachmentStorageLimitBytes.rawValue) as? NSNumber {
            config.attachmentStorageLimitBytes = value.int64Value
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

    private func normalized(configuration: CollectorConfiguration) -> CollectorConfiguration {
        var copy = configuration
        copy.maxMessages = max(1, min(copy.maxMessages, 500))
        copy.maxAllChats = max(1, min(copy.maxAllChats, 500))
        copy.attachmentStorageLimitBytes = max(100_000_000, min(copy.attachmentStorageLimitBytes, 100_000_000_000))
        copy.accountLabel = copy.accountLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.displayName = copy.displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.outputPath = copy.outputPath.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.profileDir = copy.profileDir.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.markerTitle = copy.markerTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.markerUrlSubstring = copy.markerUrlSubstring.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.targetUrl = copy.targetUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.debugPort = max(1, min(copy.debugPort, 65535))
        copy.cleanLabelLists()
        return copy
    }

    private func boundedScheduleInterval(_ value: Int) -> Int {
        max(1, min(value, 24 * 60))
    }

    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}

enum UnsavedChangesDecision {
    case none
    case save
    case discard
    case cancel
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
    case downloadAttachments = "downloadAttachments"
    case attachmentStorageLimitBytes = "attachmentStorageLimitBytes"
    case displayName = "displayName"
    case debugPort = "debugPort"
    case markerTitle = "markerTitle"
    case markerUrlSubstring = "markerUrlSubstring"
    case targetUrl = "targetUrl"
    case availableLabels = "availableLabels"
    case scheduleIntervalMinutes = "scheduleIntervalMinutes"
    case scheduleRunnerImplementationVersion = "scheduleRunnerImplementationVersion"
}
