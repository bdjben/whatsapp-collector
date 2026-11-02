import Darwin
import Foundation
import Testing
@testable import WhatsAppCollectorNative

private let baseDate = Date(timeIntervalSince1970: 1_800_000_000)

@Test
func enablingAutomationSchedulesFirstRunAfterInterval() {
    var record = AutomationScheduleRecord(intervalMinutes: 45)

    record.setEnabled(true, now: baseDate)

    #expect(record.enabled)
    #expect(record.nextRecurringRunAt == baseDate.addingTimeInterval(45 * 60))
    #expect(record.dueTriggers(at: baseDate).isEmpty)
    #expect(record.dueTriggers(at: baseDate.addingTimeInterval(45 * 60)) == [.recurring])
}

@Test
func unchangedIntervalPreservesExistingDueTime() {
    var record = AutomationScheduleRecord(intervalMinutes: 45)
    record.setEnabled(true, now: baseDate)
    let originalDue = record.nextRecurringRunAt

    record.setIntervalMinutes(45, now: baseDate.addingTimeInterval(10 * 60))

    #expect(record.nextRecurringRunAt == originalDue)
}

@Test
func changedIntervalTakesEffectWithoutStartingAnExport() {
    var record = AutomationScheduleRecord(intervalMinutes: 45)
    record.setEnabled(true, now: baseDate)
    let changedAt = baseDate.addingTimeInterval(10 * 60)

    record.setIntervalMinutes(90, now: changedAt)

    #expect(record.currentRunStartedAt == nil)
    #expect(record.nextRecurringRunAt == changedAt.addingTimeInterval(90 * 60))
}

@Test
func loginExportOccursOnlyOncePerConsoleSession() {
    let session = ConsoleLoginSession.make(username: "tester", uid: 501, loginSeconds: 1_800_000_000)
    var record = AutomationScheduleRecord()
    record.scheduleLoginExport(for: session)
    let due = session.startedAt.addingTimeInterval(AutomationScheduleRecord.loginDelay)

    #expect(record.dueTriggers(at: due) == [.login])
    record.beginRun(triggers: [.login], at: due)
    record.completeRun(
        at: due.addingTimeInterval(30),
        succeeded: true,
        failureMessage: nil,
        threadCount: 12,
        exportedAt: nil,
        outputPath: nil
    )
    record.scheduleLoginExport(for: session)

    #expect(record.loginRunAt == nil)
    #expect(record.dueTriggers(at: due.addingTimeInterval(60)).isEmpty)
}

@Test
func newLoginDefersOverdueRecurrenceToFiveMinuteLoginRun() {
    let session = ConsoleLoginSession.make(username: "tester", uid: 501, loginSeconds: 1_800_000_000)
    var record = AutomationScheduleRecord(intervalMinutes: 15)
    record.setEnabled(true, now: session.startedAt.addingTimeInterval(-60 * 60))

    record.scheduleLoginExport(for: session)
    let due = session.startedAt.addingTimeInterval(AutomationScheduleRecord.loginDelay)

    #expect(record.dueTriggers(at: session.startedAt).isEmpty)
    #expect(record.nextRecurringRunAt == due)
    #expect(record.dueTriggers(at: due) == [.login, .recurring])
}

@Test
func simultaneousLoginAndRecurringTriggersCoalesceIntoOneRun() {
    let session = ConsoleLoginSession.make(username: "tester", uid: 501, loginSeconds: 1_800_000_000)
    var record = AutomationScheduleRecord(intervalMinutes: 5)
    record.setEnabled(true, now: baseDate)
    record.scheduleLoginExport(for: session)
    let due = baseDate.addingTimeInterval(5 * 60)

    let triggers = record.dueTriggers(at: due)
    record.beginRun(triggers: triggers, at: due)

    #expect(triggers == [.login, .recurring])
    #expect(record.currentRunTriggers == triggers)
    #expect(record.loginRunAt == nil)
    #expect(record.nextRecurringRunAt == nil)
}

@Test
func wakeExportRunsFiveMinutesAfterOneWakeEvent() {
    var record = AutomationScheduleRecord()
    record.scheduleWakeExport(at: baseDate)
    let due = baseDate.addingTimeInterval(AutomationScheduleRecord.wakeDelay)

    record.scheduleWakeExport(at: baseDate.addingTimeInterval(10))

    #expect(record.wakeRunAt == due)
    #expect(record.dueTriggers(at: due.addingTimeInterval(-1)).isEmpty)
    #expect(record.dueTriggers(at: due) == [.wake])
}

@Test
func wakeExportCoalescesWithRecurrenceDueBeforeIt() {
    var record = AutomationScheduleRecord(intervalMinutes: 2)
    record.setEnabled(true, now: baseDate)

    record.scheduleWakeExport(at: baseDate)
    let due = baseDate.addingTimeInterval(AutomationScheduleRecord.wakeDelay)

    #expect(record.nextRecurringRunAt == due)
    #expect(record.dueTriggers(at: due) == [.recurring, .wake])
}

