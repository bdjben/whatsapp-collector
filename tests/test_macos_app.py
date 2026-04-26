from __future__ import annotations

import importlib.util
import plistlib
from pathlib import Path


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
