from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

APP_NAME = "WhatsApp Collector"
PRODUCT_NAME = "WhatsAppCollectorNative"
BUNDLE_NAME = f"{APP_NAME}.app"
BUNDLE_IDENTIFIER = "studio.bdjben.whatsapp-collector"
BUNDLE_VERSION = "0.4.2"
SPARKLE_FEED_URL = "https://github.com/bdjben/whatsapp-collector/releases/latest/download/appcast.xml"
SPARKLE_PUBLIC_ED_KEY = "5rau7VI4KCvnHSD4dI1xXTSek9PijJJgOFgsRjcIb58="
DMG_NAME = "WhatsApp-Collector-macOS.dmg"
ZIP_NAME = "WhatsApp-Collector-macOS.zip"
MIN_SYSTEM_VERSION = "14.0"

NATIVE_PACKAGE_DIR = "native-macos"
NATIVE_BRIDGE_PATH = "native-macos/Support/native_bridge.py"
ICON_GENERATOR_PATH = "native-macos/Support/generate_icon.swift"
PYTHON_PACKAGE_PATH = "src/whatsapp_collector"

DEFAULT_APP_OUTPUT_DIR = "~/Documents/WhatsApp Collector/Exports"
DEFAULT_APP_OUTPUT_JSON = f"{DEFAULT_APP_OUTPUT_DIR}/whatsapp-dashboard-export.json"
DEFAULT_APP_PROFILE_DIR = "~/Library/Application Support/WhatsApp Collector/Chrome Profile"
DEFAULT_APP_PORT = 8765


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
    package_dir = project_root / NATIVE_PACKAGE_DIR
    bridge_path = project_root / NATIVE_BRIDGE_PATH
    python_package = project_root / PYTHON_PACKAGE_PATH
    if not package_dir.exists():
        raise FileNotFoundError(f"Native Swift package not found: {package_dir}")
    if not bridge_path.exists():
        raise FileNotFoundError(f"Native bridge not found: {bridge_path}")
    if not python_package.exists():
        raise FileNotFoundError(f"Collector Python package not found: {python_package}")

    app_path = output_dir / BUNDLE_NAME
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    frameworks = contents / "Frameworks"
    resources = contents / "Resources"
    if app_path.exists():
        shutil.rmtree(app_path)
    macos.mkdir(parents=True)
    frameworks.mkdir(parents=True)
    resources.mkdir(parents=True)

    _stage_bridge_resources(project_root, resources)
    _write_info_plist(contents / "Info.plist")

    if compile_app:
        _compile_native_swift(package_dir, macos / PRODUCT_NAME, frameworks)
        _build_native_icon(project_root, resources)
    else:
        (macos / PRODUCT_NAME).write_text("#!/bin/sh\necho 'compile_app=False scaffold'\n")
        (macos / PRODUCT_NAME).chmod(0o755)
        (resources / "AppIcon.icns").write_bytes(b"")
    if sign_app:
        _sign_app(app_path, identity=sign_identity)
    return app_path


def _stage_bridge_resources(project_root: Path, resources: Path) -> None:
    shutil.copy2(project_root / NATIVE_BRIDGE_PATH, resources / "native_bridge.py")
    python_root = resources / "python"
    package_destination = python_root / "whatsapp_collector"
    if package_destination.exists():
        shutil.rmtree(package_destination)
    python_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        project_root / PYTHON_PACKAGE_PATH,
        package_destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _write_info_plist(path: Path) -> None:
    payload = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleVersion": BUNDLE_VERSION,
        "CFBundleShortVersionString": BUNDLE_VERSION,
        "CFBundleExecutable": PRODUCT_NAME,
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": MIN_SYSTEM_VERSION,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "SUEnableAutomaticChecks": True,
        "SUFeedURL": SPARKLE_FEED_URL,
        "SUPublicEDKey": SPARKLE_PUBLIC_ED_KEY,
    }
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)


def _compile_native_swift(package_dir: Path, output: Path, frameworks: Path) -> None:
    subprocess.run(
        ["swift", "build", "--configuration", "release", "--package-path", str(package_dir)],
        check=True,
    )
    completed = subprocess.run(
        ["swift", "build", "--configuration", "release", "--package-path", str(package_dir), "--show-bin-path"],
        check=True,
        capture_output=True,
        text=True,
    )
    binary = Path(completed.stdout.strip()) / PRODUCT_NAME
    if not binary.exists():
        raise FileNotFoundError(f"Swift build did not produce {binary}")
    shutil.copy2(binary, output)
    output.chmod(0o755)
    subprocess.run(["install_name_tool", "-add_rpath", "@executable_path/../Frameworks", str(output)], check=True)
    _copy_sparkle_framework(binary.parent, frameworks)


def _copy_sparkle_framework(build_products_dir: Path, frameworks: Path) -> None:
    source = build_products_dir / "Sparkle.framework"
    if not source.exists():
        source = build_products_dir.parents[1] / "artifacts" / "sparkle" / "Sparkle" / "Sparkle.xcframework" / "macos-arm64_x86_64" / "Sparkle.framework"
    if not source.exists():
        raise FileNotFoundError(f"Swift build did not stage Sparkle.framework near {build_products_dir}")
    destination = frameworks / "Sparkle.framework"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)


def _build_native_icon(project_root: Path, resources: Path) -> None:
    generator = project_root / ICON_GENERATOR_PATH
    if not generator.exists():
        raise FileNotFoundError(f"Icon generator not found: {generator}")
    iconset = resources / "AppIcon.iconset"
    subprocess.run(["swift", str(generator), str(iconset)], check=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "AppIcon.icns")], check=True)
    shutil.rmtree(iconset)


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
        "WhatsApp Collector is now a native macOS app window with a menu bar extra. "
        "It keeps writing the same JSON export file for AI agents and local automations.\n\n"
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
    parser.add_argument(
        "--sign-identity",
        default=os.environ.get("WHATSAPP_COLLECTOR_CODESIGN_IDENTITY"),
        help="Developer ID Application identity for real signing. Defaults to ad-hoc signing when omitted.",
    )
    parser.add_argument("--notary-profile", default=os.environ.get("WHATSAPP_COLLECTOR_NOTARY_PROFILE"), help="notarytool keychain profile for DMG notarization")
    parser.add_argument("--notarize", action="store_true", help="Submit the final DMG to Apple notarization using --notary-profile")
    parser.add_argument("--staple", action="store_true", help="Staple notarization ticket to the final DMG")
    parser.add_argument("--no-dmg", action="store_true", help="Skip DMG creation")
    parser.add_argument("--no-zip", action="store_true", help="Skip ZIP creation")
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
