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
        let structured = (threads ?? []).map { chat in
            var problem = chat
            problem.reason = problem.reason ?? code
            return problem
        }
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
    var detail: String?
    var messageProblems: [ExportMessageProblem]?
    var id: String { threadKey ?? chatTitle ?? "unknown" }
    var title: String { chatTitle ?? threadKey ?? "Unknown chat" }

    var explanation: String {
        if let detail, !detail.isEmpty { return detail }
        if let explanation = sourceDiagnostics?.captureState?.failureExplanation { return explanation }
        switch reason {
        case "locked-chat": return "Locked in WhatsApp; not collected."
        case "chat-open-failed": return "Could not open and verify this conversation."
        case "attachment-storage-problem": return "An attachment storage problem stopped the attempted export."
        default: return "Conversation content could not be verified."
        }
    }

    var affectedMessages: [ExportMessageProblem] {
        messageProblems ?? sourceDiagnostics?.captureState?.missingRequiredMessages ?? []
    }

    var technicalDetails: String {
        ([reason].compactMap { $0 }
         + (sourceDiagnostics?.captureState?.incompleteReasons ?? [])
         + affectedMessages.compactMap { problem in
             problem.messageId.map { "Message \($0): \(problem.downloadFailure ?? problem.reason ?? "not captured")" }
         }
         + (sourceDiagnostics?.issues ?? []).compactMap(\.detail)).joined(separator: "\n")
    }
}

struct ExportMessageProblem: Codable, Identifiable, Sendable {
    var messageId: String?
    var messageType: String?
    var timestamp: String?
    var parentMessageId: String?
    var fileName: String?
    var sizeBytes: Int64?
    var reason: String?
    var downloadFailure: String?
    var id: String { messageId ?? [timestamp, fileName, messageType].compactMap { $0 }.joined(separator: ":") }

    var summary: String {
        let kind: String
        switch messageType {
        case "image": kind = "Photo"
        case "video": kind = "Video"
        case "ptt", "audio": kind = "Audio"
        case "document": kind = "Document"
        case "sticker": kind = "Sticker"
        default: kind = "Message"
        }
        var parts = [parentMessageId == nil ? kind : "\(kind) in a media group"]
        if let timestamp { parts.append(DisplayFormatters.date(timestamp)) }
        if let fileName, !fileName.hasPrefix("attachment-") { parts.append(fileName) }
        if let sizeBytes { parts.append(ByteCountFormatter.string(fromByteCount: sizeBytes, countStyle: .file)) }
        parts.append(reason == "attachment-not-downloaded" ? "Download failed" : "Not captured")
        return parts.joined(separator: " - ")
    }
}

struct SavedExportDiagnostics: Decodable, Sendable {
    var skippedThreads: [ExportChatProblem]?
    var attachmentCleanup: AttachmentCleanupSummary?
}

struct AttachmentCleanupSummary: Decodable, Sendable {
    var deletedFileCount: Int?
    var deletedBytes: Int?
}

struct AttachmentStorageProblem: Decodable, Sendable {
    var category: String?
    var detail: String?
    var threadKey: String?
    var chatTitle: String?
    var messageId: String?
    var attachmentId: String?
}

struct ExportFailureReport: Decodable, Sendable {
    var checkedAt: String?
    var error: String?
    var loginRequired: Bool?
    var exportQuality: ExportQualitySummary?
    var rejectedCandidatePath: String?
    var storageProblem: AttachmentStorageProblem?

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
        let storageChats = storageProblem?.threadKey.map {
            [ExportChatProblem(threadKey: $0, chatTitle: storageProblem?.chatTitle, reason: "attachment-storage-problem")]
        } ?? []
        return ((exportQuality?.issues ?? []).flatMap(\.affectedChats) + storageChats)
            .filter { seen.insert($0.id).inserted }
    }

    var summary: String {
        if storageProblem != nil { return error ?? "Export failed because of an attachment storage problem." }
        if chats.count == 1, let chat = chats.first { return "Export failed: \(chat.title). \(chat.explanation)" }
        return chats.isEmpty ? (error ?? "Export failed.") : "Export failed: " + chats.map(\.title).joined(separator: ", ")
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
    var activeLoading: Bool?
    var contextLoaded: Bool?
    var waitTimedOut: Bool?
    var incompleteReasons: [String]?
    var missingRequiredMessageCount: Int?
    var missingRequiredMessages: [ExportMessageProblem]?

    var failureExplanation: String? {
        var parts = [String]()
        if let count = missingRequiredMessageCount, count > 0 {
            parts.append("\(count) recent \(count == 1 ? "message" : "messages") could not be captured.")
            if activeLoading == false && contextLoaded == true {
                parts.append("WhatsApp had finished loading, but the collector could not read all required content.")
            }
        }
        if activeLoading == true { parts.append("WhatsApp was still loading the conversation.") }
        if waitTimedOut == true { parts.append("The capture deadline was reached before the conversation could be verified.") }
        if incompleteReasons?.contains("disconnected") == true { parts.append("WhatsApp lost its connection during capture.") }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }
}
