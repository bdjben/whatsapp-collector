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
                    detail: "Open the dedicated WhatsApp Web profile, then load labels. The native app reads WhatsApp’s IndexedDB label store directly.",
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
                                clearAction: { store.clearLabelDecision(label) }
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
                subtitle: "Choose labels that should always be collected, and labels that should be skipped when they are the only label.",
                systemImage: "tag"
            )

            StatusBanner(busyState: store.busyState, error: store.lastError)

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

                Label("\(store.configuration.allowLabels.count) allow", systemImage: "checkmark.circle")
                    .foregroundStyle(.green)
                Label("\(store.configuration.excludeLabels.count) exclude", systemImage: "minus.circle")
                    .foregroundStyle(.orange)
            }

            HStack(spacing: 8) {
                TextField("Add label manually", text: $manualLabel)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 280)
                Button("Allow") {
                    store.setAllow(manualLabel)
                    manualLabel = ""
                }
                .disabled(manualLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button("Exclude") {
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
            return .allow
        }
        if store.configuration.excludeLabels.contains(where: { $0.caseInsensitiveCompare(label) == .orderedSame }) {
            return .exclude
        }
        return .ignore
    }
}
