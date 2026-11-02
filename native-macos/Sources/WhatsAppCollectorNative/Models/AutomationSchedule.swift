import Foundation

enum AutomaticExportTrigger: String, Codable, Hashable, Sendable {
    case login
    case recurring
    case wake
}

struct ExportAttempt: Codable, Equatable, Sendable {
    enum Outcome: String, Codable, Sendable {
        case running
        case succeeded
        case failed
        case interrupted
    }

    var startedAt: Date
    var completedAt: Date?
    var triggers: Set<AutomaticExportTrigger>
    var outcome: Outcome = .running
    var failureMessage: String?
    var threadCount: Int?
}

struct AutomationScheduleRecord: Codable, Equatable, Sendable {
    static let loginDelay: TimeInterval = 5 * 60
    static let wakeDelay: TimeInterval = 5 * 60
    static let wakeDeduplicationWindow: TimeInterval = 60
    static let retainedAttemptLimit = 20

    var enabled: Bool
    var intervalMinutes: Int
    var nextRecurringRunAt: Date?
    var loginSessionID: String?
    var loginRunAt: Date?
    var loginAttemptedSessionID: String?
    var wakeRunAt: Date?
    var lastWakeEventAt: Date?
    var currentRunStartedAt: Date?
    var currentRunTriggers: Set<AutomaticExportTrigger>
    var lastRunAt: Date?
    var lastSuccessAt: Date?
    var lastFailureAt: Date?
    var lastFailureMessage: String?
    var lastThreadCount: Int?
    var lastExportedAt: String?
    var lastOutputPath: String?
    var schedulerInitializedAt: Date?
    var recentAttempts: [ExportAttempt]?

    init(intervalMinutes: Int = 15) {
        enabled = false
        self.intervalMinutes = Self.boundedInterval(intervalMinutes)
        nextRecurringRunAt = nil
        loginSessionID = nil
        loginRunAt = nil
        loginAttemptedSessionID = nil
        wakeRunAt = nil
        lastWakeEventAt = nil
        currentRunStartedAt = nil
        currentRunTriggers = []
        lastRunAt = nil
        lastSuccessAt = nil
        lastFailureAt = nil
        lastFailureMessage = nil
        lastThreadCount = nil
        lastExportedAt = nil
        lastOutputPath = nil
        schedulerInitializedAt = nil
        recentAttempts = nil
    }

    var isRunning: Bool {
        currentRunStartedAt != nil
    }

    mutating func setEnabled(_ newValue: Bool, now: Date) {
        guard enabled != newValue else {
            repairNextRecurringRunIfNeeded(now: now)
            return
        }
        enabled = newValue
        nextRecurringRunAt = newValue ? Self.nextRun(after: now, intervalMinutes: intervalMinutes) : nil
    }

    mutating func setIntervalMinutes(_ newValue: Int, now: Date) {
        let bounded = Self.boundedInterval(newValue)
        guard intervalMinutes != bounded else {
            repairNextRecurringRunIfNeeded(now: now)
            return
        }
        intervalMinutes = bounded
        if enabled {
            nextRecurringRunAt = Self.nextRun(after: now, intervalMinutes: bounded)
        }
    }

    mutating func scheduleLoginExport(for session: ConsoleLoginSession) {
        guard loginSessionID != session.id else { return }
        loginSessionID = session.id
        loginAttemptedSessionID = nil
        let dueAt = session.startedAt.addingTimeInterval(Self.loginDelay)
        loginRunAt = dueAt
        if enabled, let nextRecurringRunAt, nextRecurringRunAt <= dueAt {
            self.nextRecurringRunAt = dueAt
        }
    }

    mutating func markLoginSessionHandled(_ session: ConsoleLoginSession?) {
        loginSessionID = session?.id
        loginAttemptedSessionID = session?.id
        loginRunAt = nil
    }

    mutating func cancelPendingLoginExport() {
        loginRunAt = nil
        loginAttemptedSessionID = loginSessionID
    }

    mutating func scheduleWakeExport(at wakeDate: Date) {
        if let lastWakeEventAt {
            let elapsed = wakeDate.timeIntervalSince(lastWakeEventAt)
            if elapsed >= 0, elapsed < Self.wakeDeduplicationWindow {
                return
            }
        }
        lastWakeEventAt = wakeDate
        let dueAt = wakeDate.addingTimeInterval(Self.wakeDelay)
        wakeRunAt = dueAt
        if enabled, let nextRecurringRunAt, nextRecurringRunAt <= dueAt {
            self.nextRecurringRunAt = dueAt
        }
    }

    mutating func cancelPendingWakeExport() {
        wakeRunAt = nil
    }

    mutating func cancelPendingEventExports() {
        cancelPendingLoginExport()
        cancelPendingWakeExport()
    }

    mutating func deferOverdueRecurringRun(from now: Date) {
        guard enabled, let nextRecurringRunAt, nextRecurringRunAt <= now else { return }
        self.nextRecurringRunAt = Self.nextRun(after: now, intervalMinutes: intervalMinutes)
    }

    mutating func resumeAfterCleanShutdown(at now: Date) {
        cancelPendingEventExports()
        deferOverdueRecurringRun(from: now)
    }

