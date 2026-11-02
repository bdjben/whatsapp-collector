import SwiftUI

struct AutomationView: View {
    @EnvironmentObject private var store: CollectorStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(
                    title: "Automatic Exports",
                    subtitle: "Recurring exports run while WhatsApp Collector remains open in the menu bar.",
                    systemImage: "clock.arrow.circlepath"
                )

                StatusBanner(
                    busyState: store.busyState,
                    error: store.lastError,
                    scheduledRunActive: store.scheduledExportIsRunning,
                    scheduledRunText: store.scheduledExportStatusText
                )

                GroupBox("Login & Wake") {
                    VStack(alignment: .leading, spacing: 12) {
                        Toggle(isOn: Binding(
                            get: { store.draftLaunchAtLoginEnabled },
                            set: { store.setDraftLaunchAtLogin($0) }
                        )) {
                            Label("Open WhatsApp Collector when you log in", systemImage: "power")
                        }
                        .toggleStyle(.switch)
                        Text("Launches the WhatsApp Collector menu-bar app after you sign in. This setting alone does not run an export.")
                            .font(.callout)
                            .foregroundStyle(.secondary)

                        Toggle(isOn: $store.draftRunExportAfterLoginEnabled) {
                            Label("Run an export after login", systemImage: "arrow.clockwise.circle")
                        }
                        .toggleStyle(.switch)
                        .disabled(store.draftLaunchAtLoginEnabled == false)
                        Text("Optional. Runs one export five minutes after you log in following startup or restart, five minutes after you sign out and later log back into this macOS account (including after using another or Guest account), and five minutes after the Mac wakes from sleep. Recurring exports can remain off.")
                            .font(.callout)
                            .foregroundStyle(.secondary)

                        Toggle(isOn: $store.draftLaunchMinimizedEnabled) {
                            Label("Open at login with the dashboard hidden", systemImage: "menubar.rectangle")
                        }
                        .toggleStyle(.switch)
                        .disabled(store.draftLaunchAtLoginEnabled == false)
                        Text("The menu-bar item remains available. Opening the app manually still shows the dashboard.")
                            .font(.callout)
                            .foregroundStyle(.secondary)

                        Text(store.launchAtLoginDisplayStatus)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        if let pendingLoginExportAt = store.pendingLoginExportAt {
                            Text("Next login export: \(DisplayFormatters.date(pendingLoginExportAt))")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                        if let pendingWakeExportAt = store.pendingWakeExportAt {
                            Text("Next wake export: \(DisplayFormatters.date(pendingWakeExportAt))")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack {
                            Text("Status")
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(store.automationStatusText)
                                .font(.headline)
                        }
                        HStack {
                            Text("Interval")
                                .foregroundStyle(.secondary)
                            TextField("Minutes", value: $store.draftScheduleIntervalMinutes, format: .number)
                                .textFieldStyle(.roundedBorder)
                                .frame(width: 90)
                            Stepper("", value: $store.draftScheduleIntervalMinutes, in: 1...(24 * 60))
                                .labelsHidden()
                            Text("minutes")
                                .foregroundStyle(.secondary)
                        }
                        if let nextStep = store.schedule?.nextStep {
                            Text(nextStep)
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                        scheduleRunHistory
                        HStack {
                            if store.schedule?.enabled != true {
                                Button {
                                    Task { await store.installSchedule() }
                                } label: {
                                    Label("Start Automatic Exports", systemImage: "play.circle")
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(store.isBusy)
                            }

                            Button {
                                Task { await store.removeSchedule() }
                            } label: {
                                Label("Stop", systemImage: "stop.circle")
                            }
                            .disabled(store.isBusy || store.schedule?.enabled != true)

                            Button {
                                Task {
                                    await store.refreshStatus()
                                    await store.refreshScheduleStatusQuietly()
                                }
                            } label: {
                                Label("Refresh", systemImage: "arrow.clockwise")
                            }
                            .disabled(store.isBusy)
                        }
                    }
                    .padding(.vertical, 4)
                } label: {
                    Text("Schedule")
                }
            }
            .padding(22)
        }
        .safeAreaInset(edge: .bottom) {
            SaveChangesBar()
        }
    }

    @ViewBuilder
    private var scheduleRunHistory: some View {
        if let schedule = store.schedule, schedule.enabled == true {
            Divider()
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                ScheduleReadoutRow(
                    title: "Current state",
                    value: currentRunText(schedule)
                )
                ScheduleReadoutRow(
                    title: "Last automatic attempt",
                    value: timestampText(schedule.lastRunAt)
                )
                ScheduleReadoutRow(
                    title: "Last successful automatic export",
                    value: timestampText(schedule.lastSuccessAt)
                )
                ScheduleReadoutRow(
                    title: "Threads exported",
                    value: schedule.lastThreadCount.map { "\($0)" } ?? "Not recorded"
                )
                ScheduleReadoutRow(
                    title: "Next recurring run",
                    value: timestampText(schedule.nextRunAfter)
                )
                if shouldShowFailure(schedule) {
                    ScheduleReadoutRow(
                        title: "Last automatic-export error",
                        value: failureText(schedule)
                    )
                }
            }
        }
    }

    private func timestampText(_ value: String?) -> String {
        guard value != nil else { return "Not recorded yet" }
        return "\(DisplayFormatters.relativeDate(value)) · \(DisplayFormatters.date(value))"
    }

    private func currentRunText(_ schedule: ScheduleState) -> String {
        if schedule.isCurrentRunActive {
            return schedule.currentRunStartedAt.map { "Running since \(DisplayFormatters.date($0))" } ?? "Running now"
        }
        if let status = schedule.currentRunStatus, status.isEmpty == false {
            let updated = schedule.currentRunUpdatedAt.map { " · \(DisplayFormatters.relativeDate($0))" } ?? ""
            return "\(status.capitalized)\(updated)"
        }
        return "Idle"
    }

    private func shouldShowFailure(_ schedule: ScheduleState) -> Bool {
        guard let failureAt = DisplayFormatters.parseDate(schedule.lastFailureAt) else { return false }
        guard let successAt = DisplayFormatters.parseDate(schedule.lastSuccessAt) else { return true }
        return failureAt > successAt
    }

    private func failureText(_ schedule: ScheduleState) -> String {
        let message = schedule.lastFailureMessage?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let message, !message.isEmpty {
            return "\(timestampText(schedule.lastFailureAt)) · \(message)"
        }
        return timestampText(schedule.lastFailureAt)
    }
}

private struct ScheduleReadoutRow: View {
    var title: String
    var value: String

    var body: some View {
        GridRow {
            Text(title)
                .foregroundStyle(.secondary)
            Text(value)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }
}
