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


def test_macos_app_builder_creates_menu_bar_bundle_scaffold(tmp_path: Path) -> None:
    builder = _load_builder()
    project = tmp_path / "project"
    dist = tmp_path / "dist"
    (project / "dist").mkdir(parents=True)
    (project / "dist" / "whatsapp-collector.pyz").write_text("#!/usr/bin/env python3\n")

    app_path = builder.build_macos_app(project, dist, compile_app=False)

    assert app_path.name == "WhatsApp Collector.app"
    info = plistlib.loads((app_path / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "WhatsApp Collector"
    assert info["LSUIElement"] is True
    assert info["CFBundleIconFile"] == "WhatsAppCollector"
    assert (app_path / "Contents" / "Resources" / "whatsapp-collector.pyz").exists()
    assert (app_path / "Contents" / "Resources" / "WhatsAppCollectorIcon.svg").exists()
    swift_source = (app_path / "Contents" / "Resources" / "WhatsAppCollectorMenu.swift").read_text()
    assert "NSStatusBar.system.statusItem" in swift_source
    assert "Open WhatsApp Collector UI" in swift_source
    assert "Show Output Folder" in swift_source
    assert "Copy Output JSON Path" in swift_source
    assert "Copy AI Harness Prompt" in swift_source
    assert "most recent WhatsApp Collector export is at" in swift_source
    assert "~/Documents/WhatsApp Collector/Exports" in swift_source
    assert "W↗" in swift_source


def test_macos_app_builder_uses_accessible_document_output_folder() -> None:
    builder = _load_builder()

    assert builder.DEFAULT_APP_OUTPUT_DIR == "~/Documents/WhatsApp Collector/Exports"
    assert builder.DEFAULT_APP_OUTPUT_JSON == "~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json"
    assert builder.DEFAULT_APP_PROFILE_DIR == "~/Library/Application Support/WhatsApp Collector/Chrome Profile"
    assert builder.DMG_NAME == "WhatsApp-Collector-macOS.dmg"


def test_macos_app_builder_signs_bundle_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _load_builder()
    project = tmp_path / "project"
    dist = tmp_path / "dist"
    (project / "dist").mkdir(parents=True)
    (project / "dist" / "whatsapp-collector.pyz").write_text("#!/usr/bin/env python3\n")
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
