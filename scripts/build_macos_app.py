from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import textwrap
from pathlib import Path

APP_NAME = "WhatsApp Collector"
BUNDLE_NAME = f"{APP_NAME}.app"
BUNDLE_IDENTIFIER = "studio.bdjben.whatsapp-collector"
BUNDLE_VERSION = "0.3.6"
PYZ_NAME = "whatsapp-collector.pyz"
DMG_NAME = "WhatsApp-Collector-macOS.dmg"
ZIP_NAME = "WhatsApp-Collector-macOS.zip"
DEFAULT_APP_OUTPUT_DIR = "~/Documents/WhatsApp Collector/Exports"
DEFAULT_APP_OUTPUT_JSON = f"{DEFAULT_APP_OUTPUT_DIR}/whatsapp-dashboard-export.json"
DEFAULT_APP_PROFILE_DIR = "~/Library/Application Support/WhatsApp Collector/Chrome Profile"
DEFAULT_APP_PORT = 8765

SWIFT_SOURCE = r'''
import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var serverProcess: Process?
    private let port = 8765
    private let appName = "WhatsApp Collector"
    private let outputDir = NSString(string: "~/Documents/WhatsApp Collector/Exports").expandingTildeInPath
    private let outputJson = NSString(string: "~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json").expandingTildeInPath
    private let profileDir = NSString(string: "~/Library/Application Support/WhatsApp Collector/Chrome Profile").expandingTildeInPath

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        createKnownFolders()
        configureMenuBar()
        startServer()
    }

    func applicationWillTerminate(_ notification: Notification) {
        serverProcess?.terminate()
    }

    private func configureMenuBar() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "W↗"
        statusItem.button?.toolTip = "WhatsApp Collector"

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Open WhatsApp Collector UI", action: #selector(openUI), keyEquivalent: "o"))
        menu.addItem(NSMenuItem(title: "Show Output Folder", action: #selector(showOutputFolder), keyEquivalent: "f"))
        menu.addItem(NSMenuItem(title: "Copy Output JSON Path", action: #selector(copyOutputPath), keyEquivalent: "c"))
        menu.addItem(NSMenuItem(title: "Copy AI Harness Prompt", action: #selector(copyAIHarnessPrompt), keyEquivalent: "p"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Restart Local UI", action: #selector(restartServer), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Quit WhatsApp Collector", action: #selector(quit), keyEquivalent: "q"))
        for item in menu.items { item.target = self }
        statusItem.menu = menu
    }

    private func createKnownFolders() {
        try? FileManager.default.createDirectory(atPath: outputDir, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(atPath: profileDir, withIntermediateDirectories: true)
        let readme = outputDir + "/README.txt"
        if !FileManager.default.fileExists(atPath: readme) {
            let body = "WhatsApp Collector writes exports here.\n\nMain JSON file:\n" + outputJson + "\n\nOpen the menu bar W↗ icon and choose Show Output Folder to return here.\n"
            try? body.write(toFile: readme, atomically: true, encoding: .utf8)
        }
    }

    private func pythonExecutable() -> String {
        if let override = ProcessInfo.processInfo.environment["WHATSAPP_COLLECTOR_PYTHON"], !override.isEmpty {
            return override
        }
        let candidates = [
            "/opt/homebrew/bin/python3.11",
            "/usr/local/bin/python3.11",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return "/usr/bin/python3"
    }

    private func startServer() {
        guard serverProcess == nil || serverProcess?.isRunning == false else { return }
        guard let resourcePath = Bundle.main.resourcePath else { return }
        let pyz = resourcePath + "/whatsapp-collector.pyz"
        createKnownFolders()

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonExecutable())
        process.arguments = [pyz, "ui", "--host", "127.0.0.1", "--port", String(port), "--output", outputJson, "--profile-dir", profileDir]
        process.currentDirectoryURL = URL(fileURLWithPath: NSHomeDirectory())
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                self?.statusItem.button?.title = "W!"
                self?.serverProcess = nil
            }
        }
        do {
            try process.run()
            serverProcess = process
            statusItem.button?.title = "W↗"
        } catch {
            showAlert(title: "Could not start WhatsApp Collector", message: "Install Python 3.11 or set WHATSAPP_COLLECTOR_PYTHON to a compatible Python.\n\n" + error.localizedDescription)
        }
    }

    @objc private func openUI() {
        startServer()
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(port)/")!)
    }

    @objc private func showOutputFolder() {
        createKnownFolders()
        NSWorkspace.shared.open(URL(fileURLWithPath: outputDir, isDirectory: true))
    }

    @objc private func copyOutputPath() {
        copyToClipboard(outputJson)
    }

    @objc private func copyAIHarnessPrompt() {
        let prompt = """
My most recent WhatsApp Collector export is at:
\(outputJson)

It is updated regularly. Treat this JSON file as a read-only local resource when answering questions about my WhatsApp conversations. You need local filesystem access to this path; if you cannot read local files directly, ask me to upload the JSON. If you need current WhatsApp context, read this file first, use its account metadata and threads/messages as source data, and cite that the information came from the local WhatsApp Collector export. Do not send messages or modify WhatsApp from this file.
"""
        copyToClipboard(prompt)
    }

    private func copyToClipboard(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }

    @objc private func restartServer() {
        serverProcess?.terminate()
        serverProcess = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.startServer() }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func showAlert(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
'''

ICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#06150c"/><stop offset="1" stop-color="#0b1220"/></linearGradient>
    <linearGradient id="fg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#25D366"/><stop offset="1" stop-color="#8AB4FF"/></linearGradient>
  </defs>
  <rect x="64" y="64" width="896" height="896" rx="210" fill="url(#bg)"/>
  <path d="M210 318 L306 706 L410 396 L512 706 L612 318" fill="none" stroke="url(#fg)" stroke-width="86" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M620 622 L812 430" fill="none" stroke="#8AB4FF" stroke-width="72" stroke-linecap="round"/>
  <path d="M694 402 H840 V548" fill="none" stroke="#8AB4FF" stroke-width="72" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
'''


def build_macos_app(
    project_root: Path,
    output_dir: Path,
    *,
    compile_app: bool = True,
    sign_app: bool = True,
    sign_identity: str | None = None,
) -> Path:
    project_root = project_root.resolve()
    output_dir = output_dir.resolve()
    pyz_path = project_root / "dist" / PYZ_NAME
    if not pyz_path.exists():
        raise FileNotFoundError(f"Build the zipapp first: {pyz_path}")

    app_path = output_dir / BUNDLE_NAME
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    if app_path.exists():
        shutil.rmtree(app_path)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    shutil.copy2(pyz_path, resources / PYZ_NAME)
    (resources / "WhatsAppCollectorMenu.swift").write_text(SWIFT_SOURCE)
    (resources / "WhatsAppCollectorIcon.svg").write_text(ICON_SVG)
    _write_info_plist(contents / "Info.plist")

    if compile_app:
        _compile_swift(resources / "WhatsAppCollectorMenu.swift", macos / APP_NAME)
        _build_icon(resources)
    else:
        (macos / APP_NAME).write_text("#!/bin/sh\necho 'compile_app=False scaffold'\n")
        (macos / APP_NAME).chmod(0o755)
    if sign_app:
        _sign_app(app_path, identity=sign_identity)
    return app_path


def _write_info_plist(path: Path) -> None:
    payload = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleVersion": BUNDLE_VERSION,
        "CFBundleShortVersionString": BUNDLE_VERSION,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIconFile": "WhatsAppCollector",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)


def _compile_swift(source: Path, output: Path) -> None:
    subprocess.run(
        ["swiftc", str(source), "-o", str(output), "-framework", "AppKit"],
        check=True,
    )


def _build_icon(resources: Path) -> None:
    svg = resources / "WhatsAppCollectorIcon.svg"
    base_png = resources / "WhatsAppCollectorIcon.png"
    iconset = resources / "WhatsAppCollector.iconset"
    iconset.mkdir(exist_ok=True)
    subprocess.run(["sips", "-s", "format", "png", str(svg), "--out", str(base_png)], check=True, stdout=subprocess.DEVNULL)
    sizes = [16, 32, 128, 256, 512]
    for size in sizes:
        subprocess.run(["sips", "-z", str(size), str(size), str(base_png), "--out", str(iconset / f"icon_{size}x{size}.png")], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["sips", "-z", str(size * 2), str(size * 2), str(base_png), "--out", str(iconset / f"icon_{size}x{size}@2x.png")], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "WhatsAppCollector.icns")], check=True)


def _sign_app(app_path: Path, *, identity: str | None = None) -> None:
    if identity:
        command = [
            "codesign",
            "--force",
            "--deep",
            "--options",
            "runtime",
            "--sign",
            identity,
            "--timestamp",
            str(app_path),
        ]
    else:
        command = ["codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", str(app_path)]
    subprocess.run(command, check=True)


def _sign_dmg(dmg_path: Path, *, identity: str) -> None:
    subprocess.run(["codesign", "--force", "--sign", identity, "--timestamp", str(dmg_path)], check=True)


def _notarize_dmg(dmg_path: Path, *, keychain_profile: str) -> None:
    subprocess.run(
        ["xcrun", "notarytool", "submit", str(dmg_path), "--keychain-profile", keychain_profile, "--wait"],
        check=True,
    )


def _staple_dmg(dmg_path: Path) -> None:
    subprocess.run(["xcrun", "stapler", "staple", str(dmg_path)], check=True)


def build_zip(app_path: Path, output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    zip_path = output_dir / ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    subprocess.run(
        ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_path), str(zip_path)],
        check=True,
    )
    return zip_path


def build_dmg(
    app_path: Path,
    output_dir: Path,
    *,
    sign_identity: str | None = None,
    notary_profile: str | None = None,
    staple: bool = False,
) -> Path:
    output_dir = output_dir.resolve()
    staging = output_dir / "dmg-staging"
    dmg_path = output_dir / DMG_NAME
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    staged_app = staging / BUNDLE_NAME
    shutil.copytree(app_path, staged_app, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    readme = staging / "README.txt"
    readme.write_text(
        "Drag WhatsApp Collector.app onto the Applications shortcut.\n\n"
        "If macOS still blocks first launch, right-click WhatsApp Collector.app in Applications, choose Open, "
        "and confirm once. Developer ID signed and notarized releases should open without the unidentified-developer warning.\n"
    )
    if dmg_path.exists():
        dmg_path.unlink()
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            "-o",
            str(dmg_path),
        ],
        check=True,
    )
    if sign_identity:
        _sign_dmg(dmg_path, identity=sign_identity)
    if notary_profile:
        _notarize_dmg(dmg_path, keychain_profile=notary_profile)
    if staple:
        _staple_dmg(dmg_path)
    return dmg_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WhatsApp Collector.app for macOS")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--no-compile", action="store_true", help="Create bundle scaffold without compiling Swift or generating icns")
    parser.add_argument("--no-sign", action="store_true", help="Skip code signing")
    parser.add_argument("--sign-identity", default=os.environ.get("WHATSAPP_COLLECTOR_CODESIGN_IDENTITY"), help="Developer ID Application identity for real signing. Defaults to ad-hoc signing when omitted.")
    parser.add_argument("--notary-profile", default=os.environ.get("WHATSAPP_COLLECTOR_NOTARY_PROFILE"), help="notarytool keychain profile for DMG notarization")
    parser.add_argument("--notarize", action="store_true", help="Submit the final DMG to Apple notarization using --notary-profile")
    parser.add_argument("--staple", action="store_true", help="Staple notarization ticket to the final DMG")
    parser.add_argument("--no-dmg", action="store_true", help="Skip DMG creation")
    parser.add_argument("--no-zip", action="store_true", help="Skip legacy ZIP creation")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    if args.notarize and not args.notary_profile:
        parser.error("--notarize requires --notary-profile or WHATSAPP_COLLECTOR_NOTARY_PROFILE")
    if args.notarize and not args.sign_identity:
        parser.error("--notarize requires --sign-identity or WHATSAPP_COLLECTOR_CODESIGN_IDENTITY")
    app = build_macos_app(
        Path(args.project_root),
        output_dir,
        compile_app=not args.no_compile,
        sign_app=not args.no_sign,
        sign_identity=args.sign_identity,
    )
    print(app)
    if not args.no_zip:
        print(build_zip(app, output_dir))
    if not args.no_dmg:
        print(build_dmg(app, output_dir, sign_identity=args.sign_identity, notary_profile=(args.notary_profile if args.notarize else None), staple=args.staple or args.notarize))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
