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
                    error: store.lastError ?? store.exportStatusError,
                    scheduledRunActive: store.scheduledExportIsRunning,
                    scheduledRunText: store.scheduledExportStatusText
                )

                if store.exportActivityIsVisible, let progress = store.displayedExportProgress {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(progress.summary)
                            .font(.callout.weight(.medium))
                        if let detail = progress.detail, !detail.isEmpty {
                            Text(detail)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let fraction = progress.fractionCompleted {
                            ProgressView(value: fraction)
                                .accessibilityLabel(progress.summary)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                metrics
                actions
                collectionSettings
                attachmentDownloads
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
                subtitle: "Run exports through a dedicated WhatsApp Web profile and keep the AI-ready JSON fresh.",
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
                title: "Automation",
                value: store.automationStatusText,
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
                Text("Use Launch / Login for first-time setup or whenever WhatsApp asks you to sign in again. Run Export automatically opens or reuses that same dedicated Chrome profile.")
                Text("Keep the dedicated window open while an export is underway. After success, WhatsApp Collector closes only the exact Chrome process it opened or reused for the configured profile and DevTools port. If an export fails, that process closes after a five-minute grace period while the app remains open; an interrupted cleanup is retried on the next launch. Other Chrome profiles and windows are not targeted. The app does not use the old localhost browser UI.")
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

    private var attachmentDownloads: some View {
        GroupBox("Attachment Downloads") {
            VStack(alignment: .leading, spacing: 12) {
                Toggle("Download attachments automatically", isOn: $store.draftConfiguration.downloadAttachments)

                VStack(alignment: .leading, spacing: 10) {
                    attachmentLimitRow("Single attachment limit", value: $store.draftConfiguration.attachmentFileLimitMB,
                                       unit: "MB", range: 10...1_000, step: 10)
                    attachmentLimitRow("Per-chat storage limit", value: $store.draftConfiguration.attachmentChatLimitMB,
                                       unit: "MB", range: 10...5_000, step: 50)
                    attachmentLimitRow("Total attachment storage limit", value: $store.draftConfiguration.attachmentStorageLimitGB,
                                       unit: "GB", range: 2...25, step: 0.1)
                }
                .disabled(store.draftConfiguration.downloadAttachments == false)

                Text("Automatic cleanup: oldest local files first, below 75% of the chat or total limit. Current-export attachments are protected. JSON backups are kept for one year.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                if let summary = store.export?.attachmentSummary, let detected = summary.detected, detected > 0 {
                    Text("Current export: \(summary.downloaded ?? 0) of \(detected) attachments downloaded and verified (\(DisplayFormatters.bytes(summary.downloadedBytes))).")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }
                if let cleanup = store.export?.exportDiagnostics?.attachmentCleanup, (cleanup.deletedFileCount ?? 0) > 0 {
                    Text("Last export cleanup: \(cleanup.deletedFileCount ?? 0) \(cleanup.deletedFileCount == 1 ? "file" : "files") removed, \(DisplayFormatters.bytes(cleanup.deletedBytes)) reclaimed.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private func attachmentLimitRow(_ title: String, value: Binding<Double>, unit: String,
                                    range: ClosedRange<Double>, step: Double) -> some View {
        HStack(spacing: 10) {
            Text(title).foregroundStyle(.secondary).frame(width: 235, alignment: .leading)
            TextField(unit, value: value, format: .number.precision(.fractionLength(0...1)))
                .textFieldStyle(.roundedBorder)
                .frame(width: 90)
                .accessibilityLabel("\(title) in \(unit)")
            Stepper(title, value: value, in: range, step: step)
                .labelsHidden()
                .accessibilityLabel("Adjust \(title.lowercased())")
            Text(unit).foregroundStyle(.secondary)
            Text("\(Int(range.lowerBound))-\(Int(range.upperBound)) \(unit)")
                .font(.caption).foregroundStyle(.secondary)
            Spacer(minLength: 0)
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
