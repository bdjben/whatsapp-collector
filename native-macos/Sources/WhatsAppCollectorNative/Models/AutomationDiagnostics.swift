import Foundation

struct AutomationDiagnostics: Encodable {
    var schedulerImplementation = "native-app"
    var appVersion: String
    var schedulerActive: Bool
    var schedulerInitializedAt: Date?
    var lastSchedulerCheckAt: Date?
    var blockedReason: String?
    var launchAtLoginEnabled: Bool
    var runExportAfterLoginEnabled: Bool
    var schedule: AutomationScheduleRecord

    var formattedJSON: String {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self), let text = String(data: data, encoding: .utf8) else {
            return "Native automation diagnostics could not be encoded."
        }
        return text
    }
}
