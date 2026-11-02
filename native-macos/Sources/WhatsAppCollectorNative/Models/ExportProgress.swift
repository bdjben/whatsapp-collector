import Foundation

struct ExportProgress: Codable, Equatable, Sendable {
    var phase: String
    var completed: Int?
    var total: Int?
    var detail: String?
    var elapsedSeconds: Double?

    func displayed(since start: ContinuousClock.Instant, at now: ContinuousClock.Instant = .now) -> Self {
        let elapsed = start.duration(to: now).components
        var copy = self
        copy.elapsedSeconds = max(0, Double(elapsed.seconds) + Double(elapsed.attoseconds) / 1e18)
        return copy
    }

    var title: String {
        phase.replacingOccurrences(of: "_", with: " ").replacingOccurrences(of: "-", with: " ").capitalized
    }

    var fractionCompleted: Double? {
        guard let completed, let total, total > 0, completed >= 0 else { return nil }
        return min(1, Double(completed) / Double(total))
    }

    var summary: String {
        var parts = [title]
        if let completed, let total, completed >= 0, total > 0 {
            parts.append("\(completed) of \(total)")
        } else if let completed, completed >= 0, total == nil {
            parts.append("\(completed) processed")
        }
        if let elapsedSeconds, elapsedSeconds.isFinite, elapsedSeconds >= 0 {
            parts.append(String(format: "%.0fs elapsed", elapsedSeconds))
        }
        return parts.joined(separator: " | ")
    }
}

// Frame bytes before decoding so split UTF-8 and split records survive pipe reads.
struct BridgeProgressParser {
    private var pending = Data()
    private var discardingOversizedLine = false
    private static let maximumLineBytes = 64 * 1024

    mutating func append(_ data: Data, emit: (ExportProgress) -> Void) {
        for byte in data {
            if byte == 10 {
                if !discardingOversizedLine { decodePending(emit: emit) }
                pending.removeAll(keepingCapacity: true)
                discardingOversizedLine = false
            } else if !discardingOversizedLine {
                if pending.count < Self.maximumLineBytes {
                    pending.append(byte)
                } else {
                    pending.removeAll(keepingCapacity: true)
                    discardingOversizedLine = true
                }
            }
        }
    }

    mutating func finish(emit: (ExportProgress) -> Void) {
        if !discardingOversizedLine { decodePending(emit: emit) }
        pending.removeAll()
        discardingOversizedLine = false
    }

    private func decodePending(emit: (ExportProgress) -> Void) {
        let prefix = Data("WAC_PROGRESS:".utf8)
        guard pending.starts(with: prefix),
              let progress = try? JSONDecoder().decode(ExportProgress.self, from: pending.dropFirst(prefix.count)),
              !progress.phase.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        emit(progress)
    }
}
