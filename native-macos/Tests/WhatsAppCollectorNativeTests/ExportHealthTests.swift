import Foundation
import XCTest
@testable import WhatsAppCollectorNative

final class ExportHealthTests: XCTestCase {
    func testFailureNamesChatsFromAllQualityReportFormats() throws {
        let report = try XCTUnwrap(ExportFailureReport.decode(retained: """
        {"exportQuality":{"ok":false,"issues":[
          {"code":"too-many-latest-messages-without-content","chatTitles":["Chat A","Chat B"]},
          {"code":"duplicate-thread-ids","threadKeys":["123@c.us"]},
          {"code":"live-chat-capture-incomplete","threads":[{"chatTitle":"Chat A"}]}
        ]}}
        """))
        XCTAssertEqual(report.chats.map(\.title), ["Chat A", "Chat B", "123@c.us"])
    }

    func testRetainedFailureNamesMissingChatAndMatchesOnlyItsAttempt() throws {
        let raw = """
        2026-11-10T10:06:11Z
        Validation failed
        stdout:
        {"checkedAt":"2026-11-10T10:06:11Z","exportQuality":{"ok":false,"issues":[
        {"code":"empty-chat-capture-unverified","threads":[{"chatTitle":"Service Chat","reason":"chat-open-failed"}]}]}}

        stderr:
        WAC_PROGRESS:{}
        """
        let report = try XCTUnwrap(ExportFailureReport.decode(retained: raw))
        let start = try XCTUnwrap(DisplayFormatters.parseDate("2026-11-10T10:02:55Z"))
        let end = try XCTUnwrap(DisplayFormatters.parseDate(report.checkedAt))
        var attempt = ExportAttempt(startedAt: start, completedAt: end, triggers: [], outcome: .failed)
        XCTAssertEqual(report.chats.map(\.title), ["Service Chat"])
        XCTAssertTrue(report.belongs(to: attempt))
        attempt.outcome = .succeeded
        XCTAssertFalse(report.belongs(to: attempt))
        attempt.outcome = .failed
        attempt.startedAt = end.addingTimeInterval(10)
        attempt.completedAt = end.addingTimeInterval(20)
        XCTAssertFalse(report.belongs(to: attempt))
    }

    func testSourceDifferenceIsInfoOnlyWhenNewerContentWasExported() throws {
        let data = Data("""
        {"chatTitle":"Example","labelsRaw":["Follow Up"],"requiresResponse":true,
         "sourceDiagnostics":{"issues":[{"code":"opened-chat-newer-than-indexeddb","openedChatLatestAt":"2026-11-10T10:00:00Z"}]},
         "messages":[{"timestamp":"2026-11-10T10:00:00Z","text":"Captured"}]}
        """.utf8)
        var thread = try JSONDecoder().decode(ExportThread.self, from: data)
        XCTAssertTrue(thread.sourceWarnings.isEmpty)
        XCTAssertEqual(thread.followUpReason, "Follow Up")
        thread.messages = []
        XCTAssertEqual(thread.sourceWarnings.count, 1)
    }

    func testConfirmedEmptyIsNotWarningAndLockedChatHasName() throws {
        let data = Data("""
        {"threads":[{"chatTitle":"Empty","sourceDiagnostics":{"captureState":{"confirmedEmpty":true},
          "issues":[{"code":"confirmed-empty-live-chat"}]},"messages":[]}],
         "exportDiagnostics":{"skippedThreads":[{"chatTitle":"Locked Group","reason":"locked-chat"}]}}
        """.utf8)
        let export = try JSONDecoder().decode(WhatsAppExport.self, from: data)
        XCTAssertTrue(export.threads[0].confirmedEmpty)
        XCTAssertTrue(export.threads[0].sourceWarnings.isEmpty)
        XCTAssertEqual(export.exportDiagnostics?.skippedThreads?.first?.title, "Locked Group")
        XCTAssertEqual(export.exportDiagnostics?.skippedThreads?.first?.explanation, "Locked in WhatsApp; not collected.")
    }

    func testRecoveredTextIsInformationalButMissingTextIsNot() throws {
        var thread = try JSONDecoder().decode(ExportThread.self, from: Data("""
        {"sourceDiagnostics":{"issues":[{"code":"matching-message-text-availability-conflict","messageId":"m1"}]},
         "messages":[{"messageId":"m1","text":"Recovered text"}]}
        """.utf8))
        XCTAssertTrue(thread.sourceWarnings.isEmpty)
        thread.messages = []
        XCTAssertEqual(thread.sourceWarnings.count, 1)
    }

    func testContactPreviewHidesEncodedPhotoWithoutChangingExport() throws {
        let message = try JSONDecoder().decode(ExportMessage.self, from: Data("""
        {"text":"BEGIN:VCARD\\nVERSION:3.0\\nFN:Example Contact\\nN:Contact;Example;;;\\nTEL:+15555550123\\nEND:VCARD"}
        """.utf8))
        XCTAssertTrue(message.previewText.contains("Example"))
        XCTAssertFalse(message.previewText.contains("BEGIN:VCARD"))
        XCTAssertTrue(message.text?.contains("BEGIN:VCARD") == true)
    }
}
