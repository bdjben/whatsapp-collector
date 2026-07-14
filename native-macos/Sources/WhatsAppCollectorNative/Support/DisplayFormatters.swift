import Foundation

enum DisplayFormatters {
    static func parseDate(_ value: String?) -> Date? {
        guard let value else { return nil }
        return ISO8601DateFormatter().date(from: value)
    }

    static func date(_ value: String?) -> String {
        guard let date = parseDate(value) else { return "Never" }
        let absoluteFormatter = DateFormatter()
        absoluteFormatter.dateStyle = .medium
        absoluteFormatter.timeStyle = .short
        return absoluteFormatter.string(from: date)
    }

    static func relativeDate(_ value: String?) -> String {
        guard let date = parseDate(value) else { return "No export" }
        let relativeFormatter = RelativeDateTimeFormatter()
        relativeFormatter.unitsStyle = .abbreviated
        return relativeFormatter.localizedString(for: date, relativeTo: Date())
    }

    static func isoString(from date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }

    static func bytes(_ value: Int?) -> String {
        guard let value else { return "0 KB" }
        return ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
    }

    static func shortPath(_ path: String?) -> String {
        guard let path, !path.isEmpty else { return "Not set" }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        if path.hasPrefix(home) {
            return "~" + path.dropFirst(home.count)
        }
        return path
    }

    static func aiPrompt(path: String) -> String {
        let attachmentRoot = URL(fileURLWithPath: NSString(string: path).expandingTildeInPath)
            .deletingLastPathComponent()
            .appendingPathComponent("Attachments")
            .path
        return """
        My most recent WhatsApp Collector export is at:
        \(path)

        Treat this JSON as a read-only local resource and read it fresh before answering questions about my WhatsApp conversations. You need local filesystem access to the path above; if you cannot read local files, ask me to upload the JSON and any relevant attachment files. Use chatTitle, sender, timestamp, message text, and attachment content together, and identify the local WhatsApp Collector export as your source. Do not send messages or modify WhatsApp.

        Attachment workflow:
        1. Inspect the attachments array on every relevant message. An attachment belongs to its parent message; it is not a separate message.
        2. When status is downloaded, first try localPath. If that path is absent or unavailable, resolve relativePath against the directory containing the JSON file. The normal attachment root is \(attachmentRoot), but relativePath in the JSON is authoritative.
        3. Open and analyze the actual file, not only the message caption. Transcribe audio, inspect images with OCR/vision when useful, read PDFs and office documents, and inspect relevant video content or metadata. Combine findings from the file with the parent message's text and context.
        4. If integrity matters, compare the file with sizeBytes and sha256 when those fields are present. Treat verified=true as the collector's confirmation that downloaded bytes matched WhatsApp metadata.
        5. If status is notDownloaded, or neither path resolves to a readable file, say that the attachment exists but was unavailable. Include fileName, kind, skippedReason, and note when present, and never claim to have analyzed unavailable content.

        The export may also contain attachmentPolicy and attachmentSummary. Those describe download settings and outcomes; they do not turn attachment placeholders into message text or imply that unavailable media was analyzed.
        """
    }

}
