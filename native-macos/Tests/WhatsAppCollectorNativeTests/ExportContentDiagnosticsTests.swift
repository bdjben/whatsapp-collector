import Foundation
import XCTest
@testable import WhatsAppCollectorNative

final class ExportContentDiagnosticsTests: XCTestCase {
    func testPreservesSkippedChatAndAttachmentDiagnosticsWithoutMessageBodies() throws {
        let data = Data("""
        {"exportedAt":"2026-10-03T17:54:30Z","exportQuality":{"ok":true},
         "exportDiagnostics":{"skippedThreads":[{"chatTitle":"Example","reason":"locked-chat"}]},
         "attachmentSummary":{"notDownloaded":8},"threads":[{"messages":[{"text":"Private message"}]}]}
        """.utf8)
        let formatted = ExportContentDiagnostics.formattedJSON(from: data)
        XCTAssertTrue(formatted.contains("locked-chat"))
        XCTAssertTrue(formatted.contains("notDownloaded"))
        XCTAssertFalse(formatted.contains("Private message"))
        let decoded = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(formatted.utf8)) as? [String: Any])
        XCTAssertNil(decoded["threads"])
    }

    func testOlderExportWithoutNewDiagnosticsStillFormats() {
        let formatted = ExportContentDiagnostics.formattedJSON(from: Data("{\"exportedAt\":\"older\",\"threads\":[]}".utf8))
        XCTAssertTrue(formatted.contains("older"))
    }

    func testExpiryAndIdentityEvidenceSurvivesRestartFormatting() throws {
        let data = Data("""
        {"exportQuality":{"metrics":{"confirmedEmptyThreadCount":2,"expectedExpiredMessageCount":14,
         "unexplainedMissingMessageCount":1}},"exportDiagnostics":{"historyComparison":{
         "expectedExpirations":[{"classification":"expected-expiration"}]},
         "identityConsolidation":[{"policy":"message-id-union"}]},
         "threads":[{"messages":[{"text":"Not diagnostic content"}]}]}
        """.utf8)
        let formatted = ExportContentDiagnostics.formattedJSON(from: data)
        let decoded = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(formatted.utf8)) as? [String: Any])
        let summary = try XCTUnwrap(decoded["historySummary"] as? [String: Int])
        XCTAssertEqual(summary["expectedExpiredMessages"], 14)
        XCTAssertEqual(summary["confirmedEmptyChats"], 2)
        XCTAssertEqual(summary["missingMessagesWithoutExpiryExplanation"], 1)
        XCTAssertTrue(formatted.contains("message-id-union"))
        XCTAssertFalse(formatted.contains("Not diagnostic content"))
    }

    func testMalformedExportDoesNotProduceDiagnosticText() {
        XCTAssertEqual(ExportContentDiagnostics.formattedJSON(from: Data("not json".utf8)), "")
    }
}
