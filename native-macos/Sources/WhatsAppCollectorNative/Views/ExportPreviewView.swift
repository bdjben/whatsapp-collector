import SwiftUI

struct ExportPreviewView: View {
    @EnvironmentObject private var store: CollectorStore
    @Environment(\.appActions) private var appActions
    @State private var searchText = ""
    @State private var warningDetailsExpanded = false

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(22)
                .padding(.bottom, 12)
            Divider()

            if let export = store.export {
                VStack(spacing: 0) {
                    if let warnings = export.exportWarnings, warnings.isEmpty == false {
                        warningPanel(warnings)
                            .padding(.horizontal, 22)
                            .padding(.vertical, 10)
                        Divider()
                    }
                    HSplitView {
                        threadList(export.threads.sortedByRecency())
                            .frame(minWidth: 300, idealWidth: 360, maxWidth: 460)
                        threadDetail
                            .frame(minWidth: 460)
                    }
                }
            } else {
                EmptyState(
                    title: "No export preview",
                    detail: "Run an export or load the current JSON file from disk.",
                    systemImage: "doc.text.magnifyingglass"
                )
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(
                title: "Export Preview",
                subtitle: "Inspect the AI-agent-ingestible JSON without changing the export format.",
                systemImage: "doc.text.magnifyingglass"
            )

            HStack(spacing: 10) {
                TextField("Search threads", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 320)
                Button {
                    store.loadExportPreview()
                } label: {
                    Label("Reload File", systemImage: "arrow.clockwise")
                }
                Button {
                    appActions.showAIPrompt()
                } label: {
                    Label("View/Copy AI Prompt", systemImage: "doc.on.doc")
                }
                Button {
                    store.copySelectedThreadJSON()
                } label: {
                    Label("Copy Thread JSON", systemImage: "curlybraces")
                }
                .disabled(store.selectedThread == nil)
                Button {
                    store.revealOutput()
                } label: {
                    Label("Reveal JSON", systemImage: "folder")
                }
                Spacer()
                Text("\(store.export?.threads.count ?? 0) threads")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func warningPanel(_ warnings: [String]) -> some View {
        let skipped = messageSkippedCount(warnings)
        return Group {
            if skipped > 0 {
                VStack(alignment: .leading, spacing: 8) {
                    Button {
                        withAnimation(.easeInOut(duration: 0.16)) {
                            warningDetailsExpanded.toggle()
                        }
                    } label: {
                        HStack(spacing: 8) {
                            Label("Messages Skipped: \(skipped) - click for details", systemImage: "exclamationmark.triangle")
                                .font(.callout.weight(.semibold))
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption.weight(.semibold))
                                .rotationEffect(.degrees(warningDetailsExpanded ? 90 : 0))
                        }
                        .foregroundStyle(.orange)
                    }
                    .buttonStyle(.plain)

                    if warningDetailsExpanded {
                        Text("WhatsApp Collector skipped \(skipped) chat row\(skipped == 1 ? "" : "s") because it could identify the chat but could not capture any recent message text or attachment metadata for that row during this export.")
                            .font(.callout)
                        Text("This can happen when WhatsApp Web has not loaded that conversation, when rows are media-only/system/encrypted in a way the browser does not expose, or when the collector cannot safely open the chat during the current run. The export keeps the last successful file protection behavior and records this warning so downstream agents do not mistake missing rows for new messages.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        if warnings.count > 1 {
                            ScrollView {
                                Text(warningDetailsText(warnings))
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 160)
                            .padding(8)
                            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                        }
                    }
                }
            } else {
                Label {
                    Text(warnings.prefix(3).joined(separator: "  "))
                        .lineLimit(2)
                        .textSelection(.enabled)
                } icon: {
                    Image(systemName: "exclamationmark.triangle")
                }
                .font(.callout)
                .foregroundStyle(.orange)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private func threadList(_ threads: [ExportThread]) -> some View {
        List(selection: $store.selectedThreadID) {
            ForEach(filteredThreads(threads)) { thread in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(thread.title)
                            .font(.headline)
                            .lineLimit(1)
                        if thread.requiresResponse == true {
                            Image(systemName: "exclamationmark.circle.fill")
                                .foregroundStyle(.orange)
                        }
                    }
                    Text(thread.labelLine)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    HStack {
                        Text(thread.lastMessageDirection ?? "unknown")
                        Text("·")
                        Text(messageCountLabel(for: thread))
                    }
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                }
                .padding(.vertical, 4)
                .tag(thread.id)
            }
        }
        .listStyle(.inset)
    }

    @ViewBuilder
    private var threadDetail: some View {
        if let thread = store.selectedThread {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(thread.title)
                            .font(.title2.weight(.semibold))
                            .lineLimit(2)
                        HStack {
                            Label(thread.chatType ?? "unknown", systemImage: "bubble.left")
                            Label(thread.labelLine, systemImage: "tag")
                            if thread.unread == true {
                                Label("Unread", systemImage: "circle.fill")
                            }
                        }
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    }

                    GroupBox("Latest Message") {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(latestMessageText(for: thread))
                                .font(.body)
                                .textSelection(.enabled)
                                .foregroundStyle(latestMessageTextIsAvailable(for: thread) ? .primary : .secondary)
                            HStack {
                                Text(thread.lastMessageSender ?? "Unknown sender")
                                Text("·")
                                Text(DisplayFormatters.date(thread.lastMessageAt))
                                Text("·")
                                Text(thread.lastMessageDirection ?? "unknown")
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    if let diagnostics = thread.sourceDiagnostics,
                       let issues = diagnostics.issues,
                       issues.isEmpty == false {
                        GroupBox("Source Diagnostics") {
                            VStack(alignment: .leading, spacing: 8) {
                                Text(sourceDiagnosticSummary(diagnostics))
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                                ForEach(issues) { issue in
                                    Label(sourceDiagnosticIssueText(issue), systemImage: "exclamationmark.triangle")
                                        .font(.caption)
                                        .foregroundStyle(.orange)
                                        .textSelection(.enabled)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    GroupBox("Recent Messages") {
                        VStack(alignment: .leading, spacing: 10) {
                            let messages = Array(thread.previewMessages.prefix(20))
                            if messages.isEmpty {
                                Text("No text messages are available for this thread yet. Media-only, system, or encrypted rows may still appear in WhatsApp without readable text in the export.")
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                            }
                            ForEach(messages) { message in
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack {
                                        Text(message.sender ?? "Unknown")
                                            .font(.caption.weight(.semibold))
                                        Text(message.direction ?? "unknown")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                        Spacer()
                                        Text(DisplayFormatters.date(message.timestamp))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Text(messageText(for: message))
                                        .textSelection(.enabled)
                                        .foregroundStyle(messageTextIsAvailable(for: message) ? .primary : .secondary)
                                    if let attachments = message.attachments, attachments.isEmpty == false {
                                        ForEach(attachments) { attachment in
                                            attachmentLine(attachment)
                                        }
                                    }
                                }
                                Divider()
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(22)
            }
        } else {
            EmptyState(title: "Select a thread", detail: "Choose a thread to inspect its latest captured messages.", systemImage: "sidebar.left")
        }
    }

    private func filteredThreads(_ threads: [ExportThread]) -> [ExportThread] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        let sorted = threads.sortedByRecency()
        guard !query.isEmpty else { return sorted }
        return sorted.filter { thread in
            thread.title.localizedCaseInsensitiveContains(query)
                || thread.labelLine.localizedCaseInsensitiveContains(query)
                || (thread.lastMessageText ?? "").localizedCaseInsensitiveContains(query)
        }
    }

    private func messageCountLabel(for thread: ExportThread) -> String {
        let count = thread.displayMessageCount
        if count == 1 { return "1 recent message" }
        if count > 1 { return "\(count) recent messages" }
        return "No text messages"
    }

    private func latestMessageText(for thread: ExportThread) -> String {
        let text = thread.lastMessageText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !text.isEmpty { return text }
        return "No text message available for the latest row."
    }

    private func latestMessageTextIsAvailable(for thread: ExportThread) -> Bool {
        let text = thread.lastMessageText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !text.isEmpty
    }

    private func messageText(for message: ExportMessage) -> String {
        let text = message.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !text.isEmpty { return text }
        return "Text unavailable for this message."
    }

    private func messageTextIsAvailable(for message: ExportMessage) -> Bool {
        let text = message.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !text.isEmpty && message.textAvailable != false
    }

    private func messageSkippedCount(_ warnings: [String]) -> Int {
        for warning in warnings {
            guard warning.hasPrefix("message-capture-skipped:") else { continue }
            let suffix = warning.split(separator: ":", maxSplits: 1).last
            if let suffix, let count = Int(suffix) {
                return count
            }
        }
        return 0
    }

    private func warningDetailsText(_ warnings: [String]) -> String {
        warnings.joined(separator: "\n")
    }

    private func attachmentLine(_ attachment: ExportAttachment) -> some View {
        HStack(spacing: 6) {
            Image(systemName: attachmentIcon(attachment))
            Text(attachment.kind ?? "attachment")
                .font(.caption.weight(.semibold))
            if let fileName = attachment.fileName {
                Text(fileName)
                    .font(.caption)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            if let size = attachment.sizeBytes {
                Text(DisplayFormatters.bytes(size))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(attachment.status ?? "unknown")
                .font(.caption)
                .foregroundStyle(attachment.status == "downloaded" ? .green : .orange)
            if let reason = attachment.skippedReason {
                Text(reason)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .textSelection(.enabled)
    }

    private func sourceDiagnosticSummary(_ diagnostics: SourceDiagnostics) -> String {
        let indexed = diagnostics.indexedDbMessageCount ?? 0
        let opened = diagnostics.openedChatMessageCount ?? 0
        let merged = diagnostics.mergedMessageCount ?? 0
        let sources = diagnostics.sourcesUsed?.joined(separator: ", ") ?? "none"
        return "Compared IndexedDB and opened-chat sources. Sources used: \(sources). IndexedDB: \(indexed), opened chat: \(opened), merged: \(merged)."
    }

    private func sourceDiagnosticIssueText(_ issue: SourceDiagnosticIssue) -> String {
        switch issue.code {
        case "opened-chat-newer-than-indexeddb":
            return "Opened chat had a newer latest message than IndexedDB."
        case "indexeddb-newer-than-opened-chat":
            return "IndexedDB had a newer latest message than the opened chat view."
        case "matching-message-timestamp-conflict":
            return "Matching message \(issue.messageId ?? "") had conflicting timestamps."
        case "matching-message-text-availability-conflict":
            return "Matching message \(issue.messageId ?? "") had conflicting text availability."
        case "opened-chat-check-failed":
            return "Opened-chat verification failed: \(issue.detail ?? "unknown error")"
        default:
            return issue.code ?? "Unknown source diagnostic issue."
        }
    }

    private func attachmentIcon(_ attachment: ExportAttachment) -> String {
        switch attachment.kind {
        case "image": return "photo"
        case "video": return "film"
        case "audio": return "waveform"
        case "document": return "doc"
        default: return "paperclip"
        }
    }
}
