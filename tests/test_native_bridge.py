from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_native_bridge():
    script = Path(__file__).resolve().parents[1] / "native-macos" / "Support" / "native_bridge.py"
    spec = importlib.util.spec_from_file_location("native_bridge_under_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_schedule_install_persists_browser_control_settings(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    captured: dict[str, Any] = {}

    def fake_install_native_schedule(**kwargs):
        captured.update(kwargs)
        return {"enabled": True, "loaded": True}

    monkeypatch.setattr(bridge, "install_native_schedule", fake_install_native_schedule)
    monkeypatch.setenv("WA_COLLECTOR_NATIVE_RESOURCE_DIR", str(tmp_path / "Resources"))
    monkeypatch.setenv("WA_COLLECTOR_REPO_ROOT", str(tmp_path))

    result = bridge.dispatch(
        "schedule-install",
        {
            "intervalMinutes": 45,
            "outputPath": str(tmp_path / "export.json"),
            "profileDir": str(tmp_path / "profile"),
            "accountLabel": "WhatsApp",
            "maxMessages": 15,
            "maxAllChats": 20,
            "allowLabels": ["Clients"],
            "excludeLabels": ["Groups"],
            "includeGroups": "labeledAlways",
            "debugPort": 19300,
            "markerTitle": "Custom Collector",
            "markerUrlSubstring": "custom-marker",
            "targetUrl": "https://web.whatsapp.com/",
        },
    )

    assert result["ok"] is True
    assert captured["interval_minutes"] == 45
    assert captured["payload"]["debugPort"] == 19300
    assert captured["payload"]["markerTitle"] == "Custom Collector"
    assert captured["payload"]["markerUrlSubstring"] == "custom-marker"
    assert captured["payload"]["targetUrl"] == "https://web.whatsapp.com/"
