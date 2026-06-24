from __future__ import annotations

import importlib.util
import plistlib
from pathlib import Path

import pytest


def _load_builder():
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_macos_app.py"
    spec = importlib.util.spec_from_file_location("build_macos_app", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "native-macos" / "Support").mkdir(parents=True)
    (project / "native-macos" / "Package.swift").write_text("// swift package marker\n")
    (project / "native-macos" / "Support" / "native_bridge.py").write_text("#!/usr/bin/env python3\n")
    (project / "native-macos" / "Support" / "generate_icon.swift").write_text("// icon generator marker\n")
    (project / "src" / "whatsapp_collector").mkdir(parents=True)
    (project / "src" / "whatsapp_collector" / "__init__.py").write_text("")
    (project / "src" / "whatsapp_collector" / "collector.py").write_text("# collector marker\n")
    return project


def test_macos_app_builder_creates_native_bundle_scaffold(tmp_path: Path) -> None:
    builder = _load_builder()
    project = _make_project(tmp_path)
    dist = tmp_path / "dist"

    app_path = builder.build_macos_app(project, dist, compile_app=False, sign_app=False)

    assert app_path.name == "WhatsApp Collector.app"
    info = plistlib.loads((app_path / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "WhatsApp Collector"
    assert info["CFBundleExecutable"] == "WhatsAppCollectorNative"
    assert info["CFBundleIconFile"] == "AppIcon"
    assert info["LSMinimumSystemVersion"] == "14.0"
    assert "LSUIElement" not in info
    assert info["SUFeedURL"] == "https://github.com/bdjben/whatsapp-collector/releases/latest/download/appcast.xml"
    assert info["SUPublicEDKey"] == "5rau7VI4KCvnHSD4dI1xXTSek9PijJJgOFgsRjcIb58="
    assert info["SUEnableAutomaticChecks"] is True
    assert (app_path / "Contents" / "Resources" / "native_bridge.py").exists()
    assert (app_path / "Contents" / "Resources" / "python" / "whatsapp_collector" / "collector.py").exists()
    assert not (app_path / "Contents" / "Resources" / "whatsapp-collector.pyz").exists()


def test_macos_app_builder_uses_accessible_document_output_folder() -> None:
    builder = _load_builder()

    assert builder.DEFAULT_APP_OUTPUT_DIR == "~/Documents/WhatsApp Collector/Exports"
    assert builder.DEFAULT_APP_OUTPUT_JSON == "~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json"
    assert builder.DEFAULT_APP_PROFILE_DIR == "~/Library/Application Support/WhatsApp Collector/Chrome Profile"
    assert builder.DMG_NAME == "WhatsApp-Collector-macOS.dmg"
    assert builder.SPARKLE_FEED_URL.endswith("/appcast.xml")


def test_macos_app_builder_signs_bundle_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _load_builder()
    project = _make_project(tmp_path)
    dist = tmp_path / "dist"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return object()

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    app_path = builder.build_macos_app(project, dist, compile_app=False)

    assert app_path.name == "WhatsApp Collector.app"
    assert any(
        call[:6] == ["codesign", "--force", "--deep", "--sign", "-", "--timestamp=none"]
        and call[-1] == str(app_path)
        for call in calls
    )


def test_macos_app_builder_can_use_developer_id_identity_with_hardened_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _load_builder()
    project = _make_project(tmp_path)
    dist = tmp_path / "dist"
    identity = "Developer ID Application: Example LLC (ABCDE12345)"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return object()

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    app_path = builder.build_macos_app(project, dist, compile_app=False, sign_identity=identity)

    assert [
        "codesign",
        "--force",
        "--deep",
        "--options",
        "runtime",
        "--sign",
        identity,
        "--timestamp",
        str(app_path),
    ] in calls


def test_macos_dmg_builder_can_sign_notarize_and_staple_final_dmg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _load_builder()
    app = tmp_path / "WhatsApp Collector.app"
    app.mkdir()
    dist = tmp_path / "dist"
    identity = "Developer ID Application: Example LLC (ABCDE12345)"
    profile = "whatsapp-collector-notary"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        if command[0] == "hdiutil" and "-o" in command:
            output = Path(command[command.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake dmg")
        return object()

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    dmg_path = builder.build_dmg(app, dist, sign_identity=identity, notary_profile=profile, staple=True)

    assert dmg_path.name == "WhatsApp-Collector-macOS.dmg"
    assert ["codesign", "--force", "--sign", identity, "--timestamp", str(dmg_path)] in calls
    assert ["xcrun", "notarytool", "submit", str(dmg_path), "--keychain-profile", profile, "--wait"] in calls
    assert ["xcrun", "stapler", "staple", str(dmg_path)] in calls


def test_macos_dmg_builder_creates_drag_to_applications_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _load_builder()
    app = tmp_path / "WhatsApp Collector.app"
    app.mkdir()
    dist = tmp_path / "dist"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        if command[0] == "hdiutil" and "-o" in command:
            output = Path(command[command.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake dmg")
        return object()

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    dmg_path = builder.build_dmg(app, dist)

    assert dmg_path.name == "WhatsApp-Collector-macOS.dmg"
    assert dmg_path.exists()
    staging = tmp_path / "dist" / "dmg-staging"
    assert (staging / "WhatsApp Collector.app").exists()
    assert (staging / "Applications").is_symlink()
    hdiutil_call = next(call for call in calls if call[:2] == ["hdiutil", "create"])
    assert "-volname" in hdiutil_call
    assert "WhatsApp Collector" in hdiutil_call


def test_native_app_source_has_help_cleanup_and_single_window_guardrails() -> None:
    project = Path(__file__).resolve().parents[1]
    app_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "App" / "WhatsAppCollectorNativeApp.swift").read_text()
    section_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Models" / "AppSection.swift").read_text()
    update_models_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Models" / "UpdateModels.swift").read_text()
    update_monitor_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Stores" / "UpdateMonitor.swift").read_text()
    update_service_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Services" / "UpdateAvailabilityService.swift").read_text()
    content_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "ContentView.swift").read_text()
    dashboard_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "DashboardView.swift").read_text()
    labels_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "LabelsView.swift").read_text()
    export_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "ExportPreviewView.swift").read_text()
    automation_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "AutomationView.swift").read_text()
    prompt_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "AIPromptWindow.swift").read_text()
    components_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "Components.swift").read_text()
    help_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Views" / "HelpView.swift").read_text()
    migration_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Support" / "LegacyAppMigration.swift").read_text()
    store_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Stores" / "CollectorStore.swift").read_text()
    models_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Models" / "CollectorModels.swift").read_text()
    login_item_source = (project / "native-macos" / "Sources" / "WhatsAppCollectorNative" / "Support" / "LoginItemManager.swift").read_text()

    assert 'MenuBarExtra("W↗")' in app_source
    assert "@StateObject private var updateMonitor = UpdateMonitor()" in app_source
    assert "updateMonitor.startAutomaticChecks()" in app_source
    assert "Task { await updateMonitor.checkNow(trigger: .manual) }" in app_source
    assert "Update Available:" in app_source
    assert "UpdateStatusBanner" in content_source
    assert "automaticCheckIntervalSeconds: UInt64 = 15 * 60" in update_monitor_source
    assert "AppMetadata.appcastURL" in update_monitor_source
    assert "URLSession.shared.data" in update_service_source
    assert "VersionComparator.isVersion" in update_models_source
    assert "MenuBarStatusLabel" not in app_source
    assert "TimelineView(.periodic" not in app_source
    assert "exportActivityIsVisible" in app_source
    assert "Scheduled Export Running" in store_source
    assert "refreshScheduleStatusQuietly" in store_source
    assert "currentRunActive" in models_source
    assert "launchctlState" in models_source
    assert "scheduledRunActive" in components_source
    assert "NSWindow.allowsAutomaticWindowTabbing = false" in app_source
    assert "closeDuplicateMainWindows()" in app_source
    assert 'case .dashboard: "WhatsApp Collector"' in section_source
    assert 'title: "WhatsApp Collector"' in dashboard_source
    assert "Chrome window display" in dashboard_source
    assert "Optional macOS display name" in dashboard_source
    assert "particular monitor, such as LED TV" in dashboard_source
    assert "After a successful export, WhatsApp Collector closes only its dedicated Chrome profile" in dashboard_source
    assert "Label rules are optional" in labels_source
    assert "Optional. Most users can leave labels alone" in labels_source
    assert "Native macOS app" not in dashboard_source
    assert dashboard_source.index('Label("Load Labels"') < dashboard_source.index('Label("Run Export"')
    assert 'Label("Open Export Preview"' in dashboard_source
    assert "Updates and Help" not in dashboard_source
    assert dashboard_source.index("collectionSettings") < dashboard_source.index("browserReadiness")
    assert dashboard_source.index("chromeProfile") < dashboard_source.index("files")
    assert "deleteAction" in components_source
    assert "removeLabel" in store_source
    assert "Messages Skipped: \\(skipped) - click for details" in export_source
    assert "Source Diagnostics" in export_source
    assert "sourceDiagnostics" in models_source
    assert "DisclosureGroup" not in export_source
    assert "warningDetailsExpanded.toggle()" in export_source
    assert ".frame(maxHeight: 160)" in export_source
    assert "warningDetailsText(warnings)" in export_source
    assert "Current scheduler state" in automation_source
    assert "View/Copy AI Prompt" in app_source
    assert "View/Copy AI Prompt" in export_source
    assert "Window(\"AI Prompt\", id: \"ai-prompt\")" in app_source
    assert "Temporary Prompt Editing" in prompt_source
    assert "Close Without Saving" in prompt_source
    assert "SMAppService.mainApp" in login_item_source
    assert "Launch at Login" in automation_source
    assert "case help" in section_source
    assert "Older App Cleanup" in help_source
    assert "Optional Label Rules" in help_source
    assert "Label rules are optional" in help_source
    assert "After a successful export, the app closes only its dedicated Chrome profile" in help_source
    assert "localhost web UI" not in help_source
    assert "Native schedules call the bundled bridge" not in help_source
    assert "whatsapp-collector.pyz" in migration_source
    assert "WhatsAppCollectorMenu.swift" in migration_source
    assert "LSUIElement" in migration_source
    assert "exportsFolderHasContent" in migration_source
    assert "trashItem" in migration_source
    assert "legacy-app-" in migration_source
    assert store_source.index('alert.addButton(withTitle: "Not Now")') < store_source.index('"Back Up Exports and Move Old App to Trash"')
    assert "alertSecondButtonReturn" in store_source
    bridge_source = (project / "native-macos" / "Support" / "native_bridge.py").read_text()
    assert "terminate_profile_processes(cfg[\"profile_dir\"]" in bridge_source
    assert "\"closedAfterExport\": True" in bridge_source
