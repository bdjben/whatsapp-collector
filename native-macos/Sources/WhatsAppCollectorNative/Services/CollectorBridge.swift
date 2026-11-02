import Foundation

enum BridgeCommand: String, Sendable {
    case status
    case ensureWindow = "ensure-window"
    case runExport = "run-export"
    case closeWindow = "close-window"
    case labels
    case scheduleStatus = "schedule-status"
    case scheduleInstall = "schedule-install"
    case scheduleRemove = "schedule-remove"
}

struct CollectorBridge: Sendable {
    func run(
        _ command: BridgeCommand,
        configuration: CollectorConfiguration,
        intervalMinutes: Int? = nil,
        chromeOwnershipURL: URL? = nil,
        onProgress: (@Sendable (ExportProgress) -> Void)? = nil
    ) throws -> (BridgeResponse, String) {
        let request = BridgeRequest(configuration: configuration, intervalMinutes: intervalMinutes)
        let input = try JSONEncoder().encode(request)
        if let chromeOwnershipURL {
            try FileManager.default.createDirectory(
                at: chromeOwnershipURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try input.write(to: chromeOwnershipURL, options: .atomic)
        }
        return try run(command, input: input, chromeOwnershipURL: chromeOwnershipURL, onProgress: onProgress)
    }

    func closeOwnedChrome(at chromeOwnershipURL: URL) throws -> (BridgeResponse, String) {
        let input = try Data(contentsOf: chromeOwnershipURL)
        return try run(.closeWindow, input: input, chromeOwnershipURL: nil)
    }

    private func run(
        _ command: BridgeCommand,
        input: Data,
        chromeOwnershipURL: URL?,
        onProgress: (@Sendable (ExportProgress) -> Void)? = nil
    ) throws -> (BridgeResponse, String) {
        let script = try bridgeScriptURL()
        let python = try pythonURL()

        let process = Process()
        process.executableURL = python
        process.arguments = [script.path, command.rawValue]
        process.environment = bridgeEnvironment(
            resourceDirectory: script.deletingLastPathComponent(),
            chromeOwnershipURL: chromeOwnershipURL
        )

        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr

        try process.run()
        stdin.fileHandleForWriting.write(input)
        stdin.fileHandleForWriting.closeFile()
        let (outputData, errorData) = BridgeProcessOutput.waitForExit(process, stdout: stdout, stderr: stderr, onProgress: onProgress)
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

    private func bridgeEnvironment(resourceDirectory: URL, chromeOwnershipURL: URL?) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["WA_COLLECTOR_NATIVE_RESOURCE_DIR"] = resourceDirectory.path
        environment["WA_COLLECTOR_REPO_ROOT"] = inferredRepoRoot().path
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if let chromeOwnershipURL {
            environment["WA_COLLECTOR_CHROME_OWNERSHIP_PATH"] = chromeOwnershipURL.path
        }
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

enum BridgeProcessOutput {
    static func waitForExit(_ process: Process, stdout: Pipe, stderr: Pipe, onProgress: (@Sendable (ExportProgress) -> Void)? = nil) -> (Data, Data) {
        let output = PipeReader(handle: stdout.fileHandleForReading)
        let errors = PipeReader(handle: stderr.fileHandleForReading, onProgress: onProgress)
        let readers = DispatchGroup()
        // Drain both pipes while the bridge runs; waiting first can deadlock on a large diagnostic response.
        for reader in [output, errors] {
            readers.enter()
            DispatchQueue.global(qos: .utility).async {
                reader.read()
                readers.leave()
            }
        }
        process.waitUntilExit()
        readers.wait()
        return (output.data, errors.data)
    }

    private final class PipeReader: @unchecked Sendable {
        private let handle: FileHandle
        private let lock = NSLock()
        private var bytes = Data()
        private let onProgress: (@Sendable (ExportProgress) -> Void)?

        init(handle: FileHandle, onProgress: (@Sendable (ExportProgress) -> Void)? = nil) {
            self.handle = handle
            self.onProgress = onProgress
        }

        func read() {
            var parser = BridgeProgressParser()
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                lock.withLock { bytes.append(chunk) }
                if let onProgress { parser.append(chunk, emit: onProgress) }
            }
            if let onProgress { parser.finish(emit: onProgress) }
        }

        var data: Data { lock.withLock { bytes } }
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
