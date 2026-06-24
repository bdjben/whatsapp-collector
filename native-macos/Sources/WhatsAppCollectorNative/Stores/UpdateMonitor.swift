import Foundation

@MainActor
final class UpdateMonitor: ObservableObject {
    static let automaticCheckIntervalSeconds: UInt64 = 15 * 60

    @Published private(set) var state = UpdateAvailabilityState()

    private let service: UpdateAvailabilityService
    private var automaticCheckTask: Task<Void, Never>?

    init(service: UpdateAvailabilityService = UpdateAvailabilityService()) {
        self.service = service
    }

    deinit {
        automaticCheckTask?.cancel()
    }

    func startAutomaticChecks() {
        guard automaticCheckTask == nil else { return }
        automaticCheckTask = Task { [weak self] in
            await self?.checkNow(trigger: .automatic)
            while Task.isCancelled == false {
                try? await Task.sleep(nanoseconds: Self.automaticCheckIntervalSeconds * 1_000_000_000)
                await self?.checkNow(trigger: .automatic)
            }
        }
    }

    func checkNow(trigger: UpdateCheckTrigger = .manual) async {
        if state.isChecking { return }
        state.isChecking = true
        state.trigger = trigger
        state.errorMessage = nil
        do {
            let update = try await service.latestUpdate(from: AppMetadata.appcastURL)
            state.latestVersion = update.version
            state.latestTitle = update.title
            state.downloadURL = update.downloadURL
            state.checkedAt = Date()
            state.errorMessage = nil
            state.isChecking = false
        } catch {
            state.checkedAt = Date()
            state.errorMessage = error.localizedDescription
            state.isChecking = false
        }
    }
}
