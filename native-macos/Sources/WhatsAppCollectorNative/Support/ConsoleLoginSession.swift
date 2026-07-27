import Darwin
import Foundation

struct ConsoleLoginSession: Equatable, Sendable {
    let id: String
    let startedAt: Date

    static func current(username: String = NSUserName(), uid: uid_t = getuid()) -> ConsoleLoginSession? {
        setutxent()
        defer { endutxent() }

        var newestLogin: time_t?
        while let pointer = getutxent() {
            let entry = pointer.pointee
            guard entry.ut_type == USER_PROCESS else { continue }
            guard fixedString(entry.ut_line) == "console" else { continue }
            guard fixedString(entry.ut_user) == username else { continue }
            if newestLogin == nil || entry.ut_tv.tv_sec > newestLogin! {
                newestLogin = entry.ut_tv.tv_sec
            }
        }
        guard let newestLogin else { return nil }
        return make(username: username, uid: uid, loginSeconds: newestLogin)
    }

    static func make(username: String, uid: uid_t, loginSeconds: time_t) -> ConsoleLoginSession {
        ConsoleLoginSession(
            id: "\(uid):\(username):\(loginSeconds)",
            startedAt: Date(timeIntervalSince1970: TimeInterval(loginSeconds))
        )
    }

    private static func fixedString<T>(_ value: T) -> String {
        var copy = value
        return withUnsafeBytes(of: &copy) { bytes in
            String(decoding: bytes.prefix { $0 != 0 }, as: UTF8.self)
        }
    }
}

enum LoginLaunchBehavior {
    static let automaticLaunchWindow: TimeInterval = 10 * 60

    static func shouldHideDashboard(
        launchAtLoginEnabled: Bool,
        launchMinimized: Bool,
        session: ConsoleLoginSession?,
        previouslySeenSessionID: String?,
        now: Date
    ) -> Bool {
        guard launchAtLoginEnabled, launchMinimized, let session else { return false }
        guard previouslySeenSessionID != session.id else { return false }
        let elapsed = now.timeIntervalSince(session.startedAt)
        return elapsed >= 0 && elapsed <= automaticLaunchWindow
    }
}
