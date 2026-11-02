import Foundation

enum ExportContentDiagnostics {
    static func formattedJSON(from data: Data) -> String {
        guard let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ""
        }
        let fields = ["exportedAt", "exportQuality", "exportWarnings", "exportDiagnostics", "attachmentSummary"]
        var summary = payload.filter { fields.contains($0.key) }
        if let quality = payload["exportQuality"] as? [String: Any],
           let metrics = quality["metrics"] as? [String: Any] {
            summary["historySummary"] = [
                "confirmedEmptyChats": metrics["confirmedEmptyThreadCount"] ?? 0,
                "expectedExpiredMessages": metrics["expectedExpiredMessageCount"] ?? 0,
                "missingMessagesWithoutExpiryExplanation": metrics["unexplainedMissingMessageCount"] ?? 0,
            ]
        }
        guard summary.isEmpty == false,
              let formatted = try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys]),
              let text = String(data: formatted, encoding: .utf8) else {
            return ""
        }
        return text
    }
}
