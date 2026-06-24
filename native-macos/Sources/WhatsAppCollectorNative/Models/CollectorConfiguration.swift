import Foundation

struct CollectorConfiguration: Codable, Equatable, Sendable {
    var outputPath: String
    var profileDir: String
    var accountLabel: String
    var maxMessages: Int
    var maxAllChats: Int
    var allowLabels: [String]
    var excludeLabels: [String]
    var includeGroups: GroupInclusionMode
    var displayName: String
    var debugPort: Int
    var markerTitle: String
    var markerUrlSubstring: String
    var targetUrl: String

    static let defaultOutputPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json")
        .path

    static let defaultProfileDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/WhatsApp Collector/Chrome Profile")
        .path

    static var defaults: CollectorConfiguration {
        CollectorConfiguration(
            outputPath: defaultOutputPath,
            profileDir: defaultProfileDir,
            accountLabel: "WhatsApp",
            maxMessages: 15,
            maxAllChats: 15,
            allowLabels: [],
            excludeLabels: [],
            includeGroups: .standard,
            displayName: "",
            debugPort: 19220,
            markerTitle: "WhatsApp Collector",
            markerUrlSubstring: "whatsapp-collector",
            targetUrl: "https://web.whatsapp.com/"
        )
    }

    func withSchedulePayload(_ payload: SchedulePayload?) -> CollectorConfiguration {
        guard let payload else { return self }
        var copy = self
        copy.maxMessages = payload.maxMessages ?? copy.maxMessages
        copy.maxAllChats = payload.maxAllChats ?? copy.maxAllChats
        copy.accountLabel = payload.accountLabel ?? copy.accountLabel
        copy.allowLabels = payload.allowLabels ?? copy.allowLabels
        copy.excludeLabels = payload.excludeLabels ?? copy.excludeLabels
        copy.includeGroups = payload.includeGroups ?? copy.includeGroups
        copy.displayName = payload.displayName ?? copy.displayName
        copy.profileDir = payload.profileDir ?? copy.profileDir
        copy.outputPath = payload.outputPath ?? copy.outputPath
        copy.debugPort = payload.debugPort ?? copy.debugPort
        copy.markerTitle = payload.markerTitle ?? copy.markerTitle
        copy.markerUrlSubstring = payload.markerUrlSubstring ?? copy.markerUrlSubstring
        copy.targetUrl = payload.targetUrl ?? copy.targetUrl
        return copy
    }

    func normalizedLabels(_ labels: [String]) -> [String] {
        var seen = Set<String>()
        var normalized: [String] = []
        for label in labels {
            let trimmed = label.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            let key = trimmed.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
            guard !seen.contains(key) else { continue }
            seen.insert(key)
            normalized.append(trimmed)
        }
        return normalized
    }

    mutating func cleanLabelLists() {
        allowLabels = normalizedLabels(allowLabels)
        excludeLabels = normalizedLabels(excludeLabels)
    }
}

enum GroupInclusionMode: String, Codable, CaseIterable, Identifiable, Sendable {
    case standard
    case labeledAlways

    var id: String { rawValue }

    var title: String {
        switch self {
        case .standard: "Standard"
        case .labeledAlways: "Only Always Include groups"
        }
    }

    var detail: String {
        switch self {
        case .standard:
            "Groups can be included by the normal recent-chat window, the same as direct chats."
        case .labeledAlways:
            "Groups are skipped unless they have a label marked Always Include."
        }
    }
}

struct BridgeRequest: Encodable, Sendable {
    var outputPath: String
    var profileDir: String
    var accountLabel: String
    var maxMessages: Int
    var maxAllChats: Int
    var allowLabels: [String]
    var excludeLabels: [String]
    var includeGroups: String
    var displayName: String?
    var debugPort: Int
    var markerTitle: String
    var markerUrlSubstring: String
    var targetUrl: String
    var intervalMinutes: Int?

    init(configuration: CollectorConfiguration, intervalMinutes: Int? = nil) {
        outputPath = configuration.outputPath
        profileDir = configuration.profileDir
        accountLabel = configuration.accountLabel
        maxMessages = max(1, configuration.maxMessages)
        maxAllChats = max(1, configuration.maxAllChats)
        allowLabels = configuration.allowLabels
        excludeLabels = configuration.excludeLabels
        includeGroups = configuration.includeGroups.rawValue
        displayName = configuration.displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : configuration.displayName
        debugPort = configuration.debugPort
        markerTitle = configuration.markerTitle
        markerUrlSubstring = configuration.markerUrlSubstring
        targetUrl = configuration.targetUrl
        self.intervalMinutes = intervalMinutes
    }
}
