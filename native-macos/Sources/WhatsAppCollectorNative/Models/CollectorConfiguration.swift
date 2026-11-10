import Foundation

enum AttachmentLimits {
    static let defaultFileBytes: Int64 = 20_000_000
    static let defaultChatBytes: Int64 = 1_000_000_000
    static let defaultTotalBytes: Int64 = 5_000_000_000
    static let fileBytes: ClosedRange<Int64> = 10_000_000...1_000_000_000
    static let chatBytes: ClosedRange<Int64> = 10_000_000...5_000_000_000
    static let totalBytes: ClosedRange<Int64> = 2_000_000_000...25_000_000_000

    static func bounded(_ value: Int64, to range: ClosedRange<Int64>) -> Int64 {
        min(max(value, range.lowerBound), range.upperBound)
    }

    static func bytes(from value: Double, unit: Double, range: ClosedRange<Int64>, fallback: Int64) -> Int64 {
        guard value.isFinite else { return fallback }
        let bounded = min(max(value, Double(range.lowerBound) / unit), Double(range.upperBound) / unit)
        return Int64((bounded * unit).rounded())
    }
}

struct CollectorConfiguration: Codable, Equatable, Sendable {
    var outputPath: String
    var profileDir: String
    var accountLabel: String
    var maxMessages: Int
    var maxAllChats: Int
    var allowLabels: [String]
    var excludeLabels: [String]
    var includeGroups: GroupInclusionMode
    var downloadAttachments: Bool
    var attachmentFileLimitBytes: Int64
    var attachmentChatLimitBytes: Int64
    var attachmentStorageLimitBytes: Int64
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
            downloadAttachments: true,
            attachmentFileLimitBytes: AttachmentLimits.defaultFileBytes,
            attachmentChatLimitBytes: AttachmentLimits.defaultChatBytes,
            attachmentStorageLimitBytes: AttachmentLimits.defaultTotalBytes,
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
        copy.downloadAttachments = payload.downloadAttachments ?? copy.downloadAttachments
        copy.attachmentFileLimitBytes = payload.attachmentFileLimitBytes ?? copy.attachmentFileLimitBytes
        copy.attachmentChatLimitBytes = payload.attachmentChatLimitBytes ?? copy.attachmentChatLimitBytes
        copy.attachmentStorageLimitBytes = payload.attachmentStorageLimitBytes ?? copy.attachmentStorageLimitBytes
        copy.displayName = payload.displayName ?? copy.displayName
        copy.profileDir = payload.profileDir ?? copy.profileDir
        copy.outputPath = payload.outputPath ?? copy.outputPath
        copy.debugPort = payload.debugPort ?? copy.debugPort
        copy.markerTitle = payload.markerTitle ?? copy.markerTitle
        copy.markerUrlSubstring = payload.markerUrlSubstring ?? copy.markerUrlSubstring
        copy.targetUrl = payload.targetUrl ?? copy.targetUrl
        copy.normalizeAttachmentLimits()
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

    var attachmentStorageLimitGB: Double {
        get { Double(attachmentStorageLimitBytes) / 1_000_000_000 }
        set { attachmentStorageLimitBytes = AttachmentLimits.bytes(from: newValue, unit: 1_000_000_000,
            range: AttachmentLimits.totalBytes, fallback: attachmentStorageLimitBytes) }
    }

    var attachmentFileLimitMB: Double {
        get { Double(attachmentFileLimitBytes) / 1_000_000 }
        set { attachmentFileLimitBytes = AttachmentLimits.bytes(from: newValue, unit: 1_000_000,
            range: AttachmentLimits.fileBytes, fallback: attachmentFileLimitBytes) }
    }

    var attachmentChatLimitMB: Double {
        get { Double(attachmentChatLimitBytes) / 1_000_000 }
        set { attachmentChatLimitBytes = AttachmentLimits.bytes(from: newValue, unit: 1_000_000,
            range: AttachmentLimits.chatBytes, fallback: attachmentChatLimitBytes) }
    }