@Test
func disablingEventExportsCancelsLoginAndWakeWork() {
    let session = ConsoleLoginSession.make(username: "tester", uid: 501, loginSeconds: 1_800_000_000)
    var record = AutomationScheduleRecord()
    record.scheduleLoginExport(for: session)
    record.scheduleWakeExport(at: baseDate)

    record.cancelPendingEventExports()

    #expect(record.loginRunAt == nil)
    #expect(record.wakeRunAt == nil)
    #expect(record.dueTriggers(at: baseDate.addingTimeInterval(10 * 60)).isEmpty)
}

@Test
func overdueRecurringRunCatchesUpOnceAfterWake() {
    var record = AutomationScheduleRecord(intervalMinutes: 15)
    record.setEnabled(true, now: baseDate)
    let wakeDate = baseDate.addingTimeInterval(2 * 60 * 60)

    let triggers = record.dueTriggers(at: wakeDate)
    record.beginRun(triggers: triggers, at: wakeDate)
    record.completeRun(
        at: wakeDate.addingTimeInterval(60),
        succeeded: true,
        failureMessage: nil,
        threadCount: 20,
        exportedAt: nil,
        outputPath: nil
    )

    #expect(triggers == [.recurring])
    #expect(record.dueTriggers(at: wakeDate.addingTimeInterval(61)).isEmpty)
    #expect(record.nextRecurringRunAt == wakeDate.addingTimeInterval(16 * 60))
}

@Test
func cleanRelaunchDefersWorkMissedWhileAppWasQuit() {
    var record = AutomationScheduleRecord(intervalMinutes: 20)
    record.setEnabled(true, now: baseDate)
    let session = ConsoleLoginSession.make(username: "tester", uid: 501, loginSeconds: 1_800_000_000)
    record.scheduleLoginExport(for: session)
    record.scheduleWakeExport(at: baseDate)
    let relaunchedAt = baseDate.addingTimeInterval(2 * 60 * 60)

    record.resumeAfterCleanShutdown(at: relaunchedAt)

    #expect(record.dueTriggers(at: relaunchedAt).isEmpty)
    #expect(record.loginRunAt == nil)
    #expect(record.wakeRunAt == nil)
    #expect(record.nextRecurringRunAt == relaunchedAt.addingTimeInterval(20 * 60))
}

@Test
func recordsFromBeforeWakeSchedulingStillDecode() throws {
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    let encoded = try encoder.encode(AutomationScheduleRecord(intervalMinutes: 15))
    var payload = try #require(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
    payload.removeValue(forKey: "wakeRunAt")
    payload.removeValue(forKey: "lastWakeEventAt")
    let legacyData = try JSONSerialization.data(withJSONObject: payload)
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601

    let decoded = try decoder.decode(AutomationScheduleRecord.self, from: legacyData)

    #expect(decoded.wakeRunAt == nil)
    #expect(decoded.lastWakeEventAt == nil)
}

@Test
func interruptedRunIsRecordedAndRearmed() {
    var record = AutomationScheduleRecord(intervalMinutes: 30)
    record.setEnabled(true, now: baseDate)
    let due = baseDate.addingTimeInterval(30 * 60)
    record.beginRun(triggers: [.recurring], at: due)
    let relaunchedAt = due.addingTimeInterval(5 * 60)

    record.recoverInterruptedRun(at: relaunchedAt)

    #expect(record.currentRunStartedAt == nil)
    #expect(record.lastFailureAt == relaunchedAt)
    #expect(record.nextRecurringRunAt == relaunchedAt.addingTimeInterval(30 * 60))
}

@Test
func loginLaunchIsHiddenOnlyForFirstLaunchNearLogin() {
    let session = ConsoleLoginSession.make(username: "tester", uid: 501, loginSeconds: 1_800_000_000)

    #expect(LoginLaunchBehavior.shouldHideDashboard(
        launchAtLoginEnabled: true,
        launchMinimized: true,
        session: session,
        previouslySeenSessionID: nil,
        now: session.startedAt.addingTimeInterval(30)
    ))
    #expect(!LoginLaunchBehavior.shouldHideDashboard(
        launchAtLoginEnabled: true,
        launchMinimized: true,
        session: session,
        previouslySeenSessionID: session.id,
        now: session.startedAt.addingTimeInterval(30)
    ))
    #expect(!LoginLaunchBehavior.shouldHideDashboard(
        launchAtLoginEnabled: true,
        launchMinimized: true,
        session: session,
        previouslySeenSessionID: nil,
        now: session.startedAt.addingTimeInterval(20 * 60)
    ))
}

