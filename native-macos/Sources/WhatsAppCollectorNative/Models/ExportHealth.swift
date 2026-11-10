import Foundation
import Contacts

struct ExportQualitySummary: Decodable, Sendable {
    var ok: Bool?
    var issues: [ExportQualityIssue]?
}

struct ExportQualityIssue: Decodable, Sendable {
    var code: String?
    var detail: String?
    var threads: [ExportChatProblem]?
    var chatTitles: [String]?
    var threadKeys: [String]?

    var affectedChats: [ExportChatProblem] {
        let structured = threads ?? []
        let titles = (chatTitles ?? []).filter { title in !structured.contains { $0.chatTitle == title } }
        let keys = (threadKeys ?? []).filter { key in !structured.contains { $0.threadKey == key } }
        return structured + titles.map { ExportChatProblem(chatTitle: $0) }
            + keys.map { ExportChatProblem(threadKey: $0) }
    }
}

struct ExportChatProblem: Decodable, Identifiable, Sendable {
    var threadKey: String?
    var chatTitle: String?
    var reason: String?
    var sourceDiagnostics: SourceDiagnostics?
    var id: String { threadKey ?? chatTitle ?? "unknown" }
    var title: String { chatTitle ?? threadKey ?? "Unknown chat" }

    var explanation: String {
        switch reason {
        case "locked-chat": return "Locked in WhatsApp; not collected."
        case "chat-open-failed": return "Could not open and verify this conversation."
        default: return "Conversation content could not be verified."
        }
    }
}

struct SavedExportDiagnostics: Decodable, Sendable {
    var skippedThreads: [ExportChatProblem]?
}

struct ExportFailureReport: Decodable, Sendable {
    var checkedAt: String?
    var error: String?
    var exportQuality: ExportQualitySummary?
    var rejectedCandidatePath: String?

    // Read the structured bridge response, including diagnostics retained by older apps.
    static func decode(retained text: String) -> Self? {
        let response = text.components(separatedBy: "stdout:\n").last ?? text
        let json = response.components(separatedBy: "\nstderr:").first ?? response
        return try? JSONDecoder().decode(Self.self, from: Data(json.utf8))
    }

    func belongs(to attempt: ExportAttempt?) -> Bool {
        guard let attempt, attempt.outcome == .failed,
              let date = DisplayFormatters.parseDate(checkedAt) else { return false }
        return date >= attempt.startedAt.addingTimeInterval(-1)
            && date <= (attempt.completedAt ?? attempt.startedAt).addingTimeInterval(1)
    }

    var chats: [ExportChatProblem] {
        var seen = Set<String>()
        return (exportQuality?.issues ?? []).flatMap(\.affectedChats)
            .filter { seen.insert($0.id).inserted }
    }

    var summary: String {
        chats.isEmpty ? (error ?? "Export failed.") : "Export failed: " + chats.map(\.title).joined(separator: ", ")
    }
}

extension ExportThread {
    var followUpReason: String? {
        guard requiresResponse == true else { return nil }
        var reasons = [String]()
        if unread == true { reasons.append("Unread") }
        reasons += (labelsRaw ?? []).filter { ["follow up", "important"].contains($0.lowercased()) }
        return reasons.isEmpty ? "Follow-up flag" : reasons.joined(separator: ", ")
    }

    var confirmedEmpty: Bool { sourceDiagnostics?.captureState?.confirmedEmpty == true }

    func sourceIssueIsInformational(_ issue: SourceDiagnosticIssue) -> Bool {
        if issue.code == "confirmed-empty-live-chat" { return confirmedEmpty }
        if issue.code == "matching-message-text-availability-conflict", let messageID = issue.messageId {
            return (recentMessages ?? messages ?? []).contains {
                $0.messageId == messageID && $0.text?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            }
        }
        let timestamp: String?
        switch issue.code {
        case "opened-chat-newer-than-indexeddb": timestamp = issue.openedChatLatestAt
        case "indexeddb-newer-than-opened-chat": timestamp = issue.indexedDbLatestAt
        default: return false
        }
        guard let timestamp else { return false }
        return (recentMessages ?? messages ?? []).contains {
            $0.timestamp == timestamp && (($0.text?.isEmpty == false) || ($0.attachments?.isEmpty == false))
        }
    }

    var sourceWarnings: [SourceDiagnosticIssue] {
        (sourceDiagnostics?.issues ?? []).filter { !sourceIssueIsInformational($0) }
    }
}

extension ExportMessage {
    var previewText: String {
        let value = text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if value.hasPrefix("BEGIN:VCARD") {
            guard let contacts = try? CNContactVCardSerialization.contacts(with: Data(value.utf8)), !contacts.isEmpty else {
                return "Contact card"
            }
            return contacts.map { contact in
                let name = [contact.givenName, contact.familyName].filter { !$0.isEmpty }.joined(separator: " ")
                return ([name.isEmpty ? "Contact card" : "Contact: \(name)"]
                        + contact.phoneNumbers.map { $0.value.stringValue }).joined(separator: "\n")
            }.joined(separator: "\n\n")
        }
        return value.isEmpty ? (attachments?.isEmpty == false ? "Attachment message" : "Text unavailable for this message.") : value
    }
}

struct CaptureStateSummary: Codable, Sendable {
    var confirmedEmpty: Bool?
}
