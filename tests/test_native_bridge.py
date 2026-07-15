from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


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
            "downloadAttachments": False,
            "attachmentStorageLimitBytes": 2_500_000_000,
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
    assert captured["payload"]["downloadAttachments"] is False
    assert captured["payload"]["attachmentStorageLimitBytes"] == 2_500_000_000


def test_native_bridge_uses_backward_compatible_attachment_defaults() -> None:
    bridge = _load_native_bridge()

    config = bridge._config({})

    assert config["download_attachments"] is True
    assert config["attachment_storage_limit_bytes"] == 1_500_000_000


def _good_export() -> dict[str, Any]:
    return {
        "source": "whatsapp",
        "exportedAt": "2026-06-24T20:00:00+00:00",
        "maxAllViewChats": 30,
        "threads": [
            {
                "threadKey": "good",
                "chatTitle": "Good Thread",
                "sourceView": "all",
                "recentMessages": [
                    {
                        "messageId": "m1",
                        "timestamp": "2026-06-24T20:00:00+00:00",
                        "direction": "inbound",
                        "sender": "sender",
                        "text": "Readable text",
                        "textAvailable": True,
                        "messageType": "chat",
                        "subtype": None,
                    }
                ],
                "messages": [],
            }
        ],
    }


def _degraded_export() -> dict[str, Any]:
    return {
        "source": "whatsapp",
        "exportedAt": "2026-06-24T20:01:00+00:00",
        "maxAllViewChats": 30,
        "threads": [
            {
                "threadKey": f"bad-{index}",
                "chatTitle": f"Bad Thread {index}",
                "sourceView": "indexeddb-recent",
                "recentMessages": [
                    {
                        "messageId": f"m{index}",
                        "timestamp": "2026-06-24T20:01:00+00:00",
                        "direction": "inbound",
                        "sender": "sender",
                        "text": None,
                        "textAvailable": False,
                        "messageType": "image",
                        "subtype": None,
                    }
                ],
                "messages": [],
            }
            for index in range(3)
        ],
    }


def test_native_run_export_retries_then_writes_when_quality_recovers(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    output = tmp_path / "export.json"
    calls = {"count": 0}

    class FakeDevToolsBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_whatsapp_ready(self, **kwargs):
            return {"ready": True}

    captured_kwargs: list[dict[str, Any]] = []

    class FakeCollector:
        def collect_dashboard_export(self, **kwargs):
            captured_kwargs.append(kwargs)
            calls["count"] += 1
            return _degraded_export() if calls["count"] == 1 else _good_export()

    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "_collector", lambda cfg: FakeCollector())
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        bridge,
        "terminate_profile_processes",
        lambda *args, **kwargs: {"matchedProcessIds": [20518], "forcedProcessIds": [], "remainingProcessIds": []},
    )

    result = bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert result["ok"] is True
    assert result["threadCount"] == 1
    assert calls["count"] == 2
    assert captured_kwargs[-1]["download_attachments"] is True
    assert captured_kwargs[-1]["max_total_attachment_bytes"] == 1_500_000_000
    assert json.loads(output.read_text())["threads"][0]["chatTitle"] == "Good Thread"
    assert result["window"]["closedAfterExport"] is True


def test_native_close_window_requires_profile_port_and_captured_pids(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    captured: dict[str, Any] = {}

    def fake_terminate(profile_dir, **kwargs):
        captured["profileDir"] = profile_dir
        captured.update(kwargs)
        return {"matchedProcessIds": [20518], "forcedProcessIds": [], "remainingProcessIds": []}

    monkeypatch.setattr(bridge, "terminate_profile_processes", fake_terminate)

    result = bridge.dispatch(
        "close-window",
        {
            "profileDir": str(tmp_path / "Chrome Profile"),
            "debugPort": 19220,
            "expectedChromeProcessIds": [20518, "20519", "invalid"],
        },
    )

    assert result["ok"] is True
    assert result["window"]["closed"] is True
    assert captured["profileDir"] == tmp_path / "Chrome Profile"
    assert captured["debug_port"] == 19220
    assert captured["expected_pids"] == {20518, 20519}


def test_native_run_export_rejects_degraded_export_and_restores_last_good(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    output = tmp_path / "export.json"
    output.write_text(json.dumps(_degraded_export()))
    backup = tmp_path / "backup"
    backup.mkdir()
    last_good = backup / "export.20260624-200000.json"
    last_good.write_text(json.dumps(_good_export()))
    calls = {"count": 0}

    class FakeCollector:
        def collect_dashboard_export(self, **kwargs):
            calls["count"] += 1
            return _degraded_export()

    class FakeDevToolsBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_whatsapp_ready(self, **kwargs):
            return {"ready": True}

    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "_collector", lambda cfg: FakeCollector())
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: None)

    with pytest.raises(bridge.ExportQualityError) as exc_info:
        bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert calls["count"] == 3
    assert exc_info.value.report["restoredLastGood"] == str(last_good)
    assert json.loads(output.read_text())["threads"][0]["chatTitle"] == "Good Thread"