@Test
func failedAttemptKeepsLastSuccessAndRearmsRecurrence() throws {
    var record = AutomationScheduleRecord(intervalMinutes: 180)
    record.setEnabled(true, now: baseDate)
    let firstStart = baseDate.addingTimeInterval(180 * 60)
    let firstCompletion = firstStart.addingTimeInterval(60)
    record.beginRun(triggers: [.recurring], at: firstStart)
    record.completeRun(at: firstCompletion, succeeded: true, failureMessage: nil, threadCount: 25, exportedAt: nil, outputPath: nil)
    let secondStart = try #require(record.nextRecurringRunAt)
    let failureAt = secondStart.addingTimeInterval(60)
    record.beginRun(triggers: [.recurring], at: secondStart)
    record.completeRun(at: failureAt, succeeded: false, failureMessage: "A required chat was unavailable.", threadCount: nil, exportedAt: nil, outputPath: nil)

    #expect(record.lastRunAt == failureAt)
    #expect(record.lastSuccessAt == firstCompletion)
    #expect(record.nextRecurringRunAt == failureAt.addingTimeInterval(180 * 60))
    #expect(record.recentAttempts?.map(\.outcome) == [.succeeded, .failed])
    #expect(record.recentAttempts?.last?.failureMessage == "A required chat was unavailable.")
}

@Test
func manualAttemptDoesNotMoveTheRecurringSchedule() {
    var record = AutomationScheduleRecord(intervalMinutes: 180)
    record.setEnabled(true, now: baseDate)
    let originalDue = record.nextRecurringRunAt
    record.beginAttempt(triggers: [], at: baseDate.addingTimeInterval(60))
    record.finishAttempt(at: baseDate.addingTimeInterval(90), outcome: .succeeded, failureMessage: nil, threadCount: 12)

    #expect(record.nextRecurringRunAt == originalDue)
    #expect(record.lastRunAt == nil)
    #expect(record.recentAttempts?.last?.triggers.isEmpty == true)
    #expect(record.recentAttempts?.last?.threadCount == 12)
}

@Test
func manualAttemptAbsorbsAnAutomaticTriggerWithoutDuplicatingHistory() {
    var record = AutomationScheduleRecord(intervalMinutes: 1)
    record.setEnabled(true, now: baseDate)
    record.beginAttempt(triggers: [], at: baseDate)
    record.beginRun(triggers: [.recurring], at: baseDate)
    record.absorbDueTriggers([.wake])
    record.completeRun(at: baseDate.addingTimeInterval(90), succeeded: true, failureMessage: nil, threadCount: 12, exportedAt: nil, outputPath: nil)

    #expect(record.recentAttempts?.count == 1)
    #expect(record.recentAttempts?.last?.triggers == [.recurring, .wake])
    #expect(record.recentAttempts?.last?.outcome == .succeeded)
}

@Test
func recentAttemptHistoryIsBounded() {
    var record = AutomationScheduleRecord()
    for index in 0..<25 {
        let date = baseDate.addingTimeInterval(Double(index * 60))
        record.beginAttempt(triggers: [], at: date)
        record.finishAttempt(at: date.addingTimeInterval(30), outcome: .succeeded, failureMessage: nil, threadCount: 1)
    }

    #expect(record.recentAttempts?.count == 20)
    #expect(record.recentAttempts?.first?.startedAt == baseDate.addingTimeInterval(5 * 60))
}

@Test
func existingSettingsDecodeWithoutNewDiagnosticsFields() throws {
    var record = AutomationScheduleRecord(intervalMinutes: 180)
    record.setEnabled(true, now: baseDate)
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    var payload = try #require(JSONSerialization.jsonObject(with: encoder.encode(record)) as? [String: Any])
    payload.removeValue(forKey: "recentAttempts")
    payload.removeValue(forKey: "schedulerInitializedAt")
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    let decoded = try decoder.decode(AutomationScheduleRecord.self, from: JSONSerialization.data(withJSONObject: payload))

    #expect(decoded.enabled)
    #expect(decoded.intervalMinutes == 180)
    #expect(decoded.nextRecurringRunAt == record.nextRecurringRunAt)
    #expect(decoded.recentAttempts == nil)
    #expect(decoded.schedulerInitializedAt == nil)
}

@Test
func interruptedManualAttemptSurvivesRestartAndIsMarkedInterrupted() throws {
    var record = AutomationScheduleRecord(intervalMinutes: 180)
    record.setEnabled(true, now: baseDate)
    let originalDue = record.nextRecurringRunAt
    record.beginAttempt(triggers: [], at: baseDate)
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    var restored = try decoder.decode(AutomationScheduleRecord.self, from: encoder.encode(record))
    restored.recoverInterruptedRun(at: baseDate.addingTimeInterval(60))

    #expect(restored.nextRecurringRunAt == originalDue)
    #expect(restored.recentAttempts?.last?.outcome == .interrupted)
    #expect(restored.recentAttempts?.last?.completedAt == baseDate.addingTimeInterval(60))
}
