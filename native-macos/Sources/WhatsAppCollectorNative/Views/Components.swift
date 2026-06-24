import SwiftUI

struct SectionHeader: View {
    var title: String
    var subtitle: String?
    var systemImage: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            if let systemImage {
                Image(systemName: systemImage)
                    .foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.title2.weight(.semibold))
                if let subtitle {
                    Text(subtitle)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
    }
}

struct MetricTile: View {
    var title: String
    var value: String
    var detail: String
    var systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label(title, systemImage: systemImage)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
            }
            Text(value)
                .font(.system(size: 26, weight: .semibold, design: .rounded))
                .lineLimit(1)
                .minimumScaleFactor(0.75)
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 118, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color(nsColor: .separatorColor).opacity(0.45))
        }
    }
}

struct StatusBanner: View {
    var busyState: BusyState
    var error: String?
    var scheduledRunActive: Bool = false
    var scheduledRunText: String = "Scheduled export running."

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .foregroundStyle(color)
            Text(text)
                .font(.callout)
                .foregroundStyle(error == nil ? .secondary : .primary)
                .lineLimit(2)
            Spacer()
            if busyState != .idle || scheduledRunActive {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .padding(10)
        .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(color.opacity(0.22))
        }
    }

    private var icon: String {
        if error != nil { return "exclamationmark.triangle" }
        if scheduledRunActive { return "clock.arrow.circlepath" }
        if busyState != .idle { return "arrow.triangle.2.circlepath" }
        return "checkmark.circle"
    }

    private var color: Color {
        if error != nil { return .orange }
        if scheduledRunActive { return .accentColor }
        if busyState != .idle { return .accentColor }
        return .green
    }

    private var text: String {
        if let error { return error }
        if busyState == .idle && scheduledRunActive { return scheduledRunText }
        return busyState == .idle ? "Ready." : "\(busyState.title)..."
    }
}

struct UpdateStatusBanner: View {
    var state: UpdateAvailabilityState
    var checkNow: () -> Void
    var openRelease: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: state.iconName)
                .foregroundStyle(color)
            VStack(alignment: .leading, spacing: 2) {
                Text(state.title)
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                Text(state.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            if state.isChecking {
                ProgressView()
                    .controlSize(.small)
            }
            if state.updateAvailable {
                Button("Latest Release", action: openRelease)
                    .controlSize(.small)
            }
            Button("Check for Updates...", action: checkNow)
                .controlSize(.small)
        }
        .padding(10)
        .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(color.opacity(0.24))
        }
    }

    private var color: Color {
        if state.updateAvailable { return .blue }
        if state.errorMessage != nil { return .orange }
        return .green
    }
}

struct PathRow: View {
    var title: String
    @Binding var path: String
    var systemImage: String
    var actionTitle: String
    var action: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label(title, systemImage: systemImage)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            HStack(spacing: 8) {
                TextField(title, text: $path)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                Button(actionTitle, action: action)
            }
            Text(DisplayFormatters.shortPath(path))
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }
}

struct LabelChip: View {
    var title: String
    var role: LabelRole
    var allowAction: () -> Void
    var excludeAction: () -> Void
    var clearAction: () -> Void
    var deleteAction: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Text(title)
                .font(.callout)
                .lineLimit(1)
            Spacer(minLength: 6)
            Picker("", selection: roleBinding) {
                Text("Standard").tag(LabelRole.standard)
                Text("Always Include").tag(LabelRole.alwaysInclude)
                Text("Never Include").tag(LabelRole.neverInclude)
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .frame(width: 330)
            Button(role: .destructive, action: deleteAction) {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .help("Remove this label from the local rule list")
            .accessibilityLabel("Remove \(title) from label list")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(background, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color(nsColor: .separatorColor).opacity(0.45))
        }
    }

    private var roleBinding: Binding<LabelRole> {
        Binding(
            get: { role },
            set: { newValue in
                switch newValue {
                case .standard: clearAction()
                case .alwaysInclude: allowAction()
                case .neverInclude: excludeAction()
                }
            }
        )
    }

    private var background: Color {
        switch role {
        case .standard: Color(nsColor: .controlBackgroundColor)
        case .alwaysInclude: Color.green.opacity(0.12)
        case .neverInclude: Color.orange.opacity(0.12)
        }
    }
}

enum LabelRole: Hashable {
    case standard
    case alwaysInclude
    case neverInclude
}

struct EmptyState: View {
    var title: String
    var detail: String
    var systemImage: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}
