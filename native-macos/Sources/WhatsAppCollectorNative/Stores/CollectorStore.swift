import AppKit
import Foundation

@MainActor
final class CollectorStore: ObservableObject {
    private static let automationMigrationVersion = 1
    private static let automationPollNanoseconds: UInt64 = 15_000_000_000
    private static let legacyMigrationPollNanoseconds: UInt64 = 10_000_000_000
    private static let failedExportChromeGraceSeconds: TimeInterval = 5 * 60

    @Published var configuration: CollectorConfiguration = .defaults
    @Published var draftConfiguration: CollectorConfiguration = .defaults
    @Published var exportSummary: ExportSummary = .empty
    @Published var schedule: ScheduleState?
    @Published var availableLabels: [String] = []
    @Published var export: WhatsAppExport?
    @Published var selectedThreadID: String?
    @Published var busyState: BusyState = .idle
    @Published private(set) var exportProgress: ExportProgress?
    @Published private(set) var displayedExportProgress: ExportProgress?
    @Published var lastError: String?
    @Published var diagnostics: String = ""
    @Published private(set) var exportContentDiagnostics: String = ""
    @Published private(set) var lastExportFailureDiagnostics: String = ""
    @Published private(set) var lastSchedulerCheckAt: Date?
    @Published var aiPrompt: String = DisplayFormatters.aiPrompt(path: CollectorConfiguration.defaultOutputPath)
    @Published var scheduleIntervalMinutes: Int = 15
    @Published var draftScheduleIntervalMinutes: Int = 15
    @Published var legacyAppCandidate: LegacyAppCandidate?
    @Published var legacyCleanupSummary: String?
    @Published var selectedSection: AppSection = .dashboard
    @Published var launchAtLoginEnabled: Bool = false
    @Published var draftLaunchAtLoginEnabled: Bool = false
    @Published var launchAtLoginStatus: String = "Not configured"
    @Published var launchMinimizedEnabled: Bool = false
    @Published var draftLaunchMinimizedEnabled: Bool = false
    @Published var runExportAfterLoginEnabled: Bool = false
    @Published var draftRunExportAfterLoginEnabled: Bool = false

    private let bridge = CollectorBridge()
    private let defaults: UserDefaults
    private var automationRecord = AutomationScheduleRecord()
    private var automationTask: Task<Void, Never>?
    private var legacyMigrationTask: Task<Void, Never>?
    private var chromeCleanupTask: Task<Void, Never>?
    private var wakeObserver: NSObjectProtocol?
    private var exportInProgress = false
    private var legacyMigrationPending = false
    private var legacyScheduleState: ScheduleState?
    private var lastLoginSessionCheckAt: Date?
    private var didBootstrap = false

    var isBusy: Bool { busyState != .idle }

    var displayedDiagnostics: String {
        var sections = ["NATIVE AUTOMATION (current scheduler)\n\(automationDiagnostics.formattedJSON)"]
        if let exportProgress {
            sections.append("LATEST EXPORT PROGRESS (last reported phase, not proof of completion)\n\(exportProgress.summary)\n\(exportProgress.detail ?? "")")
        }
        if exportContentDiagnostics.isEmpty == false {
            sections.append("CURRENT EXPORT (saved content quality, skipped chats, and media outcomes)\n\(exportContentDiagnostics)")
        }
        if lastExportFailureDiagnostics.isEmpty == false {
            sections.append("LAST EXPORT FAILURE (retained for troubleshooting; may precede a later successful export)\n\(lastExportFailureDiagnostics)")
        }
        sections.append("LATEST BRIDGE RESPONSE (its schedule field describes the retired LaunchAgent, not native automation)\n\(diagnostics)")
        return sections.joined(separator: "\n\n")
    }

