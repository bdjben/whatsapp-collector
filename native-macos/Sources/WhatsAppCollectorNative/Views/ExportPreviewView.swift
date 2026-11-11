import SwiftUI

struct ExportPreviewView: View {
    @EnvironmentObject private var store: CollectorStore
    @Environment(\.appActions) private var appActions
    @State private var searchText = ""
    @State private var warningDetailsExpanded = false
    @State private var showSourceDetails = false

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(22)
                .padding(.bottom, 12)
            Divider()

            attemptStatus
                .padding(.horizontal, 22)
                .padding(.vertical, 10)
            Divider()

            if let export = store.export {
                VStack(spacing: 0) {
                    if (export.exportWarnings?.isEmpty == false) {
                        warningPanel(export)
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
                subtitle: "Saved export: \(DisplayFormatters.date(store.export?.exportedAt))",
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
                Text("\(store.export?.threads.count ?? 0) \(store.export?.threads.count == 1 ? "thread" : "threads")")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var attemptStatus: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let attempt = store.latestExportAttempt {
                let failed = attempt.outcome == .failed || attempt.outcome == .interrupted
                HStack(alignment: .top) {
                    Label("Latest attempt: \(attempt.outcome.rawValue.capitalized)",
                          systemImage: failed ? "exclamationmark.triangle" : attempt.outcome == .running ? "arrow.triangle.2.circlepath" : "checkmark.circle")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(failed ? Color.red : Color.primary)
                    Text(DisplayFormatters.date(DisplayFormatters.isoString(from: attempt.completedAt ?? attempt.startedAt)))
                        .font(.callout).foregroundStyle(.secondary)
                    if let completed = attempt.completedAt {
                        let seconds = max(0, Int(completed.timeIntervalSince(attempt.startedAt)))
                        Text("\(seconds / 60)m \(seconds % 60)s").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if failed {
                        if store.latestExportFailure?.loginRequired == true {
                            Button { Task { await store.launchLogin() } } label: {
                                Label("Show WhatsApp", systemImage: "arrow.up.forward.app")
                            }.disabled(store.isBusy)
                        }
                        Button { Task { await store.runExport() } } label: {
                            Label("Retry Export", systemImage: "arrow.clockwise")
                        }.disabled(store.isBusy)
                    }
                }
                if failed {
                    Text("Saved export was not replaced.").font(.callout)
                    if let storage = store.latestExportFailure?.storageProblem {
                        Label("Attachment storage problem", systemImage: "externaldrive.badge.exclamationmark")
                            .font(.callout.weight(.semibold)).foregroundStyle(.red)
                        Text(storage.detail ?? "The attempted export could not safely retain its attachments.")
                            .font(.caption).textSelection(.enabled)
                    }
                    if let report = store.latestExportFailure, !report.chats.isEmpty {
                        ScrollView {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(report.chats) { chat in
                                    VStack(alignment: .leading, spacing: 3) {
                                        Button {
                                            if let match = store.export?.threads.first(where: { $0.id == chat.id || $0.title == chat.title }) {
                                                store.selectedThreadID = match.id
                                            }
                                        } label: {
                                            Label(chat.title, systemImage: "bubble.left.and.exclamationmark.bubble.right")
                                        }
                                        .buttonStyle(.link)
                                        .help("Select this chat in the saved export, when available")
                                        Text(chat.explanation).font(.callout)
                                        ForEach(chat.affectedMessages) { message in
                                            Text(message.summary).font(.caption).textSelection(.enabled)
                                        }
                                        if !chat.technicalDetails.isEmpty {
                                            DisclosureGroup("Technical details") {
                                                Text(chat.technicalDetails).font(.caption).textSelection(.enabled)
                                            }
                                        }
                                    }
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }.frame(maxHeight: report.chats.count > 1 ? 220 : 160)
                    } else {
                        Text(attempt.failureMessage ?? "The attempt was interrupted.")
                            .font(.caption).textSelection(.enabled)
                    }
                }
                if attempt.outcome == .running, let progress = store.displayedExportProgress {
                    Text(progress.summary).font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Text("No recorded export attempt").font(.callout).foregroundStyle(.secondary)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func warningPanel(_ export: WhatsAppExport) -> some View {
        let skipped = export.exportDiagnostics?.skippedThreads ?? []
        let missingMedia = export.attachmentSummary?.notDownloaded ?? 0
        return DisclosureGroup(isExpanded: $warningDetailsExpanded) {
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(skipped) { chat in
                        Label("\(chat.title): \(chat.explanation)", systemImage: chat.reason == "locked-chat" ? "lock" : "exclamationmark.triangle")
                            .font(.callout)
                    }
                    if missingMedia > 0 {
                        ForEach(export.threads.filter { thread in
                            thread.previewMessages.contains { ($0.attachments ?? []).contains { $0.status != "downloaded" } }
                        }) { thread in
                            Button {
                                store.selectedThreadID = thread.id
                            } label: {
                                Label("\(thread.title): attachment downloads incomplete", systemImage: "paperclip")
                            }.buttonStyle(.link)
                        }
                    }
                    if skipped.isEmpty && missingMedia == 0 {
                        Text((export.exportWarnings ?? []).joined(separator: "\n")).font(.caption)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
            }.frame(maxHeight: 120)
        } label: {
            Text("Saved export notes: \(skipped.count) \(skipped.count == 1 ? "chat" : "chats") not collected, \(missingMedia) \(missingMedia == 1 ? "attachment" : "attachments") not downloaded")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    private func threadList(_ threads: [ExportThread]) -> some View {
        List(selection: $store.selectedThreadID) {
            ForEach(filteredThreads(threads)) { thread in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(thread.title)
                            .font(.headline)
                            .lineLimit(1)
                        if let reason = thread.followUpReason {
                            Image(systemName: "flag")
                                .foregroundStyle(.secondary)
                                .help("Follow-up: \(reason). Not an export error.")
                                .accessibilityLabel("Follow-up: \(reason)")
                        }
                        if !thread.sourceWarnings.isEmpty {
                            Image(systemName: "exclamationmark.triangle")
                                .foregroundStyle(.orange)
                                .help("Unresolved source warning in the saved export")
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
                            if let reason = thread.followUpReason {
                                Label("Follow-up: \(reason)", systemImage: "flag")
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
                        GroupBox("Collection Status") {
                            VStack(alignment: .leading, spacing: 8) {
                                if thread.confirmedEmpty {
                                    Label("Confirmed empty: the verified conversation has no exportable messages.", systemImage: "checkmark.circle")
                                        .font(.callout)
                                }
                                ForEach(thread.sourceWarnings) { issue in
                                    Label(sourceDiagnosticIssueText(issue), systemImage: "exclamationmark.triangle")
                                        .font(.caption)
                                        .foregroundStyle(.orange)
                                        .textSelection(.enabled)
                                }
                                if issues.contains(where: { thread.sourceIssueIsInformational($0) && $0.code != "confirmed-empty-live-chat" }) {
                                    Label("Recovered content included in the saved export.", systemImage: "checkmark.circle")
                                        .font(.callout).foregroundStyle(.secondary)
                                }
                                DisclosureGroup("Source details", isExpanded: $showSourceDetails) {
                                    Text(sourceDiagnosticSummary(diagnostics))
                                        .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                                    ForEach(issues.filter { thread.sourceIssueIsInformational($0) }) { issue in
                                        Text(sourceDiagnosticIssueText(issue)).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    GroupBox("Recent Messages") {
                        VStack(alignment: .leading, spacing: 10) {
                            let messages = Array(thread.previewMessages.prefix(20))
                            if messages.isEmpty {
                                Text(thread.confirmedEmpty ? "No exportable messages remain in this conversation." : "No messages were captured. See collection status for details.")
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
        return thread.confirmedEmpty ? "Confirmed empty" : "No captured messages"
    }

    private func latestMessageText(for thread: ExportThread) -> String {
        if thread.confirmedEmpty { return "Confirmed empty: no exportable messages remain." }
        if let latest = thread.previewMessages.first { return latest.previewText }
        let text = thread.lastMessageText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !text.isEmpty { return text }
        if thread.previewMessages.first?.attachments?.isEmpty == false { return "Attachment message" }
        return "No text message available for the latest row."
    }

    private func latestMessageTextIsAvailable(for thread: ExportThread) -> Bool {
        let text = thread.lastMessageText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !text.isEmpty
    }

    private func messageText(for message: ExportMessage) -> String {
        message.previewText
    }

    private func messageTextIsAvailable(for message: ExportMessage) -> Bool {
        let text = message.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !text.isEmpty && message.textAvailable != false
    }

    private func attachmentLine(_ attachment: ExportAttachment) -> some View {
        VStack(alignment: .leading, spacing: 4) {
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
            if attachment.verified == true {
                Text("verified")
                    .font(.caption)
                    .foregroundStyle(.green)
            }
            if let method = attachment.downloadMethod {
                Text(method)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
          }
          Text(attachment.statusTitle)
              .font(.caption).foregroundStyle(attachment.status == "downloaded" ? .green : .orange)
          if attachment.status != "downloaded", let note = attachment.note ?? attachment.skippedReason {
              Text(note).font(.caption).foregroundStyle(.secondary)
                  .fixedSize(horizontal: false, vertical: true)
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
            return "Message text was available from only one source."
        case "confirmed-empty-live-chat":
            return "Live conversation identity and empty history were verified."
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
