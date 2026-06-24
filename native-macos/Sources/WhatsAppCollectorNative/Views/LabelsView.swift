import SwiftUI

struct LabelsView: View {
    @EnvironmentObject private var store: CollectorStore
    @State private var searchText = ""
    @State private var manualLabel = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(22)
                .padding(.bottom, 0)

            Divider()

            if allLabels.isEmpty {
                EmptyState(
                    title: "No labels loaded",
                    detail: "Label rules are optional. Load labels only if you want Always Include or Never Include behavior; otherwise the export uses normal recent-chat rules.",
                    systemImage: "tag.slash"
                )
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(filteredLabels, id: \.self) { label in
                            LabelChip(
                                title: label,
                                role: role(for: label),
                                allowAction: { store.setAllow(label) },
                                excludeAction: { store.setExclude(label) },
                                clearAction: { store.clearLabelDecision(label) },
                                deleteAction: { store.removeLabel(label) }
                            )
                        }
                    }
                    .padding(22)
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(
                title: "Label Rules",
                subtitle: "Optional. Most users can leave labels alone; use these rules only when a WhatsApp label should always force inclusion or never include a chat.",
                systemImage: "tag"
            )

            StatusBanner(busyState: store.busyState, error: store.lastError)

            GroupBox("What Standard Means") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Label rules are optional. Standard is the default no-rule behavior: it does not force a chat into the export and does not block it. A Standard-labeled chat is included only when it qualifies through the normal export rules, especially the configured Recent chats from All window.")
                    Text("Always Include forces matching chats into the export even if they are outside the recent-chat window. Never Include skips a chat only when every label on that chat is a Never Include label.")
                        .foregroundStyle(.secondary)
                }
                .font(.callout)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 4)
            }

            HStack(spacing: 10) {
                Button {
                    Task { await store.loadLabels() }
                } label: {
                    Label("Load Labels from WhatsApp", systemImage: "arrow.down.circle")
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.isBusy)

                TextField("Search labels", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 280)

                Spacer()

                Label("\(store.configuration.allowLabels.count) always", systemImage: "checkmark.circle")
                    .foregroundStyle(.green)
                Label("\(store.configuration.excludeLabels.count) never", systemImage: "minus.circle")
                    .foregroundStyle(.orange)
            }

            HStack(spacing: 8) {
                TextField("Add label manually", text: $manualLabel)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 280)
                Button("Always Include") {
                    store.setAllow(manualLabel)
                    manualLabel = ""
                }
                .disabled(manualLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button("Never Include") {
                    store.setExclude(manualLabel)
                    manualLabel = ""
                }
                .disabled(manualLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Spacer()
            }
        }
    }

    private var allLabels: [String] {
        let labels = store.availableLabels + store.configuration.allowLabels + store.configuration.excludeLabels
        var seen = Set<String>()
        return labels.compactMap { label in
            let trimmed = label.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { return nil }
            let key = trimmed.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
            guard seen.contains(key) == false else { return nil }
            seen.insert(key)
            return trimmed
        }
        .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    private var filteredLabels: [String] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return allLabels }
        return allLabels.filter { $0.localizedCaseInsensitiveContains(query) }
    }

    private func role(for label: String) -> LabelRole {
        if store.configuration.allowLabels.contains(where: { $0.caseInsensitiveCompare(label) == .orderedSame }) {
            return .alwaysInclude
        }
        if store.configuration.excludeLabels.contains(where: { $0.caseInsensitiveCompare(label) == .orderedSame }) {
            return .neverInclude
        }
        return .standard
    }
}
