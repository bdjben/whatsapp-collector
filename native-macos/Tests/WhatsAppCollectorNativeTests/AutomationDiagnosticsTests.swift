import Foundation
import Testing
@testable import WhatsAppCollectorNative

@Test @MainActor
func diagnosticsUsesNativeScheduleAndLabelsTheLegacyResponse() throws {
    let suiteName = "studio.bdjben.whatsapp-collector.tests.\(UUID().uuidString)"
    let defaults = try #require(UserDefaults(suiteName: suiteName))
    defer { defaults.removePersistentDomain(forName: suiteName) }
    var record = AutomationScheduleRecord(intervalMinutes: 180)
    record.setEnabled(true, now: Date(timeIntervalSince1970: 1_800_000_000))
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    defaults.set(try encoder.encode(record), forKey: AppPreferences.automationScheduleRecord)
    defaults.set("A retained earlier failure", forKey: "lastExportFailureDiagnostics")
    let store = CollectorStore(defaults: defaults)
    store.diagnostics = #"{"schedule":{"enabled":false,"intervalMinutes":15}}"#
    let nativeData = try #require(store.automationDiagnostics.formattedJSON.data(using: .utf8))
    let native = try #require(JSONSerialization.jsonObject(with: nativeData) as? [String: Any])
    let schedule = try #require(native["schedule"] as? [String: Any])

    #expect(native["schedulerImplementation"] as? String == "native-app")
    #expect(schedule["enabled"] as? Bool == true)
    #expect(schedule["intervalMinutes"] as? Int == 180)
    #expect(store.displayedDiagnostics.hasPrefix("NATIVE AUTOMATION (current scheduler)"))
    #expect(store.displayedDiagnostics.contains("retired LaunchAgent"))
    #expect(store.displayedDiagnostics.contains("A retained earlier failure"))
}
