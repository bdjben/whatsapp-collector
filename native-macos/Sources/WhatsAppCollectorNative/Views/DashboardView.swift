import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var store: CollectorStore
    @Environment(\.appActions) private var appActions

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                dashboardHeader

                StatusBanner(
                    busyState: store.busyState,
                    error: store.lastError,
                    scheduledRunActive: store.scheduledExportIsRunning,
                    scheduledRunText: store.scheduledExportStatusText
                )

                metrics
                actions
                collectionSettings
                browserReadiness
                chromeProfile
                files
            }
            .padding(22)
        }
        .safeAreaInset(edge: .bottom) {
            SaveChangesBar()
        }
    }

    private var dashboardHeader: some View {
        HStack(alignment: .top, spacing: 16) {
            SectionHeader(
                title: "WhatsApp Collector",
                subtitle: "Launch the dedicated WhatsApp Web profile, run exports, and keep the AI-ready JSON fresh.",
                systemImage: "message.badge"
            )

            VStack(alignment: .trailing, spacing: 6) {
                Text(AppMetadata.displayVersion)
                    .font(.headline)
                Button {
                    appActions.checkForUpdates()
                } label: {
                    Label("Check for Updates...", systemImage: "arrow.triangle.2.circlepath")
                }
                .controlSize(.small)
                .accessibilityLabel("Check for Updates")
            }
            .frame(minWidth: 190, alignment: .trailing)
        }
    }

    private var metrics: some View {
        HStack(spacing: 12) {
            MetricTile(
                title: "Threads",
                value: "\(store.exportSummary.threadCount ?? store.export?.threads.count ?? 0)",
                detail: store.exportSummary.exists == true ? "Current export" : "No export yet",
                systemImage: "bubble.left.and.text.bubble.right"
            )
            MetricTile(
                title: "Last Export",
                value: DisplayFormatters.relativeDate(store.exportSummary.exportedAt ?? store.exportSummary.updatedAt),
                detail: DisplayFormatters.date(store.exportSummary.exportedAt ?? store.exportSummary.updatedAt),
                systemImage: "calendar.badge.clock"
            )
            MetricTile(
                title: "Schedule",
                value: store.schedule?.displayState ?? "Unknown",
                detail: scheduleDetail,
                systemImage: "clock.arrow.circlepath"
            )
            MetricTile(
                title: "App Version",
                value: AppMetadata.shortVersionString,
                detail: "Sparkle updates enabled",
                systemImage: "sparkles"
            )
        }
    }

    private var actions: some View {
        GroupBox {
            HStack(spacing: 10) {
                Button {
                    Task { await store.launchLogin() }
                } label: {
                    Label("Launch / Login", systemImage: "rectangle.on.rectangle")
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.isBusy)

                Button {
                    Task { await store.loadLabels() }
                } label: {
                    Label("Load Labels", systemImage: "tag")
                }
                .disabled(store.isBusy)

                Button {
                    Task { await store.runExport() }
                } label: {
                    Label("Run Export", systemImage: "square.and.arrow.down")
                }
                .keyboardShortcut("r", modifiers: [.command])
                .disabled(store.isBusy)

                Spacer()

                Button {
                    store.loadExportPreview()
                    store.requestSectionChange(.export)
                } label: {
                    Label("Open Export Preview", systemImage: "doc.text.magnifyingglass")
                }
                Button {
                    store.revealOutput()
                } label: {
                    Label("Reveal", systemImage: "folder")
                }
            }
            .padding(.vertical, 4)
        } label: {
            Text("Primary Actions")
        }
    }

    private var browserReadiness: some View {
        GroupBox("Browser Setup") {
            VStack(alignment: .leading, spacing: 6) {
                Text("Use Launch / Login to open the dedicated Chrome profile, then make sure WhatsApp Web or WhatsApp Business Web is logged in there before running an export.")
                Text("Keep that Chrome window open while exporting. After a successful export, WhatsApp Collector closes only its dedicated Chrome profile window/process. The app reads WhatsApp through that profile’s DevTools connection and does not use the old localhost browser UI.")
                    .foregroundStyle(.secondary)
            }
            .font(.callout)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private var collectionSettings: some View {
        GroupBox {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 14) {
                GridRow {
                    Text("Account label")
                        .foregroundStyle(.secondary)
                    TextField("WhatsApp", text: $store.draftConfiguration.accountLabel)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 360)
                }
                GridRow {
                    Text("Messages per conversation")
                        .foregroundStyle(.secondary)
                    HStack {
                        TextField("Messages", value: $store.draftConfiguration.maxMessages, format: .number)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 90)
                        Stepper("", value: $store.draftConfiguration.maxMessages, in: 1...500)
                            .labelsHidden()
                        Text("Recent messages saved for each collected thread.")
                            .foregroundStyle(.secondary)
                    }
                }
                GridRow {
                    Text("Recent chats from All")
                        .foregroundStyle(.secondary)
                    HStack {
                        TextField("Chats", value: $store.draftConfiguration.maxAllChats, format: .number)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 90)
                        Stepper("", value: $store.draftConfiguration.maxAllChats, in: 1...500)
                            .labelsHidden()
                        Text("Additional unlabeled/recent chats to collect.")
                            .foregroundStyle(.secondary)
                    }
                }
                GridRow {
                    Text("Groups")
                        .foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 6) {
                        Picker("Groups", selection: $store.draftConfiguration.includeGroups) {
                            ForEach(GroupInclusionMode.allCases) { mode in
                                Text(mode.title).tag(mode)
                            }
                        }
                        .labelsHidden()
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 420)
                        Text(store.draftConfiguration.includeGroups.detail)
                            .foregroundStyle(.secondary)
                    }
                }
                GridRow {
                    Text("Chrome window display")
                        .foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 6) {
                        TextField("Optional macOS display name", text: $store.draftConfiguration.displayName)
                            .textFieldStyle(.roundedBorder)
                            .frame(maxWidth: 360)
                        Text("Optional. Use this only if you want the dedicated Chrome window placed on a particular monitor. Enter the name of the display as it appears in macOS System Settings > Displays. Leave it blank to use the main display.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.vertical, 4)
        } label: {
            Text("Collection Settings")
        }
    }

    private var chromeProfile: some View {
        GroupBox("Chrome Profile Folder") {
            PathRow(
                title: "Dedicated Chrome profile",
                path: $store.draftConfiguration.profileDir,
                systemImage: "person.crop.square",
                actionTitle: "Open",
                action: store.openDraftProfileFolder
            )
            .padding(.vertical, 4)
        }
    }

    private var files: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 16) {
                PathRow(
                    title: "Export data file",
                    path: $store.draftConfiguration.outputPath,
                    systemImage: "doc.badge.gearshape",
                    actionTitle: "Copy",
                    action: store.copyDraftOutputPath
                )
            }
            .padding(.vertical, 4)
        } label: {
            Text("Files")
        }
    }

    private var scheduleDetail: String {
        if store.scheduledExportIsRunning {
            return store.scheduledExportStatusText
        }
        if let interval = store.schedule?.intervalMinutes, store.schedule?.enabled == true {
            if store.schedule?.mode == "web" {
                return "Legacy localhost runner, every \(interval) min"
            }
            if let lastSuccess = store.schedule?.lastSuccessAt {
                let next = store.schedule?.nextRunAfter.map { ", next \(DisplayFormatters.relativeDate($0))" } ?? ""
                return "Last \(DisplayFormatters.relativeDate(lastSuccess))\(next)"
            }
            return "Every \(interval) min"
        }
        return store.schedule?.nextStep ?? "Refresh status"
    }
}