    var automationDiagnostics: AutomationDiagnostics {
        let blockedReason: String?
        if automationTask == nil {
            blockedReason = didBootstrap ? "Startup initialization is in progress." : "Application startup has not initialized automation."
        } else if legacyMigrationPending {
            blockedReason = "Waiting for migration from the older scheduler."
        } else if exportInProgress {
            blockedReason = "An export is already running."
        } else if isBusy {
            blockedReason = "Waiting for the current \(busyState.title.lowercased()) operation."
        } else {
            blockedReason = nil
        }
        return AutomationDiagnostics(
            appVersion: AppMetadata.shortVersionString,
            schedulerActive: automationTask != nil,
            schedulerInitializedAt: automationTask == nil ? nil : automationRecord.schedulerInitializedAt,
            lastSchedulerCheckAt: lastSchedulerCheckAt,
            blockedReason: blockedReason,
            launchAtLoginEnabled: launchAtLoginEnabled,
            runExportAfterLoginEnabled: runExportAfterLoginEnabled,
            schedule: automationRecord
        )
    }

    var hasUnsavedChanges: Bool {
        draftConfiguration != configuration
            || draftScheduleIntervalMinutes != scheduleIntervalMinutes
            || draftLaunchAtLoginEnabled != launchAtLoginEnabled
            || draftLaunchMinimizedEnabled != launchMinimizedEnabled
            || draftRunExportAfterLoginEnabled != runExportAfterLoginEnabled
    }

    var scheduledExportIsRunning: Bool { schedule?.isCurrentRunActive == true }

    var exportActivityIsVisible: Bool {
        busyState.isExportActivity || scheduledExportIsRunning
    }

    var exportActivityTitle: String {
        if scheduledExportIsRunning {
            return "Automatic Export Running"
        }
        if busyState.isExportActivity {
            return busyState.title
        }
        return "Ready"
    }

    var automationStatusText: String {
        if scheduledExportIsRunning {
            return "Running"
        }
        switch (automationRecord.enabled, runExportAfterLoginEnabled) {
        case (true, true):
            return "Recurring + Login/Wake"
        case (true, false):
            return "Recurring On"
        case (false, true):
            return "Login/Wake On"
        case (false, false):
            return "Off"
        }
    }

    var scheduledExportStatusText: String {
        guard let schedule, schedule.isCurrentRunActive else {
            return "Automatic exports are idle."
        }
        if let startedAt = schedule.currentRunStartedAt {
            return "Automatic export running since \(DisplayFormatters.relativeDate(startedAt))."
        }
        return "Automatic export running."
    }

    var launchAtLoginDisplayStatus: String {
        if draftLaunchAtLoginEnabled != launchAtLoginEnabled {
            return "Unsaved change. Click Save Changes to update Launch at Login."
        }
        return launchAtLoginStatus
    }

    var pendingLoginExportAt: String? {
        automationRecord.loginRunAt.map(Self.isoFormatter.string)
    }

    var pendingWakeExportAt: String? {
        automationRecord.wakeRunAt.map(Self.isoFormatter.string)
    }

    var selectedThread: ExportThread? {
        guard let selectedThreadID else { return export?.threads.first }
        return export?.threads.first { $0.id == selectedThreadID }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let loadedConfiguration = Self.loadConfiguration(from: defaults)
        let legacyInterval: Int
        if defaults.object(forKey: DefaultsKey.scheduleIntervalMinutes.rawValue) == nil {
            legacyInterval = 15
        } else {
            legacyInterval = max(1, defaults.integer(forKey: DefaultsKey.scheduleIntervalMinutes.rawValue))
        }
        let loadedAutomation = Self.loadAutomationRecord(
            from: defaults,
            fallbackIntervalMinutes: legacyInterval
        )
        configuration = loadedConfiguration
        draftConfiguration = loadedConfiguration
        automationRecord = loadedAutomation
        scheduleIntervalMinutes = loadedAutomation.intervalMinutes
        draftScheduleIntervalMinutes = loadedAutomation.intervalMinutes
        aiPrompt = DisplayFormatters.aiPrompt(path: loadedConfiguration.outputPath)
        availableLabels = Self.loadStringArray(for: .availableLabels, from: defaults)
        lastExportFailureDiagnostics = defaults.string(forKey: "lastExportFailureDiagnostics") ?? ""
        refreshLaunchAtLoginStatus()
        draftLaunchAtLoginEnabled = launchAtLoginEnabled
        launchMinimizedEnabled = defaults.bool(forKey: AppPreferences.launchMinimized)
        draftLaunchMinimizedEnabled = launchMinimizedEnabled
        runExportAfterLoginEnabled = defaults.bool(forKey: AppPreferences.runExportAfterLogin)
            && launchAtLoginEnabled
        draftRunExportAfterLoginEnabled = runExportAfterLoginEnabled
        if runExportAfterLoginEnabled == false {
            automationRecord.cancelPendingEventExports()
        }
        rebuildScheduleState()
    }

