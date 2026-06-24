import Foundation

struct BridgeResponse: Decodable, Sendable {
    var ok: Bool
    var command: String?
    var checkedAt: String?
    var error: String?
    var errorType: String?
    var message: String?
    var export: ExportSummary?
    var schedule: ScheduleState?
    var labels: [String]?
    var allowLabels: [String]?
    var excludeLabels: [String]?
    var includeGroups: GroupInclusionMode?
    var window: WindowSummary?
    var threadCount: Int?
    var aiPrompt: String?
}

struct ExportSummary: Decodable, Equatable, Sendable {
    var path: String?
    var exists: Bool?
    var threadCount: Int?
    var sizeBytes: Int?
    var updatedAt: String?
    var exportedAt: String?
    var parseError: String?

    static var empty: ExportSummary {
        ExportSummary(path: nil, exists: false, threadCount: 0, sizeBytes: 0, updatedAt: nil, exportedAt: nil, parseError: nil)
    }
}

struct ScheduleState: Decodable, Equatable, Sendable {
    var enabled: Bool?
    var loaded: Bool?
    var label: String?
    var mode: String?
    var intervalMinutes: Int?
    var uiUrl: String?
    var payload: SchedulePayload?
    var bridgePath: String?
    var pythonExecutable: String?
    var resourceDir: String?
    var repoRoot: String?
    var plistPath: String?
    var scriptPath: String?
    var payloadPath: String?
    var stdoutPath: String?
    var stderrPath: String?
    var nextStep: String?
    var stdoutUpdatedAt: String?
    var stderrUpdatedAt: String?
    var lastRunAt: String?
    var lastSuccessAt: String?
    var lastFailureAt: String?
    var lastFailureMessage: String?
    var lastThreadCount: Int?
    var lastExportedAt: String?
    var lastOutputPath: String?
    var nextRunAfter: String?

    var displayState: String {
        let suffix = mode == "web" ? " (web)" : ""
        if enabled == true && loaded == true { return "On\(suffix)" }
        if enabled == true { return "Configured\(suffix)" }
        return "Off"
    }

    var isLegacyWebSchedule: Bool {
        enabled == true && mode == "web"
    }
}

struct SchedulePayload: Codable, Equatable, Sendable {
    var maxMessages: Int?
    var maxAllChats: Int?
    var accountLabel: String?
    var allowLabels: [String]?
    var excludeLabels: [String]?
    var includeGroups: GroupInclusionMode?
    var displayName: String?
    var profileDir: String?
    var outputPath: String?
    var debugPort: Int?
    var markerTitle: String?
    var markerUrlSubstring: String?
    var targetUrl: String?
}

struct WindowSummary: Decodable, Equatable, Sendable {
    var windowId: Int?
    var targetId: String?
    var requestedDisplay: String?
    var displayFallbackUsed: Bool?
    var placementMode: String?
    var settleSeconds: Double?
    var profileDir: String?
    var markerTitle: String?
    var markerUrlSubstring: String?
    var targetUrl: String?
    var debugPort: Int?
    var launched: Bool?
}

struct WhatsAppExport: Decodable, Sendable {
    var source: String?
    var exportedAt: String?
    var account: ExportAccount?
    var allowLabels: [String]?
    var excludeLabels: [String]?
    var maxRecentMessages: Int?
    var maxAllViewChats: Int?
    var includeGroups: String?
    var threads: [ExportThread]
    var exportWarnings: [String]?
}

struct ExportAccount: Decodable, Sendable {
    var platform: String?
    var accountLabel: String?
}

struct ExportThread: Codable, Identifiable, Sendable {
    var threadKey: String?
    var chatTitle: String?
    var chatType: String?
    var participants: [ExportParticipant]?
    var labelsRaw: [String]?
    var labelsNormalized: [String]?
    var unread: Bool?
    var starred: Bool?
    var requiresResponse: Bool?
    var lastMessageAt: String?
    var lastMessageDirection: String?
    var lastMessageSender: String?
    var lastMessageText: String?
    var timestampLabel: String?
    var sourceView: String?
    var recentMessages: [ExportMessage]?
    var messages: [ExportMessage]?

    var id: String { threadKey ?? chatTitle ?? "unknown-thread" }
    var title: String { chatTitle ?? threadKey ?? "Untitled" }
    var labelLine: String {
        let labels = labelsRaw ?? labelsNormalized ?? []
        return labels.isEmpty ? "No labels" : labels.joined(separator: ", ")
    }
    var messageCount: Int { recentMessages?.count ?? messages?.count ?? 0 }

    var previewMessages: [ExportMessage] {
        let captured = recentMessages ?? messages ?? []
        if captured.isEmpty,
           let lastMessageText,
           lastMessageText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false {
            return [
                ExportMessage(
                    messageId: "\(id):latest",
                    timestamp: lastMessageAt,
                    direction: lastMessageDirection,
                    sender: lastMessageSender,
                    text: lastMessageText,
                    textAvailable: true,
                    messageType: "chat",
                    subtype: nil
                )
            ]
        }
        return captured.sortedByRecency()
    }

    var displayMessageCount: Int {
        let count = previewMessages.count
        if count > 0 { return count }
        return messageCount
    }
}

struct ExportParticipant: Codable, Sendable {
    var name: String?
    var phone: String?
}

struct ExportMessage: Codable, Identifiable, Sendable {
    var messageId: String?
    var timestamp: String?
    var direction: String?
    var sender: String?
    var text: String?
    var textAvailable: Bool?
    var messageType: String?
    var subtype: String?

    var id: String { messageId ?? timestamp ?? text ?? "unknown-message" }
}

extension Array where Element == ExportThread {
    func sortedByRecency() -> [ExportThread] {
        sorted { lhs, rhs in
            let left = lhs.recencyDate
            let right = rhs.recencyDate
            if left != right {
                return left > right
            }
            return lhs.title.localizedCaseInsensitiveCompare(rhs.title) == .orderedAscending
        }
    }
}

extension Array where Element == ExportMessage {
    func sortedByRecency() -> [ExportMessage] {
        sorted { lhs, rhs in
            let left = lhs.recencyDate
            let right = rhs.recencyDate
            if left != right {
                return left > right
            }
            return lhs.id < rhs.id
        }
    }
}

extension ExportThread {
    fileprivate var recencyDate: Date {
        DisplayFormatters.parseDate(lastMessageAt) ?? .distantPast
    }
}

extension ExportMessage {
    fileprivate var recencyDate: Date {
        DisplayFormatters.parseDate(timestamp) ?? .distantPast
    }
}

enum BusyState: Equatable {
    case idle
    case refreshing
    case launching
    case exporting
    case loadingLabels
    case scheduling

    var title: String {
        switch self {
        case .idle: "Ready"
        case .refreshing: "Refreshing"
        case .launching: "Opening Chrome"
        case .exporting: "Exporting"
        case .loadingLabels: "Loading Labels"
        case .scheduling: "Scheduling"
        }
    }
}