    mutating func normalizeAttachmentLimits() {
        attachmentFileLimitBytes = AttachmentLimits.bounded(attachmentFileLimitBytes, to: AttachmentLimits.fileBytes)
        attachmentChatLimitBytes = AttachmentLimits.bounded(attachmentChatLimitBytes, to: AttachmentLimits.chatBytes)
        attachmentStorageLimitBytes = AttachmentLimits.bounded(attachmentStorageLimitBytes, to: AttachmentLimits.totalBytes)
    }
}

extension CollectorConfiguration {
    private enum CodingKeys: String, CodingKey {
        case outputPath, profileDir, accountLabel, maxMessages, maxAllChats, allowLabels, excludeLabels, includeGroups
        case downloadAttachments, attachmentFileLimitBytes, attachmentChatLimitBytes, attachmentStorageLimitBytes
        case displayName, debugPort, markerTitle, markerUrlSubstring, targetUrl
    }

    init(from decoder: Decoder) throws {
        self = .defaults
        let values = try decoder.container(keyedBy: CodingKeys.self)
        outputPath = try values.decodeIfPresent(String.self, forKey: .outputPath) ?? outputPath
        profileDir = try values.decodeIfPresent(String.self, forKey: .profileDir) ?? profileDir
        accountLabel = try values.decodeIfPresent(String.self, forKey: .accountLabel) ?? accountLabel
        maxMessages = try values.decodeIfPresent(Int.self, forKey: .maxMessages) ?? maxMessages
        maxAllChats = try values.decodeIfPresent(Int.self, forKey: .maxAllChats) ?? maxAllChats
        allowLabels = try values.decodeIfPresent([String].self, forKey: .allowLabels) ?? allowLabels
        excludeLabels = try values.decodeIfPresent([String].self, forKey: .excludeLabels) ?? excludeLabels
        includeGroups = try values.decodeIfPresent(GroupInclusionMode.self, forKey: .includeGroups) ?? includeGroups
        downloadAttachments = try values.decodeIfPresent(Bool.self, forKey: .downloadAttachments) ?? downloadAttachments
        attachmentFileLimitBytes = try values.decodeIfPresent(Int64.self, forKey: .attachmentFileLimitBytes) ?? attachmentFileLimitBytes
        attachmentChatLimitBytes = try values.decodeIfPresent(Int64.self, forKey: .attachmentChatLimitBytes) ?? attachmentChatLimitBytes
        attachmentStorageLimitBytes = try values.decodeIfPresent(Int64.self, forKey: .attachmentStorageLimitBytes) ?? attachmentStorageLimitBytes
        displayName = try values.decodeIfPresent(String.self, forKey: .displayName) ?? displayName
        debugPort = try values.decodeIfPresent(Int.self, forKey: .debugPort) ?? debugPort
        markerTitle = try values.decodeIfPresent(String.self, forKey: .markerTitle) ?? markerTitle
        markerUrlSubstring = try values.decodeIfPresent(String.self, forKey: .markerUrlSubstring) ?? markerUrlSubstring
        targetUrl = try values.decodeIfPresent(String.self, forKey: .targetUrl) ?? targetUrl
        normalizeAttachmentLimits()
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
    var downloadAttachments: Bool
    var attachmentFileLimitBytes: Int64
    var attachmentChatLimitBytes: Int64
    var attachmentStorageLimitBytes: Int64
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
        downloadAttachments = configuration.downloadAttachments
        attachmentFileLimitBytes = AttachmentLimits.bounded(configuration.attachmentFileLimitBytes, to: AttachmentLimits.fileBytes)
        attachmentChatLimitBytes = AttachmentLimits.bounded(configuration.attachmentChatLimitBytes, to: AttachmentLimits.chatBytes)
        attachmentStorageLimitBytes = AttachmentLimits.bounded(configuration.attachmentStorageLimitBytes, to: AttachmentLimits.totalBytes)
        displayName = configuration.displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : configuration.displayName
        debugPort = configuration.debugPort
        markerTitle = configuration.markerTitle
        markerUrlSubstring = configuration.markerUrlSubstring
        targetUrl = configuration.targetUrl
        self.intervalMinutes = intervalMinutes
    }
}