    func bootstrap() async {
        guard didBootstrap == false else { return }
        didBootstrap = true
#if DEBUG
        if ProcessInfo.processInfo.environment["WHATSAPP_COLLECTOR_QA_MODE"] == "1" {
            initializeLoginScheduling()
            startAutomationLoop()
            return
        }
#endif
        await refreshStatus(applySchedulePayloadIfNeeded: true)
        automationRecord.recoverInterruptedRun(at: Date())
        resumeAfterCleanShutdown()
        persistAutomationRecord()
        await migrateLegacySchedule()
        initializeLoginScheduling()
        startAutomationLoop()
        schedulePendingChromeCleanupIfNeeded()
        loadExportPreview()
        refreshLegacyAppCandidate()
        refreshLaunchAtLoginStatus()
        draftLaunchAtLoginEnabled = launchAtLoginEnabled
        rebuildScheduleState()
    }

    deinit {
        automationTask?.cancel()
        legacyMigrationTask?.cancel()
        chromeCleanupTask?.cancel()
    }

    func saveDraftChanges() async -> Bool {
        let newConfiguration = normalized(configuration: draftConfiguration)
        let newInterval = boundedScheduleInterval(draftScheduleIntervalMinutes)
        let newLaunchAtLogin = draftLaunchAtLoginEnabled
        let newLaunchMinimized = draftLaunchMinimizedEnabled
        let newRunExportAfterLogin = newLaunchAtLogin && draftRunExportAfterLoginEnabled
        let launchAtLoginChanged = newLaunchAtLogin != launchAtLoginEnabled
        let runExportAfterLoginChanged = newRunExportAfterLogin != runExportAfterLoginEnabled

        if launchAtLoginChanged {
            do {
                try LoginItemManager.setEnabled(newLaunchAtLogin)
            } catch {
                refreshLaunchAtLoginStatus()
                draftLaunchAtLoginEnabled = launchAtLoginEnabled
                lastError = "Could not update Launch at Login: \(error.localizedDescription)"
                diagnostics = error.localizedDescription
                return false
            }
        }

        configuration = newConfiguration
        draftConfiguration = newConfiguration
        scheduleIntervalMinutes = newInterval
        draftScheduleIntervalMinutes = newInterval
        launchAtLoginEnabled = LoginItemManager.isConfigured
        draftLaunchAtLoginEnabled = launchAtLoginEnabled
        launchMinimizedEnabled = newLaunchMinimized
        draftLaunchMinimizedEnabled = newLaunchMinimized
        runExportAfterLoginEnabled = newRunExportAfterLogin
        draftRunExportAfterLoginEnabled = newRunExportAfterLogin
        persistConfiguration()
        persistScheduleInterval()
        defaults.set(launchMinimizedEnabled, forKey: AppPreferences.launchMinimized)
        defaults.set(runExportAfterLoginEnabled, forKey: AppPreferences.runExportAfterLogin)

        automationRecord.setIntervalMinutes(newInterval, now: Date())
        if launchAtLoginChanged || runExportAfterLoginChanged {
            if launchAtLoginEnabled, runExportAfterLoginEnabled {
                markCurrentLoginSessionHandled()
            } else {
                automationRecord.cancelPendingEventExports()
            }
        }
        persistAutomationRecord()
        refreshLaunchAtLoginStatus()
        rebuildScheduleState()
        return true
    }

