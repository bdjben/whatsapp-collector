import SwiftUI

struct HelpView: View {
    @EnvironmentObject private var store: CollectorStore
    @Environment(\.appActions) private var appActions

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(
                    title: "Help",
                    subtitle: "Set up the dedicated WhatsApp Web profile, keep exports fresh, and hand the JSON to local AI agents.",
                    systemImage: "questionmark.circle"
                )

                GroupBox("App Support") {
                    HStack(spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(AppMetadata.displayVersion)
                                .font(.headline)
                            Text("The Help menu opens this section, and GitHub has the public release page, downloads, and issue history.")
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button {
                            appActions.checkForUpdates()
                        } label: {
                            Label("Check for Updates...", systemImage: "arrow.triangle.2.circlepath")
                        }
                        Button {
                            appActions.openRepository()
                        } label: {
                            Label("GitHub", systemImage: "safari")
                        }
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Typical Workflow") {
                    VStack(alignment: .leading, spacing: 12) {
                        HelpStep(number: 1, title: "Launch / Login", detail: "Open the dedicated Chrome profile and confirm WhatsApp Web or WhatsApp Business Web is logged in.")
                        HelpStep(number: 2, title: "Load Labels", detail: "Read the current WhatsApp label inventory, then mark labels as Standard, Always Include, or Never Include.")
                        HelpStep(number: 3, title: "Run Export", detail: "Refresh the same stable JSON file used by local agents and automations.")
                        HelpStep(number: 4, title: "Preview or Reveal", detail: "Review threads inside the app, copy one thread as JSON, or reveal the full export file in Finder.")
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Label Rules") {
                    VStack(alignment: .leading, spacing: 8) {
                        HelpDefinition(term: "Standard", detail: "Do not force or block chats with this label. Standard chats are included only when they match the normal export rules, especially the Recent chats from All setting.")
                        HelpDefinition(term: "Always Include", detail: "Force chats with this label into the export even when they are outside the recent-chat window.")
                        HelpDefinition(term: "Never Include", detail: "Skip a chat only when every label on it is a Never Include label.")
                        HelpDefinition(term: "Groups", detail: "Use the Groups setting on the dashboard to keep group chats out unless the group has an Always Include label.")
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Browser Requirements") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("WhatsApp Collector needs Google Chrome installed and uses a dedicated Chrome profile, separate from your normal browsing profile. Click Launch / Login, let that Chrome window open, and keep WhatsApp Web or WhatsApp Business Web logged in there.")
                        Text("Do not close the dedicated Chrome window while exporting. If Chrome asks for a QR login, scan it first, then return to the app and run the export. The app opens Chrome with its own DevTools connection, so you do not need to turn on Chrome developer settings. The app only reads the logged-in browser data needed for the export; it does not send messages or open the old browser-based app UI.")
                            .foregroundStyle(.secondary)
                    }
                    .font(.callout)
                    .textSelection(.enabled)
                    .padding(.vertical, 4)
                }

                GroupBox("Automation") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Use the Automation tab to start or stop recurring exports. When automatic exports are on, macOS runs WhatsApp Collector on the interval you choose while you are logged in, refreshes the same JSON file, and records the last successful run in the app.")
                            .foregroundStyle(.secondary)
                        Text("Turn on Launch at Login there if you want the app itself to open automatically after you sign in. You can turn either option off from the same Automation tab.")
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
                            Text(candidate.hasAppBundle ? "Older app found at \(candidate.displayPath)" : "Existing export content found at \(candidate.displayPath)")
                                .textSelection(.enabled)
                            Text(candidate.hasAppBundle ? "The app can back up exports from \(DisplayFormatters.shortPath(LegacyAppMigration.defaultExportsURL.path)) and move the older wrapper app to Trash." : "The app can back up existing exports from \(DisplayFormatters.shortPath(LegacyAppMigration.defaultExportsURL.path)) without removing any app.")
                                .foregroundStyle(.secondary)
                        } else {
                            Text("No older menu-bar/web UI app or legacy export folder is currently detected.")
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
                                Label(store.legacyAppCandidate?.hasAppBundle == true ? "Back Up and Remove Older App" : "Back Up Existing Exports", systemImage: "tray.and.arrow.down")
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
                            appActions.showAIPrompt()
                        } label: {
                            Label("View/Copy AI Prompt", systemImage: "doc.on.doc")
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
                .frame(width: 124, alignment: .leading)
            Text(detail)
                .foregroundStyle(.secondary)
        }
    }
}
