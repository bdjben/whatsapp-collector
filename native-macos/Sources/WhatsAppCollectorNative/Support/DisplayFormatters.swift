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
        """
        My most recent WhatsApp Collector export is at:
        \(path)

        It is updated regularly. Treat this JSON file as a read-only local resource when answering questions about my WhatsApp conversations. You need local filesystem access to this path; if you cannot read local files directly, ask me to upload the JSON. If you need current WhatsApp context, read this file first, use its account metadata and threads/messages as source data, and cite that the information came from the local WhatsApp Collector export. Do not send messages or modify WhatsApp from this file.
        """
    }

}
