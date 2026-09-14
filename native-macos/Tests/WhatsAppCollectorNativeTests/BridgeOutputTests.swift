import Foundation
import Testing
@testable import WhatsAppCollectorNative

@Test func largeBridgeOutputDrainsBothPipesWithoutDeadlocking() throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/sh")
    process.arguments = ["-c", "/usr/bin/head -c 262144 /dev/zero; /usr/bin/head -c 262144 /dev/zero >&2"]
    let stdout = Pipe()
    let stderr = Pipe()
    process.standardOutput = stdout
    process.standardError = stderr
    try process.run()

    let (output, errors) = BridgeProcessOutput.waitForExit(process, stdout: stdout, stderr: stderr)

    #expect(process.terminationStatus == 0)
    #expect(output.count == 262144)
    #expect(errors.count == 262144)
}
