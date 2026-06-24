import Foundation

enum UpdateCheckTrigger: String, Sendable {
    case automatic
    case manual
}

struct UpdateAvailabilityState: Equatable, Sendable {
    var currentVersion: String = AppMetadata.shortVersionString
    var latestVersion: String?
    var latestTitle: String?
    var downloadURL: URL?
    var checkedAt: Date?
    var errorMessage: String?
    var isChecking: Bool = false
    var trigger: UpdateCheckTrigger = .automatic

    var updateAvailable: Bool {
        guard let latestVersion else { return false }
        return VersionComparator.isVersion(latestVersion, newerThan: currentVersion)
    }

    var iconName: String {
        if updateAvailable { return "arrow.down.circle.fill" }
        if isChecking { return "arrow.triangle.2.circlepath" }
        if errorMessage != nil { return "exclamationmark.triangle" }
        return "checkmark.circle"
    }

    var title: String {
        if updateAvailable, let latestVersion {
            return "Update available: Version \(latestVersion)"
        }
        if isChecking {
            return "Checking for updates..."
        }
        if errorMessage != nil {
            return "Could not check for updates"
        }
        if checkedAt != nil {
            return "\(AppMetadata.displayVersion) is up to date"
        }
        return "\(AppMetadata.displayVersion) · update checks enabled"
    }

    var detail: String {
        if updateAvailable {
            return "The app checks automatically every 15 minutes. Use Check for Updates to install with Sparkle."
        }
        if let errorMessage {
            return errorMessage
        }
        if let checkedAt {
            return "Last checked \(DisplayFormatters.relativeDate(DisplayFormatters.isoString(from: checkedAt)))."
        }
        return "Automatic checks run every 15 minutes."
    }
}

struct AppcastUpdate: Equatable, Sendable {
    var version: String
    var title: String?
    var downloadURL: URL?
}

enum VersionComparator {
    static func isVersion(_ candidate: String, newerThan current: String) -> Bool {
        compare(candidate, current) == .orderedDescending
    }

    static func compare(_ lhs: String, _ rhs: String) -> ComparisonResult {
        let leftParts = numericParts(lhs)
        let rightParts = numericParts(rhs)
        let count = max(leftParts.count, rightParts.count)
        for index in 0..<count {
            let left = index < leftParts.count ? leftParts[index] : 0
            let right = index < rightParts.count ? rightParts[index] : 0
            if left > right { return .orderedDescending }
            if left < right { return .orderedAscending }
        }
        return .orderedSame
    }

    private static func numericParts(_ value: String) -> [Int] {
        value
            .split { character in
                character.isNumber == false
            }
            .compactMap { Int($0) }
    }
}
