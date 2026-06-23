import SwiftUI

struct HelpView: View {
    @EnvironmentObject private var store: CollectorStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(
                    title: "Help",
                    subtitle: "Set up the dedicated WhatsApp Web profile, keep exports fresh, and hand the JSON to local AI agents.",
                    systemImage: "questionmark.circle"
                )

                GroupBox("Typical Workflow") {
                    VStack(alignment: .leading, spacing: 12) {
                        HelpStep(number: 1, title: "Launch / Login", detail: "Open the dedicated Chrome profile and confirm WhatsApp Web or WhatsApp Business Web is logged in.")
                        HelpStep(number: 2, title: "Load Labels", detail: "Read the current WhatsApp label inventory, then mark labels as Ignore, Allow, or Exclude.")
                        HelpStep(number: 3, title: "Run Export", detail: "Refresh the same stable JSON file used by local agents and automations.")
                        HelpStep(number: 4, title: "Preview or Reveal", detail: "Review threads inside the app, copy one thread as JSON, or reveal the full export file in Finder.")
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Label Rules") {
                    VStack(alignment: .leading, spacing: 8) {
                        HelpDefinition(term: "Ignore", detail: "Use the normal collector behavior for this label.")
                        HelpDefinition(term: "Allow", detail: "Always collect chats with this label.")
                        HelpDefinition(term: "Exclude", detail: "Skip chats when this is their only label.")
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Automation") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Automatic exports use a macOS LaunchAgent. Native schedules call the bundled bridge directly, so the localhost web UI does not need to be running.")
                            .foregroundStyle(.secondary)
                        Button {
                            Task { await store.installSchedule() }
                        } label: {
                            Label("Start Automatic Exports", systemImage: "play.circle")
                        }
                        .disabled(store.isBusy)
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Older App Cleanup") {
                    VStack(alignment: .leading, spacing: 10) {
                        if let candidate = store.legacyAppCandidate {
                            Text("Older app found at \(candidate.displayPath)")
                                .textSelection(.enabled)
                            Text("The app can back up exports from \(DisplayFormatters.shortPath(LegacyAppMigration.defaultExportsURL.path)) and move the older wrapper app to Trash.")
                                .foregroundStyle(.secondary)
                        } else {
                            Text("No older menu-bar/web UI app is currently detected in /Applications.")
                                .foregroundStyle(.secondary)
                        }
                        if let summary = store.legacyCleanupSummary {
                            Text(summary)
                                .font(.callout)
                                .textSelection(.enabled)
                                .foregroundStyle(.secondary)
                        }
                        HStack {
                            Button {
                                store.refreshLegacyAppCandidate()
                            } label: {
                                Label("Check Again", systemImage: "arrow.clockwise")
                            }
                            Button {
                                store.promptForLegacyCleanup()
                            } label: {
                                Label("Back Up and Remove Older App", systemImage: "trash")
                            }
                            .disabled(store.legacyAppCandidate == nil)
                        }
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Files for AI Agents") {
                    VStack(alignment: .leading, spacing: 10) {
                        PathRow(
                            title: "Export JSON",
                            path: $store.configuration.outputPath,
                            systemImage: "doc.text",
                            actionTitle: "Copy",
                            action: store.copyOutputPath
                        )
                        Button {
                            store.copyPrompt()
                        } label: {
                            Label("Copy AI Prompt", systemImage: "doc.on.doc")
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            .padding(22)
        }
    }
}

private struct HelpStep: View {
    var number: Int
    var title: String
    var detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text("\(number)")
                .font(.caption.weight(.bold))
                .foregroundStyle(.white)
                .frame(width: 22, height: 22)
                .background(Color.accentColor, in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.headline)
                Text(detail)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct HelpDefinition: View {
    var term: String
    var detail: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(term)
                .font(.headline)
                .frame(width: 72, alignment: .leading)
            Text(detail)
                .foregroundStyle(.secondary)
        }
    }
}
