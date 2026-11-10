import Foundation
import XCTest
@testable import WhatsAppCollectorNative

final class AttachmentLimitTests: XCTestCase {
    func testStorageFailureAndBlockedAttachmentPresentation() throws {
        let report = try XCTUnwrap(ExportFailureReport.decode(retained: #"{"error":"Attachment storage problem: incoming size mismatch","storageProblem":{"category":"incoming-message-attachment","detail":"Size mismatch","threadKey":"chat","chatTitle":"Example","messageId":"message"}}"#))
        XCTAssertEqual(report.storageProblem?.category, "incoming-message-attachment")
        XCTAssertEqual(report.chats.first?.title, "Example")
        XCTAssertTrue(report.summary.contains("storage problem"))
        let attachment = try JSONDecoder().decode(ExportAttachment.self, from: Data(#"{"status":"notAllowedToDownload","note":"Current-export attachments protected."}"#.utf8))
        XCTAssertEqual(attachment.statusTitle, "Not allowed to download")
        XCTAssertEqual(attachment.note, "Current-export attachments protected.")
    }

    func testFirstInstallDefaultsAndBridgePayload() throws {
        let config = CollectorConfiguration.defaults
        XCTAssertEqual(config.attachmentFileLimitMB, 20)
        XCTAssertEqual(config.attachmentChatLimitMB, 1_000)
        XCTAssertEqual(config.attachmentStorageLimitGB, 5)
        let data = try JSONEncoder().encode(BridgeRequest(configuration: config))
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(payload["attachmentFileLimitBytes"] as? Int64, 20_000_000)
        XCTAssertEqual(payload["attachmentChatLimitBytes"] as? Int64, 1_000_000_000)
        XCTAssertEqual(payload["attachmentStorageLimitBytes"] as? Int64, 5_000_000_000)
    }

    func testNumericLimitsRejectOutOfRangeAndNonfiniteValues() {
        var config = CollectorConfiguration.defaults
        config.attachmentStorageLimitGB = 0.1
        XCTAssertEqual(config.attachmentStorageLimitGB, 2)
        config.attachmentStorageLimitGB = 100
        XCTAssertEqual(config.attachmentStorageLimitGB, 25)
        config.attachmentStorageLimitGB = 5
        config.attachmentStorageLimitGB = .nan
        config.attachmentStorageLimitGB = .infinity
        XCTAssertEqual(config.attachmentStorageLimitGB, 5)
        config.attachmentFileLimitMB = -1
        config.attachmentChatLimitMB = Double.greatestFiniteMagnitude
        XCTAssertEqual(config.attachmentFileLimitMB, 10)
        XCTAssertEqual(config.attachmentChatLimitMB, 5_000)
        config.attachmentFileLimitMB = 2_000
        config.attachmentChatLimitMB = 1
        XCTAssertEqual(config.attachmentFileLimitMB, 1_000)
        XCTAssertEqual(config.attachmentChatLimitMB, 10)
    }

    func testOlderConfigurationKeepsSavedTotalAndGetsNewDefaults() throws {
        let old = Data(#"{"attachmentStorageLimitBytes":5000000000,"downloadAttachments":false}"#.utf8)
        let config = try JSONDecoder().decode(CollectorConfiguration.self, from: old)
        XCTAssertEqual(config.attachmentStorageLimitGB, 5)
        XCTAssertEqual(config.attachmentFileLimitMB, 20)
        XCTAssertEqual(config.attachmentChatLimitMB, 1_000)
        XCTAssertFalse(config.downloadAttachments)
        XCTAssertEqual(try JSONDecoder().decode(CollectorConfiguration.self, from: JSONEncoder().encode(config)), config)
    }

    func testSchedulePayloadRetainsEachLimitAndOlderPayloadPreservesChoices() throws {
        let data = Data(#"{"attachmentFileLimitBytes":250000000,"attachmentChatLimitBytes":800000000,"attachmentStorageLimitBytes":5000000000}"#.utf8)
        let payload = try JSONDecoder().decode(SchedulePayload.self, from: data)
        let config = CollectorConfiguration.defaults.withSchedulePayload(payload)
        XCTAssertEqual(config.attachmentFileLimitMB, 250)
        XCTAssertEqual(config.attachmentChatLimitMB, 800)
        XCTAssertEqual(config.attachmentStorageLimitGB, 5)
        let old = try JSONDecoder().decode(SchedulePayload.self, from: Data("{}".utf8))
        XCTAssertEqual(config.withSchedulePayload(old), config)
    }

    @MainActor
    func testSavedPreferencesSurviveRelaunchAndMissingKeysUseDefaults() async throws {
        let suite = "WhatsAppCollector.AttachmentLimitTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        XCTAssertEqual(CollectorStore(defaults: defaults).configuration.attachmentStorageLimitGB, 5)
        defaults.set(5_000_000_000 as Int64, forKey: "attachmentStorageLimitBytes")
        let store = CollectorStore(defaults: defaults)
        XCTAssertEqual(store.configuration.attachmentStorageLimitGB, 5)
        XCTAssertEqual(store.configuration.attachmentFileLimitMB, 20)
        XCTAssertEqual(store.configuration.attachmentChatLimitMB, 1_000)
        store.configuration.attachmentFileLimitMB = 250
        store.configuration.attachmentChatLimitMB = 800
        store.saveConfiguration()
        let reloaded = CollectorStore(defaults: defaults)
        XCTAssertEqual(reloaded.configuration.attachmentFileLimitMB, 250)
        XCTAssertEqual(reloaded.configuration.attachmentChatLimitMB, 800)
        XCTAssertEqual(reloaded.configuration.attachmentStorageLimitGB, 5)
    }

    func testBridgeClampsRawValuesAndDoesNotChangeIndependentLimits() {
        var config = CollectorConfiguration.defaults
        config.attachmentStorageLimitBytes = Int64.max
        config.attachmentFileLimitBytes = 200_000_000
        config.attachmentChatLimitBytes = 50_000_000
        let request = BridgeRequest(configuration: config)
        XCTAssertEqual(request.attachmentStorageLimitBytes, 25_000_000_000)
        XCTAssertEqual(request.attachmentFileLimitBytes, 200_000_000)
        XCTAssertEqual(request.attachmentChatLimitBytes, 50_000_000)
    }
}