    func dueTriggers(at now: Date) -> Set<AutomaticExportTrigger> {
        var triggers = Set<AutomaticExportTrigger>()
        if enabled, let nextRecurringRunAt, nextRecurringRunAt <= now {
            triggers.insert(.recurring)
        }
        if let loginRunAt,
           loginRunAt <= now,
           let loginSessionID,
           loginAttemptedSessionID != loginSessionID {
            triggers.insert(.login)
        }
        if let wakeRunAt, wakeRunAt <= now {
            triggers.insert(.wake)
        }
        return triggers
    }

    mutating func beginRun(triggers: Set<AutomaticExportTrigger>, at date: Date) {
        guard triggers.isEmpty == false else { return }
        beginAttempt(triggers: triggers, at: date)
        absorbDueTriggers(triggers)
        currentRunStartedAt = date
    }

    mutating func absorbDueTriggers(_ triggers: Set<AutomaticExportTrigger>) {
        guard triggers.isEmpty == false else { return }
        if triggers.contains(.recurring) {
            nextRecurringRunAt = nil
        }
        if triggers.contains(.login) {
            loginRunAt = nil
            loginAttemptedSessionID = loginSessionID
        }
        if triggers.contains(.wake) {
            wakeRunAt = nil
        }
        currentRunTriggers.formUnion(triggers)
        if var attempts = recentAttempts, attempts.last?.outcome == .running {
            attempts[attempts.count - 1].triggers.formUnion(triggers)
            recentAttempts = attempts
        }
    }

    mutating func beginAttempt(triggers: Set<AutomaticExportTrigger>, at date: Date) {
        var attempts = recentAttempts ?? []
        if attempts.last?.outcome == .running {
            attempts[attempts.count - 1].triggers.formUnion(triggers)
        } else {
            attempts.append(ExportAttempt(startedAt: date, triggers: triggers))
        }
        recentAttempts = Array(attempts.suffix(Self.retainedAttemptLimit))
    }

    mutating func finishAttempt(
        at date: Date,
        outcome: ExportAttempt.Outcome,
        failureMessage: String?,
        threadCount: Int?
    ) {
        guard var attempts = recentAttempts, attempts.last?.outcome == .running else { return }
        let index = attempts.count - 1
        attempts[index].completedAt = date
        attempts[index].outcome = outcome
        attempts[index].failureMessage = failureMessage
        attempts[index].threadCount = threadCount
        recentAttempts = attempts
        // Manual attempts update failure diagnostics without consuming or rescheduling automatic triggers.
        if attempts[index].triggers.isEmpty {
            if outcome == .failed {
                lastFailureAt = date
                lastFailureMessage = failureMessage
            } else if outcome == .succeeded {
                lastFailureMessage = nil
            }
        }
    }

    mutating func completeRun(
        at date: Date,
        succeeded: Bool,
        failureMessage: String?,
        threadCount: Int?,
        exportedAt: String?,
        outputPath: String?
    ) {
        finishAttempt(
            at: date,
            outcome: succeeded ? .succeeded : .failed,
            failureMessage: failureMessage,
            threadCount: threadCount
        )
        let completedTriggers = currentRunTriggers
        lastRunAt = date
        if succeeded {
            lastSuccessAt = date
            lastFailureMessage = nil
            lastThreadCount = threadCount
            lastExportedAt = exportedAt
            lastOutputPath = outputPath
        } else {
            lastFailureAt = date
            lastFailureMessage = failureMessage
        }
        if completedTriggers.contains(.recurring), enabled {
            nextRecurringRunAt = Self.nextRun(after: date, intervalMinutes: intervalMinutes)
        }
        currentRunStartedAt = nil
        currentRunTriggers = []
        repairNextRecurringRunIfNeeded(now: date)
    }

    mutating func recoverInterruptedRun(at date: Date) {
        if let startedAt = currentRunStartedAt {
            beginAttempt(triggers: currentRunTriggers, at: startedAt)
        }
        finishAttempt(
            at: date,
            outcome: .interrupted,
            failureMessage: "The export was interrupted when WhatsApp Collector stopped.",
            threadCount: nil
        )
        guard isRunning else {
            repairNextRecurringRunIfNeeded(now: date)
            return
        }
        lastRunAt = date
        lastFailureAt = date
        lastFailureMessage = "The previous automatic export was interrupted when WhatsApp Collector stopped."
        currentRunStartedAt = nil
        currentRunTriggers = []
        if enabled {
            nextRecurringRunAt = Self.nextRun(after: date, intervalMinutes: intervalMinutes)
        }
    }

    mutating func repairNextRecurringRunIfNeeded(now: Date) {
        if enabled, nextRecurringRunAt == nil, isRunning == false {
            nextRecurringRunAt = Self.nextRun(after: now, intervalMinutes: intervalMinutes)
        } else if enabled == false {
            nextRecurringRunAt = nil
        }
    }

    static func boundedInterval(_ value: Int) -> Int {
        max(1, min(value, 24 * 60))
    }

    private static func nextRun(after date: Date, intervalMinutes: Int) -> Date {
        date.addingTimeInterval(TimeInterval(boundedInterval(intervalMinutes) * 60))
    }
}