    func discardDraftChanges() {
        draftConfiguration = configuration
        draftScheduleIntervalMinutes = scheduleIntervalMinutes
        draftLaunchAtLoginEnabled = launchAtLoginEnabled
        draftLaunchMinimizedEnabled = launchMinimizedEnabled
        draftRunExportAfterLoginEnabled = runExportAfterLoginEnabled
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
        automationRecord.setIntervalMinutes(scheduleIntervalMinutes, now: Date())
        persistAutomationRecord()
        rebuildScheduleState()
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
                scheduleIntervalMinutes = boundedScheduleInterval(interval)
            }
            draftConfiguration = configuration
            draftScheduleIntervalMinutes = scheduleIntervalMinutes
            automationRecord.setIntervalMinutes(scheduleIntervalMinutes, now: Date())
            persistConfiguration()
            persistScheduleInterval()
            persistAutomationRecord()
            rebuildScheduleState()
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
        await executeExport(requestedTriggers: [])
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
        guard await saveDraftChanges() else { return }
        automationRecord.setEnabled(true, now: Date())
        persistAutomationRecord()
        rebuildScheduleState()
    }

    func removeSchedule() async {
        automationRecord.setEnabled(false, now: Date())
        persistAutomationRecord()
        rebuildScheduleState()
    }

    func refreshScheduleStatusQuietly() async {
        if legacyMigrationPending {
            await migrateLegacySchedule()
        }
        syncLoginScheduling()
        rebuildScheduleState()
    }

    func loadExportPreview() {
        let url = URL(fileURLWithPath: NSString(string: configuration.outputPath).expandingTildeInPath)
        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder().decode(WhatsAppExport.self, from: data)
            exportContentDiagnostics = ExportContentDiagnostics.formattedJSON(from: data)
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
            exportContentDiagnostics = ""
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

    func setDraftLaunchAtLogin(_ enabled: Bool) {
        draftLaunchAtLoginEnabled = enabled
        if enabled == false {
            draftLaunchMinimizedEnabled = false
            draftRunExportAfterLoginEnabled = false
        }
    }

    func refreshLaunchAtLoginStatus() {
        launchAtLoginEnabled = LoginItemManager.isConfigured
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

    private func executeExport(requestedTriggers: Set<AutomaticExportTrigger>) async {
        guard exportInProgress == false, busyState == .idle else { return }
        exportInProgress = true
        let startedAt = Date()
        var automaticTriggers = requestedTriggers
        automaticTriggers.formUnion(automationRecord.dueTriggers(at: startedAt))
        if automaticTriggers.isEmpty == false {
            automationRecord.beginRun(triggers: automaticTriggers, at: startedAt)
        } else {
            automationRecord.beginAttempt(triggers: [], at: startedAt)
        }
        persistAutomationRecord()
        rebuildScheduleState()

        await closePendingOwnedChromeNow()
        let ownershipURL = pendingChromeOwnershipURL
        defaults.set(ownershipURL.path, forKey: AppPreferences.pendingChromeOwnershipPath)
        let response = await perform(
            .runExport,
            busy: .exporting,
            chromeOwnershipURL: ownershipURL
        )
        let completedAt = Date()
        let additionalTriggers = automationRecord.dueTriggers(at: completedAt)
        if additionalTriggers.isEmpty == false {
            if automationRecord.isRunning {
                automationRecord.absorbDueTriggers(additionalTriggers)
            } else {
                automationRecord.beginRun(triggers: additionalTriggers, at: startedAt)
            }
        }

        let succeeded = response != nil
        if let response {
            apply(response)
            if response.window?.closedAfterExport == false {
                await closePendingOwnedChromeNow()
                schedulePendingChromeCleanupIfNeeded()
            } else {
                try? FileManager.default.removeItem(at: ownershipURL)
                defaults.removeObject(forKey: AppPreferences.pendingChromeOwnershipPath)
            }
            loadExportPreview()
        } else {
            try? FileManager.default.setAttributes([.modificationDate: completedAt], ofItemAtPath: ownershipURL.path)
            schedulePendingChromeCleanupIfNeeded()
            showChromeMissingAlertIfNeeded()
        }

        if automationRecord.isRunning {
            automationRecord.completeRun(
                at: completedAt,
                succeeded: succeeded,
                failureMessage: succeeded ? nil : lastError,
                threadCount: response?.threadCount ?? response?.export?.threadCount,
                exportedAt: response?.export?.exportedAt,
                outputPath: response?.export?.path
            )
            persistAutomationRecord()
            rebuildScheduleState()
        } else {
            automationRecord.finishAttempt(
                at: completedAt,
                outcome: succeeded ? .succeeded : .failed,
                failureMessage: succeeded ? nil : lastError,
                threadCount: response?.threadCount ?? response?.export?.threadCount
            )
            persistAutomationRecord()
            rebuildScheduleState()
        }
        exportInProgress = false
    }

    private func perform(
        _ command: BridgeCommand,
        busy: BusyState,
        intervalMinutes: Int? = nil,
        chromeOwnershipURL: URL? = nil
    ) async -> BridgeResponse? {
        busyState = busy
        lastError = nil
        if command == .runExport {
            exportProgress = nil
            displayedExportProgress = nil
        }
        let bridgeStartedAt = ContinuousClock.now
        let (progressStream, progressContinuation) = AsyncStream<ExportProgress>.makeStream(bufferingPolicy: .bufferingNewest(1))
        let progressTask = Task { @MainActor in
            for await progress in progressStream {
                if command == .runExport {
                    self.exportProgress = progress
                    // Producer phases use different clock origins; only the UI uses our shared clock.
                    self.displayedExportProgress = progress.displayed(since: bridgeStartedAt)
                }
            }
        }
        let bridge = self.bridge
        let config = configuration
        do {
            let (response, raw) = try await Task.detached(priority: .userInitiated) {
                try bridge.run(
                    command,
                    configuration: config,
                    intervalMinutes: intervalMinutes,
                    chromeOwnershipURL: chromeOwnershipURL,
                    onProgress: { progressContinuation.yield($0) }
                )
            }.value
            progressContinuation.finish()
            await progressTask.value
            diagnostics = raw
            busyState = .idle
            return response
        } catch {
            progressContinuation.finish()
            await progressTask.value
            busyState = .idle
            if let bridgeError = error as? BridgeError {
                lastError = bridgeError.message
                diagnostics = [bridgeError.message, bridgeError.details].filter { !$0.isEmpty }.joined(separator: "\n\n")
            } else {
                lastError = error.localizedDescription
                diagnostics = error.localizedDescription
            }
            if command == .runExport {
                lastExportFailureDiagnostics = "\(Self.isoFormatter.string(from: Date()))\n\(diagnostics)"
                defaults.set(lastExportFailureDiagnostics, forKey: "lastExportFailureDiagnostics")
            }
            return nil
        }
    }

    private func apply(_ response: BridgeResponse) {
        if let export = response.export {
            exportSummary = export
        }
        if let prompt = response.aiPrompt {
            aiPrompt = prompt
        } else {
            aiPrompt = DisplayFormatters.aiPrompt(path: configuration.outputPath)
        }
    }

    private func startAutomationLoop() {
        guard automationTask == nil else { return }
        automationRecord.schedulerInitializedAt = Date()
        persistAutomationRecord()
        if wakeObserver == nil {
            wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didWakeNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    await self?.handleWake()
                }
            }
        }
        automationTask = Task { [weak self] in
            while Task.isCancelled == false {
                await self?.evaluateAutomaticExports()
                try? await Task.sleep(nanoseconds: Self.automationPollNanoseconds)
            }
        }
        rebuildScheduleState()
    }

    private func evaluateAutomaticExports() async {
        lastSchedulerCheckAt = Date()
#if DEBUG
        if ProcessInfo.processInfo.environment["WHATSAPP_COLLECTOR_QA_MODE"] == "1",
           let path = ProcessInfo.processInfo.environment["WHATSAPP_COLLECTOR_QA_DIAGNOSTICS_PATH"] {
            try? Data(automationDiagnostics.formattedJSON.utf8).write(to: URL(fileURLWithPath: path), options: .atomic)
        }
#endif
        syncLoginScheduling()
        guard legacyMigrationPending == false else { return }
        guard exportInProgress == false, busyState == .idle else { return }
        let triggers = automationRecord.dueTriggers(at: Date())
        guard triggers.isEmpty == false else { return }
        await executeExport(requestedTriggers: triggers)
    }

    private func handleWake() async {
        if runExportAfterLoginEnabled {
            let original = automationRecord
            automationRecord.scheduleWakeExport(at: Date())
            if automationRecord != original {
                persistAutomationRecord()
                rebuildScheduleState()
            }
        }
        try? await Task.sleep(nanoseconds: 5_000_000_000)
        await evaluateAutomaticExports()
    }

    private func initializeLoginScheduling() {
        let initialized = defaults.bool(forKey: AppPreferences.loginSchedulingInitialized)
        if initialized == false {
            let now = Date()
            let session = ConsoleLoginSession.current()
            let nearLogin = session.map {
                let elapsed = now.timeIntervalSince($0.startedAt)
                return elapsed >= 0 && elapsed <= LoginLaunchBehavior.automaticLaunchWindow
            } ?? false
            if launchAtLoginEnabled, runExportAfterLoginEnabled, nearLogin, let session {
                automationRecord.scheduleLoginExport(for: session)
            } else {
                automationRecord.markLoginSessionHandled(session)
                automationRecord.deferOverdueRecurringRun(from: now)
            }
            defaults.set(true, forKey: AppPreferences.loginSchedulingInitialized)
            persistAutomationRecord()
            return
        }
        syncLoginScheduling()
    }

    private func resumeAfterCleanShutdown() {
        guard defaults.object(forKey: AppPreferences.cleanShutdownAt) != nil else { return }
        defaults.removeObject(forKey: AppPreferences.cleanShutdownAt)
        automationRecord.resumeAfterCleanShutdown(at: Date())
    }

    private func syncLoginScheduling() {
        let now = Date()
        if let lastLoginSessionCheckAt,
           now.timeIntervalSince(lastLoginSessionCheckAt) < 60 {
            return
        }
        lastLoginSessionCheckAt = now
        let hadDraftLoginChange = draftLaunchAtLoginEnabled != launchAtLoginEnabled
        let hadDraftEventChange = draftRunExportAfterLoginEnabled != runExportAfterLoginEnabled
        launchAtLoginEnabled = LoginItemManager.isConfigured
        launchAtLoginStatus = LoginItemManager.statusDescription
        if hadDraftLoginChange == false {
            draftLaunchAtLoginEnabled = launchAtLoginEnabled
        }
        if launchAtLoginEnabled == false, runExportAfterLoginEnabled {
            runExportAfterLoginEnabled = false
            defaults.set(false, forKey: AppPreferences.runExportAfterLogin)
        }
        if hadDraftEventChange == false {
            draftRunExportAfterLoginEnabled = runExportAfterLoginEnabled
        }
        let original = automationRecord
        if launchAtLoginEnabled, runExportAfterLoginEnabled, let session = ConsoleLoginSession.current() {
            automationRecord.scheduleLoginExport(for: session)
        } else {
            automationRecord.cancelPendingEventExports()
        }
        if automationRecord != original {
            persistAutomationRecord()
            rebuildScheduleState()
        }
    }

    private func markCurrentLoginSessionHandled() {
        automationRecord.markLoginSessionHandled(ConsoleLoginSession.current())
        defaults.set(true, forKey: AppPreferences.loginSchedulingInitialized)
    }

    private func migrateLegacySchedule() async {
        let bridge = self.bridge
        let config = configuration
        do {
            let (response, _) = try await Task.detached(priority: .utility) {
                try bridge.run(.scheduleStatus, configuration: config)
            }.value
            guard let legacy = response.schedule else {
                finishLegacyMigration()
                return
            }

            let legacyExists = legacy.enabled == true || legacy.loaded == true
            guard legacyExists else {
                finishLegacyMigration()
                return
            }

            adoptLegacySchedule(legacy)
            if legacy.isCurrentRunActive {
                if launchAtLoginEnabled, runExportAfterLoginEnabled {
                    automationRecord.markLoginSessionHandled(ConsoleLoginSession.current())
                    persistAutomationRecord()
                }
                legacyMigrationPending = true
                legacyScheduleState = legacy
                rebuildScheduleState()
                startLegacyMigrationPolling()
                return
            }

            let (removed, _) = try await Task.detached(priority: .utility) {
                try bridge.run(.scheduleRemove, configuration: config)
            }.value
            guard removed.ok else {
                throw BridgeError(removed.error ?? "Could not remove the older automatic-export LaunchAgent.")
            }
            if removed.schedule?.loaded == true {
                throw BridgeError("The older automatic-export LaunchAgent is still loaded; migration will retry.")
            }
            finishLegacyMigration()
        } catch {
            legacyMigrationPending = true
            legacyScheduleState = nil
            lastError = "Could not finish upgrading automatic exports: \(error.localizedDescription)"
            diagnostics = error.localizedDescription
            rebuildScheduleState()
            startLegacyMigrationPolling()
        }
    }

    private func adoptLegacySchedule(_ legacy: ScheduleState) {
        guard legacy.enabled == true else { return }
        let now = Date()
        if let interval = legacy.intervalMinutes {
            scheduleIntervalMinutes = boundedScheduleInterval(interval)
            draftScheduleIntervalMinutes = scheduleIntervalMinutes
            automationRecord.setIntervalMinutes(scheduleIntervalMinutes, now: now)
        }
        automationRecord.setEnabled(true, now: now)
        if let nextRun = DisplayFormatters.parseDate(legacy.nextRunAfter) {
            automationRecord.nextRecurringRunAt = nextRun
        }
        automationRecord.lastRunAt = DisplayFormatters.parseDate(legacy.lastRunAt)
        automationRecord.lastSuccessAt = DisplayFormatters.parseDate(legacy.lastSuccessAt)
        automationRecord.lastFailureAt = DisplayFormatters.parseDate(legacy.lastFailureAt)
        automationRecord.lastFailureMessage = legacy.lastFailureMessage
        automationRecord.lastThreadCount = legacy.lastThreadCount
        automationRecord.lastExportedAt = legacy.lastExportedAt
        automationRecord.lastOutputPath = legacy.lastOutputPath
        if launchAtLoginEnabled,
           runExportAfterLoginEnabled,
           let session = ConsoleLoginSession.current(),
           let lastSuccessAt = automationRecord.lastSuccessAt,
           lastSuccessAt >= session.startedAt {
            automationRecord.markLoginSessionHandled(session)
        }
        persistScheduleInterval()
        persistAutomationRecord()
    }

    private func startLegacyMigrationPolling() {
        guard legacyMigrationTask == nil else { return }
        legacyMigrationTask = Task { [weak self] in
            while Task.isCancelled == false {
                try? await Task.sleep(nanoseconds: Self.legacyMigrationPollNanoseconds)
                guard let self else { return }
                await self.migrateLegacySchedule()
                if self.legacyMigrationPending == false {
                    break
                }
            }
            self?.legacyMigrationTask = nil
        }
    }

    private func finishLegacyMigration() {
        legacyMigrationPending = false
        legacyScheduleState = nil
        defaults.set(Self.automationMigrationVersion, forKey: AppPreferences.automationMigrationVersion)
        persistAutomationRecord()
        rebuildScheduleState()
    }

    private var pendingChromeOwnershipURL: URL {
#if DEBUG
        if ProcessInfo.processInfo.environment["WHATSAPP_COLLECTOR_QA_MODE"] == "1",
           let path = ProcessInfo.processInfo.environment["WHATSAPP_COLLECTOR_QA_DIAGNOSTICS_PATH"] {
            return URL(fileURLWithPath: path).deletingLastPathComponent().appendingPathComponent("chrome-ownership.json")
        }
#endif
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/WhatsApp Collector")
            .appendingPathComponent("pending-chrome-ownership.json")
    }

    private func schedulePendingChromeCleanupIfNeeded() {
        let path = defaults.string(forKey: AppPreferences.pendingChromeOwnershipPath)
            ?? pendingChromeOwnershipURL.path
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.fileExists(atPath: url.path) else {
            defaults.removeObject(forKey: AppPreferences.pendingChromeOwnershipPath)
            return
        }
        defaults.set(url.path, forKey: AppPreferences.pendingChromeOwnershipPath)
        let modifiedAt = (try? FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate]) as? Date
        let elapsed = modifiedAt.map { Date().timeIntervalSince($0) } ?? 0
        let delaySeconds = max(0, Self.failedExportChromeGraceSeconds - elapsed)
        chromeCleanupTask?.cancel()
        chromeCleanupTask = Task { [weak self] in
            if delaySeconds > 0 {
                try? await Task.sleep(nanoseconds: UInt64(delaySeconds * 1_000_000_000))
            }
            await self?.closePendingOwnedChromeNow()
        }
    }

    private func closePendingOwnedChromeNow() async {
        let path = defaults.string(forKey: AppPreferences.pendingChromeOwnershipPath)
            ?? pendingChromeOwnershipURL.path
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.fileExists(atPath: url.path) else {
            defaults.removeObject(forKey: AppPreferences.pendingChromeOwnershipPath)
            return
        }
        let bridge = self.bridge
        do {
            _ = try await Task.detached(priority: .utility) {
                try bridge.closeOwnedChrome(at: url)
            }.value
            try? FileManager.default.removeItem(at: url)
            defaults.removeObject(forKey: AppPreferences.pendingChromeOwnershipPath)
        } catch {
            // Keep the exact-PID ownership record so the next launch can retry safely.
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

    private func rebuildScheduleState() {
        let nextStep: String
        if legacyMigrationPending {
            nextStep = legacyScheduleState?.isCurrentRunActive == true
                ? "An export from the older scheduler is finishing. The app will switch to in-app automation as soon as it is safely idle."
                : "WhatsApp Collector is retrying migration from the older automatic-export scheduler."
        } else if automationRecord.enabled {
            nextStep = "Automatic exports run while WhatsApp Collector remains open in the menu bar. Closing the dashboard is fine; choosing Quit stops automation."
        } else if runExportAfterLoginEnabled {
            nextStep = "Recurring exports are off. The optional login-and-wake export remains enabled."
        } else {
            nextStep = "Recurring automatic exports are off."
        }

        let currentRunActive = automationRecord.isRunning || legacyScheduleState?.isCurrentRunActive == true
        let currentRunStartedAt = automationRecord.currentRunStartedAt.map(Self.isoFormatter.string)
            ?? legacyScheduleState?.currentRunStartedAt
        let triggerDescription = automationRecord.currentRunTriggers
            .sorted { $0.rawValue < $1.rawValue }
            .map { trigger in
                switch trigger {
                case .login: "login"
                case .recurring: "recurring schedule"
                case .wake: "wake from sleep"
                }
            }
            .joined(separator: " and ")
        let currentRunMessage: String?
        if triggerDescription.isEmpty {
            currentRunMessage = legacyScheduleState?.currentRunMessage
        } else {
            currentRunMessage = "Automatic export started by \(triggerDescription)."
        }

        schedule = ScheduleState(
            inAppEnabled: automationRecord.enabled,
            intervalMinutes: automationRecord.intervalMinutes,
            nextStep: nextStep,
            lastRunAt: automationRecord.lastRunAt.map(Self.isoFormatter.string),
            lastSuccessAt: automationRecord.lastSuccessAt.map(Self.isoFormatter.string),
            lastFailureAt: automationRecord.lastFailureAt.map(Self.isoFormatter.string),
            lastFailureMessage: automationRecord.lastFailureMessage,
            lastThreadCount: automationRecord.lastThreadCount,
            lastExportedAt: automationRecord.lastExportedAt,
            lastOutputPath: automationRecord.lastOutputPath,
            nextRunAfter: automationRecord.nextRecurringRunAt.map(Self.isoFormatter.string),
            currentRunStatus: currentRunActive ? "running" : "idle",
            currentRunStartedAt: currentRunStartedAt,
            currentRunMessage: currentRunMessage,
            currentRunActive: currentRunActive
        )
    }

    private func persistAutomationRecord() {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(automationRecord) {
            defaults.set(data, forKey: AppPreferences.automationScheduleRecord)
        }
    }

    private static func loadAutomationRecord(
        from defaults: UserDefaults,
        fallbackIntervalMinutes: Int
    ) -> AutomationScheduleRecord {
        guard let data = defaults.data(forKey: AppPreferences.automationScheduleRecord) else {
            return AutomationScheduleRecord(intervalMinutes: fallbackIntervalMinutes)
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard var record = try? decoder.decode(AutomationScheduleRecord.self, from: data) else {
            return AutomationScheduleRecord(intervalMinutes: fallbackIntervalMinutes)
        }
        record.intervalMinutes = AutomationScheduleRecord.boundedInterval(record.intervalMinutes)
        return record
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
}
