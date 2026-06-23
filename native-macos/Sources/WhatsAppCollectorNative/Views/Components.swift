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

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .foregroundStyle(color)
            Text(text)
                .font(.callout)
                .foregroundStyle(error == nil ? .secondary : .primary)
                .lineLimit(2)
            Spacer()
            if busyState != .idle {
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
        if busyState != .idle { return "arrow.triangle.2.circlepath" }
        return "checkmark.circle"
    }

    private var color: Color {
        if error != nil { return .orange }
        if busyState != .idle { return .accentColor }
        return .green
    }

    private var text: String {
        if let error { return error }
        return busyState == .idle ? "Ready." : "\(busyState.title)..."
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

    var body: some View {
        HStack(spacing: 8) {
            Text(title)
                .font(.callout)
                .lineLimit(1)
            Spacer(minLength: 6)
            Picker("", selection: roleBinding) {
                Text("Ignore").tag(LabelRole.ignore)
                Text("Allow").tag(LabelRole.allow)
                Text("Exclude").tag(LabelRole.exclude)
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .frame(width: 190)
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
                case .ignore: clearAction()
                case .allow: allowAction()
                case .exclude: excludeAction()
                }
            }
        )
    }

    private var background: Color {
        switch role {
        case .ignore: Color(nsColor: .controlBackgroundColor)
        case .allow: Color.green.opacity(0.12)
        case .exclude: Color.orange.opacity(0.12)
        }
    }
}

enum LabelRole: Hashable {
    case ignore
    case allow
    case exclude
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
