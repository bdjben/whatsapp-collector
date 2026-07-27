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
    let relaunchedAt = baseDate.addingTimeInterval(2 * 60 * 60)

    record.deferOverdueRecurringRun(from: relaunchedAt)

    #expect(record.dueTriggers(at: relaunchedAt).isEmpty)
    #expect(record.nextRecurringRunAt == relaunchedAt.addingTimeInterval(20 * 60))
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
