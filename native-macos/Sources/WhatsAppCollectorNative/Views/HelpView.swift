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
                        HelpStep(number: 1, title: "Launch / Login", detail: "For first-time setup, open the dedicated Chrome profile and confirm WhatsApp Web or WhatsApp Business Web is logged in.")
                        HelpStep(number: 2, title: "Optional Label Rules", detail: "Label rules are optional. Load labels only if you want some labels to Always Include chats or Never Include chats.")
                        HelpStep(number: 3, title: "Run Export", detail: "Automatically open or reuse the dedicated profile and refresh the stable JSON file used by local agents and automations.")
                        HelpStep(number: 4, title: "Preview or Reveal", detail: "Review threads inside the app, copy one thread as JSON, or reveal the full export file in Finder.")
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Label Rules") {
                    VStack(alignment: .leading, spacing: 8) {
                        HelpDefinition(term: "Standard", detail: "The default optional no-rule state. Do not force or block chats with this label. Standard chats are included only when they match the normal export rules, especially the Recent chats from All setting.")
                        HelpDefinition(term: "Always Include", detail: "Force chats with this label into the export even when they are outside the recent-chat window.")
                        HelpDefinition(term: "Never Include", detail: "Skip a chat only when every label on it is a Never Include label.")
                        HelpDefinition(term: "Groups", detail: "Use the Groups setting on the dashboard to keep group chats out unless the group has an Always Include label.")
                    }
                    .padding(.vertical, 4)
                }

                GroupBox("Browser Requirements") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("WhatsApp Collector needs Google Chrome installed and uses a dedicated Chrome profile, separate from your normal browsing profile. Use Launch / Login for first-time setup or reauthentication, and keep WhatsApp Web or WhatsApp Business Web logged in there. Run Export and automatic exports open or reuse that dedicated profile automatically.")
                        Text("Do not close the dedicated Chrome window while exporting. If Chrome asks for a QR login, scan it and run the export again. After a successful export, the app closes only the exact Chrome process it opened or reused for the configured profile and DevTools port. A failed export gets a five-minute grace period before that same process is closed while the app remains open; interrupted cleanup is retried on the next launch. Other Chrome profiles and windows are not targeted. You do not need to turn on Chrome developer settings. The app only reads the logged-in browser data needed for the export; it does not send messages or open the old browser-based app UI.")
                            .foregroundStyle(.secondary)
                    }
                    .font(.callout)
                    .textSelection(.enabled)
                    .padding(.vertical, 4)
                }

                GroupBox("Attachments") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Attachment downloads are optional. Limits for individual files, each chat, and total storage are adjustable in the Dashboard. WhatsApp Collector detects media from WhatsApp message metadata, checks WhatsApp Web's local media cache, and retries through the live message download path when verified bytes are missing.")
                        Text("Defaults are 20 MB per file, 1 GB per chat, and 5 GB total. Ranges are 10 MB-1 GB per file, 10 MB-5 GB per chat, and 2-25 GB total. Saved choices within these ranges are preserved. During collection, storage pressure automatically removes the oldest local files until usage is below 75% of the affected chat or total limit. Current-export attachments are protected. If they prevent cleanup, incoming attachments are marked Not allowed to download. WhatsApp's own messages and media are never deleted. JSON backups are kept for one year with no size limit.")
                            .foregroundStyle(.secondary)
                    }
                    .font(.callout)
                    .textSelection(.enabled)
                    .padding(.vertical, 4)
                }

                GroupBox("Automation") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Use the Automation tab to start or stop recurring exports. Recurring exports run while WhatsApp Collector remains open in the menu bar. You may close the dashboard window; choosing Quit stops future automatic exports. If the Mac sleeps through a due time, the app performs one catch-up export after wake. Reopening after an explicit Quit starts a fresh interval instead of replaying missed runs.")
                            .foregroundStyle(.secondary)
                        Text("Open at Login only starts the menu-bar app. The separate, default-off Run an Export After Login option runs once five minutes after login following startup or restart, once after you sign out and later log back into this macOS account (including after using another or Guest account), and once five minutes after wake from sleep. Recurring exports can remain off. The dashboard can stay hidden during login launches.")
                            .foregroundStyle(.secondary)
                        Button {
                            Task { await store.installSchedule() }
                        } label: {
                            Label("Start Automatic Exports", systemImage: "play.circle")
                        }
                        .disabled(store.isBusy || store.schedule?.enabled == true)
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
                            path: .constant(store.configuration.outputPath),
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
