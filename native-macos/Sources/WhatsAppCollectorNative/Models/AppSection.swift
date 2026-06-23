import Foundation

enum AppSection: String, CaseIterable, Identifiable {
    case dashboard
    case labels
    case export
    case automation
    case diagnostics
    case help

    var id: String { rawValue }

    var title: String {
        switch self {
        case .dashboard: "Dashboard"
        case .labels: "Labels"
        case .export: "Export"
        case .automation: "Automation"
        case .diagnostics: "Diagnostics"
        case .help: "Help"
        }
    }

    var navigationTitle: String {
        switch self {
        case .dashboard: "WhatsApp Collector Dashboard"
        default: title
        }
    }

    var subtitle: String {
        switch self {
        case .dashboard: "Run and monitor"
        case .labels: "Rules"
        case .export: "Preview output"
        case .automation: "Schedule"
        case .diagnostics: "Bridge details"
        case .help: "How to use"
        }
    }

    var systemImage: String {
        switch self {
        case .dashboard: "gauge.with.dots.needle.bottom.50percent"
        case .labels: "tag"
        case .export: "doc.text.magnifyingglass"
        case .automation: "clock.arrow.circlepath"
        case .diagnostics: "stethoscope"
        case .help: "questionmark.circle"
        }
    }
}
