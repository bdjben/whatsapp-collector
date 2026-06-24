import SwiftUI

struct AutomationView: View {
    @EnvironmentObject private var store: CollectorStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(
                    title: "Automatic Exports",
                    subtitle: "Use the existing macOS LaunchAgent refresh path without copying shell commands.",
                    systemImage: "clock.arrow.circlepath"
                )

                StatusBanner(
                    busyState: store.busyState,
                    error: store.lastError,
                    scheduledRunActive: store.scheduledExportIsRunning,
                    scheduledRunText: store.scheduledExportStatusText
                )

                GroupBox("Launch at Login") {
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle(isOn: Binding(
                            get: { store.launchAtLoginEnabled },
                            set: { store.setLaunchAtLogin($0) }
                        )) {
                            Label("Open WhatsApp Collector when you log in", systemImage: "power")
                        }
                        .toggleStyle(.switch)
                        Text(store.launchAtLoginStatus)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }

                if store.schedule?.isLegacyWebSchedule == true {
                    Label(
                        "This schedule was installed by the older localhost web runner. Start automatic exports here to replace it with the native bridge runner.",
                        systemImage: "exclamationmark.triangle"
                    )
                    .font(.callout)
                    .foregroundStyle(.orange)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack {
                            Text("Status")
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(store.schedule?.displayState ?? "Unknown")
                                .font(.headline)
                        }
                        HStack {
                            Text("Interval")
                                .foregroundStyle(.secondary)
                            TextField("Minutes", value: $store.scheduleIntervalMinutes, format: .number)
                                .textFieldStyle(.roundedBorder)
                                .frame(width: 90)
                            Stepper("", value: $store.scheduleIntervalMinutes, in: 1...(24 * 60))
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
                            Button {
                                Task { await store.installSchedule() }
                            } label: {
                                Label("Start Automatic Exports", systemImage: "play.circle")
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(store.isBusy)

                            Button {
                                Task { await store.removeSchedule() }
                            } label: {
                                Label("Stop", systemImage: "stop.circle")
                            }
                            .disabled(store.isBusy)

                            Button {
                                Task { await store.refreshStatus() }
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

                GroupBox {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 10) {
                        GridRow {
                            Text("Mode")
                                .foregroundStyle(.secondary)
                            Text(store.schedule?.mode ?? "Not configured")
                                .font(.system(.body, design: .monospaced))
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        GridRow {
                            Text("LaunchAgent")
                                .foregroundStyle(.secondary)
                            Text(store.schedule?.plistPath ?? "Not configured")
                                .font(.system(.body, design: .monospaced))
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        GridRow {
                            Text("Payload")
                                .foregroundStyle(.secondary)
                            Text(store.schedule?.payloadPath ?? "Not configured")
                                .font(.system(.body, design: .monospaced))
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        GridRow {
                            Text("Bridge")
                                .foregroundStyle(.secondary)
                            Text(store.schedule?.bridgePath ?? "Not configured")
                                .font(.system(.body, design: .monospaced))
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        GridRow {
                            Text("Logs")
                                .foregroundStyle(.secondary)
                            Text(store.schedule?.stderrPath ?? "Not configured")
                                .font(.system(.body, design: .monospaced))
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                    }
                    .padding(.vertical, 4)
                } label: {
                    Text("Readback")
                }
            }
            .padding(22)
        }
    }

    @ViewBuilder
    private var scheduleRunHistory: some View {
        if let schedule = store.schedule, schedule.enabled == true {
            Divider()
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                ScheduleReadoutRow(
                    title: "Current scheduler state",
                    value: currentRunText(schedule)
                )
                ScheduleReadoutRow(
                    title: "Last scheduled run",
                    value: timestampText(schedule.lastSuccessAt ?? schedule.lastRunAt)
                )
                ScheduleReadoutRow(
                    title: "Threads exported",
                    value: schedule.lastThreadCount.map { "\($0)" } ?? "Not recorded"
                )
                ScheduleReadoutRow(
                    title: "Next estimated run",
                    value: timestampText(schedule.nextRunAfter)
                )
                if shouldShowFailure(schedule) {
                    ScheduleReadoutRow(
                        title: "Last scheduler error",
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
