import Foundation
import ServiceManagement

enum LoginItemManager {
    static var isEnabled: Bool {
        SMAppService.mainApp.status == .enabled
    }

    static var statusDescription: String {
        switch SMAppService.mainApp.status {
        case .enabled:
            return "WhatsApp Collector will open when you log in."
        case .notRegistered:
            return "Launch at Login is off."
        case .requiresApproval:
            return "macOS needs approval in System Settings before Launch at Login can turn on."
        case .notFound:
            return "Launch at Login is unavailable for this build."
        @unknown default:
            return "Launch at Login status is unknown."
        }
    }

    static func setEnabled(_ enabled: Bool) throws {
        if enabled {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
    }
}
