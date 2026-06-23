import Foundation

enum BridgeCommand: String, Sendable {
    case status
    case ensureWindow = "ensure-window"
    case runExport = "run-export"
    case labels
    case scheduleStatus = "schedule-status"
    case scheduleInstall = "schedule-install"
    case scheduleRemove = "schedule-remove"
}

struct CollectorBridge: Sendable {
    func run(_ command: BridgeCommand, configuration: CollectorConfiguration, intervalMinutes: Int? = nil) throws -> (BridgeResponse, String) {
        let script = try bridgeScriptURL()
        let python = try pythonURL()
        let request = BridgeRequest(configuration: configuration, intervalMinutes: intervalMinutes)
        let input = try JSONEncoder().encode(request)

        let process = Process()
        process.executableURL = python
        process.arguments = [script.path, command.rawValue]
        process.environment = bridgeEnvironment(resourceDirectory: script.deletingLastPathComponent())

        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr

        try process.run()
        stdin.fileHandleForWriting.write(input)
        stdin.fileHandleForWriting.closeFile()
        process.waitUntilExit()

        let outputData = stdout.fileHandleForReading.readDataToEndOfFile()
        let errorData = stderr.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: outputData, encoding: .utf8) ?? ""
        let errorOutput = String(data: errorData, encoding: .utf8) ?? ""

        if process.terminationStatus != 0 {
            if let response = try? JSONDecoder().decode(BridgeResponse.self, from: outputData) {
                throw BridgeError(response.error ?? "Bridge command failed.", details: prettyOutput(output: output, error: errorOutput))
            }
            throw BridgeError("Bridge command failed with exit \(process.terminationStatus).", details: prettyOutput(output: output, error: errorOutput))
        }

        do {
            let response = try JSONDecoder().decode(BridgeResponse.self, from: outputData)
            if response.ok == false {
                throw BridgeError(response.error ?? "Bridge command returned ok=false.", details: prettyOutput(output: output, error: errorOutput))
            }
            return (response, output)
        } catch let bridgeError as BridgeError {
            throw bridgeError
        } catch {
            throw BridgeError("Could not decode bridge response.", details: prettyOutput(output: output, error: errorOutput))
        }
    }

    private func bridgeScriptURL() throws -> URL {
        let bundleResource = Bundle.main.resourceURL?.appendingPathComponent("native_bridge.py")
        let repoRoot = inferredRepoRoot()
        let candidates = [
            bundleResource,
            repoRoot.appendingPathComponent("native-macos/Support/native_bridge.py"),
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent("native-macos/Support/native_bridge.py")
        ].compactMap { $0 }

        for candidate in candidates where FileManager.default.fileExists(atPath: candidate.path) {
            return candidate
        }
        throw BridgeError("Could not find native_bridge.py.", details: candidates.map(\.path).joined(separator: "\n"))
    }

    private func pythonURL() throws -> URL {
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/opt/homebrew/bin/python3.14",
            "/opt/homebrew/opt/python@3.14/bin/python3.14",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]

        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path)
        }
        throw BridgeError("Could not find a usable Python 3 interpreter.", details: candidates.joined(separator: "\n"))
    }

    private func bridgeEnvironment(resourceDirectory: URL) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["WA_COLLECTOR_NATIVE_RESOURCE_DIR"] = resourceDirectory.path
        environment["WA_COLLECTOR_REPO_ROOT"] = inferredRepoRoot().path
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return environment
    }

    private func inferredRepoRoot() -> URL {
        let bundleURL = Bundle.main.bundleURL
        let bundleParent = bundleURL.deletingLastPathComponent()
        if bundleParent.lastPathComponent == "dist" {
            return bundleParent.deletingLastPathComponent()
        }
        let current = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        if FileManager.default.fileExists(atPath: current.appendingPathComponent("src/whatsapp_collector").path) {
            return current
        }
        return bundleParent
    }

    private func prettyOutput(output: String, error: String) -> String {
        var parts: [String] = []
        if !output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            parts.append("stdout:\n\(output)")
        }
        if !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            parts.append("stderr:\n\(error)")
        }
        return parts.joined(separator: "\n\n")
    }
}

struct BridgeError: LocalizedError, Sendable {
    var message: String
    var details: String

    init(_ message: String, details: String = "") {
        self.message = message
        self.details = details
    }

    var errorDescription: String? { message }
    var failureReason: String? { details.isEmpty ? nil : details }
}
