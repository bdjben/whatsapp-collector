import Foundation
import Testing
@testable import WhatsAppCollectorNative

@Test func displayedProgressUsesOneMonotonicClockWithoutChangingRawRecords() {
    let start = ContinuousClock.now
    let preparing = ExportProgress(phase: "preparing-browser", elapsedSeconds: 30)
    let collecting = ExportProgress(phase: "chat", completed: 7, detail: "Current conversation", elapsedSeconds: 2)
    let closing = ExportProgress(phase: "closing-browser", elapsedSeconds: 35)
    let first = preparing.displayed(since: start, at: start.advanced(by: .seconds(30)))
    let next = collecting.displayed(since: start, at: start.advanced(by: .seconds(32)))
    let last = closing.displayed(since: start, at: start.advanced(by: .seconds(35)))

    #expect(first.elapsedSeconds == 30)
    #expect(next.elapsedSeconds == 32)
    #expect(last.elapsedSeconds == 35)
    #expect(next.summary == "Chat | 7 processed | 32s elapsed")
    #expect(next.fractionCompleted == nil)
    #expect(next.detail == collecting.detail)
    #expect(preparing.elapsedSeconds == 30)
    #expect(collecting.elapsedSeconds == 2)
    #expect(closing.elapsedSeconds == 35)
    #expect(collecting.displayed(since: start, at: start).elapsedSeconds == 0)
}

@Test func progressWithUnknownTotalShowsProcessedCountWithoutFraction() throws {
    let data = Data(#"{"phase":"chat","completed":7,"total":null,"detail":"Collecting conversation","elapsedSeconds":42}"#.utf8)
    let progress = try JSONDecoder().decode(ExportProgress.self, from: data)
    #expect(progress.summary == "Chat | 7 processed | 42s elapsed")
    #expect(progress.fractionCompleted == nil)
    #expect(ExportProgress(phase: "chat", completed: 0).summary == "Chat | 0 processed")
    #expect(ExportProgress(phase: "chat", completed: -1).summary == "Chat")
    #expect(ExportProgress(phase: "chat").summary == "Chat")
    #expect(ExportProgress(phase: "chat", completed: 7, total: 10).summary == "Chat | 7 of 10")
}

@Test func progressParserHandlesFragmentedUTF8AndUnterminatedRecords() {
    var parser = BridgeProgressParser()
    var records: [ExportProgress] = []
    let input = "ordinary stderr\nWAC_PROGRESS:invalid\nWAC_PROGRESS:{\"phase\":\"collect_chat\",\"completed\":2,\"total\":5,\"detail\":\"Chat \\u05d0\",\"elapsedSeconds\":12.5}\r\nWAC_PROGRESS:{\"phase\":\"saving\"}"
    // Also exercise a literal multibyte UTF-8 sequence, split into single-byte reads.
    let bytes = Data(input.replacingOccurrences(of: "\\u05d0", with: String(UnicodeScalar(0x05d0)!)).utf8)
    for byte in bytes { parser.append(Data([byte])) { records.append($0) } }
    parser.finish { records.append($0) }
    #expect(records.count == 2)
    #expect(records.first?.fractionCompleted == 0.4)
    #expect(records.first?.summary == "Collect Chat | 2 of 5 | 12s elapsed")
    #expect(records.last?.phase == "saving")
    #expect(records.last?.fractionCompleted == nil)
}

@Test func progressParserIgnoresOversizedAndMalformedLinesAndRecovers() {
    var parser = BridgeProgressParser()
    var records: [ExportProgress] = []
    parser.append(Data((String(repeating: "x", count: 100_000) + "\nWAC_PROGRESS:{\"phase\":\"\"}\nWAC_PROGRESS:{\"phase\":\"retry\",\"completed\":-1,\"total\":0}\n").utf8)) { records.append($0) }
    #expect(records.count == 1)
    #expect(records.first?.fractionCompleted == nil)
    #expect(ExportProgress(phase: "collect", completed: 9, total: 3).fractionCompleted == 1)
}

@Test func bridgeDeliversProgressBeforeChildExitsAndPreservesStderr() throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/bash")
    process.arguments = ["-c", "printf 'WAC_PROGRESS:{\"phase\":\"collect\",\"completed\":1,\"total\":2}\\n' >&2; read -t 5 acknowledgement || exit 7; printf 'done'"]
    let stdout = Pipe()
    let stderr = Pipe()
    let stdin = Pipe()
    process.standardOutput = stdout
    process.standardError = stderr
    process.standardInput = stdin
    try process.run()
    let (output, errors) = BridgeProcessOutput.waitForExit(process, stdout: stdout, stderr: stderr) { _ in
        stdin.fileHandleForWriting.write(Data("received\n".utf8))
    }
    stdin.fileHandleForWriting.closeFile()
    #expect(process.terminationStatus == 0)
    #expect(String(decoding: output, as: UTF8.self) == "done")
    #expect(String(decoding: errors, as: UTF8.self).hasPrefix("WAC_PROGRESS:"))
}

@Test func manualFailureRefreshesDiagnosticsWithoutChangingAutomaticSchedule() {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    var record = AutomationScheduleRecord(intervalMinutes: 180)
    record.setEnabled(true, now: now)
    record.wakeRunAt = now.addingTimeInterval(60)
    let before = record
    record.beginAttempt(triggers: [], at: now)
    record.finishAttempt(at: now.addingTimeInterval(10), outcome: .failed, failureMessage: "Manual failure", threadCount: nil)
    #expect(record.lastFailureAt == now.addingTimeInterval(10))
    #expect(record.lastFailureMessage == "Manual failure")
    #expect(record.nextRecurringRunAt == before.nextRecurringRunAt)
    #expect(record.wakeRunAt == before.wakeRunAt)
    #expect(record.lastRunAt == before.lastRunAt)
    #expect(record.currentRunTriggers == before.currentRunTriggers)
    record.beginAttempt(triggers: [], at: now.addingTimeInterval(20))
    record.finishAttempt(at: now.addingTimeInterval(30), outcome: .succeeded, failureMessage: nil, threadCount: 4)
    #expect(record.lastFailureMessage == nil)
    #expect(record.lastFailureAt == now.addingTimeInterval(10))
    #expect(record.nextRecurringRunAt == before.nextRecurringRunAt)
    #expect(record.lastSuccessAt == before.lastSuccessAt)
}
