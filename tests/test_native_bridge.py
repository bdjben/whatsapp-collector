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
    ownership_path = tmp_path / "chrome-ownership.json"
    ownership_path.write_text(json.dumps({"profileDir": "original"}))
    calls = {"count": 0}
    ensure_calls: list[dict[str, Any]] = []
    termination: dict[str, Any] = {}

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

    def fake_ensure_window(cfg):
        ensure_calls.append(cfg)
        return {
            "ok": True,
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "chromeProcessIds": [20518],
                "launched": True,
            },
        }

    def fake_terminate(profile_dir, **kwargs):
        termination["profileDir"] = profile_dir
        termination.update(kwargs)
        return {"matchedProcessIds": [20518], "forcedProcessIds": [], "remainingProcessIds": []}

    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "_collector", lambda cfg: FakeCollector())
    monkeypatch.setattr(bridge, "_ensure_window", fake_ensure_window)
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(bridge, "terminate_profile_processes", fake_terminate)
    monkeypatch.setenv(bridge.CHROME_OWNERSHIP_PATH_ENV, str(ownership_path))

    result = bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert result["ok"] is True
    assert result["threadCount"] == 1
    assert calls["count"] == 2
    assert captured_kwargs[-1]["download_attachments"] is True
    assert captured_kwargs[-1]["max_total_attachment_bytes"] == 1_500_000_000
    assert json.loads(output.read_text())["threads"][0]["chatTitle"] == "Good Thread"
    assert ensure_calls[0]["profile_dir"] == tmp_path / "profile"
    assert termination["profileDir"] == tmp_path / "profile"
    assert termination["debug_port"] == 19220
    assert termination["expected_pids"] == {20518}
    assert result["window"]["chromeProcessIds"] == [20518]
    assert result["window"]["launchedForExport"] is True
    assert result["window"]["closedAfterExport"] is True
    ownership = json.loads(ownership_path.read_text())
    assert ownership["profileDir"] == str(tmp_path / "profile")
    assert ownership["debugPort"] == 19220
    assert ownership["expectedChromeProcessIds"] == [20518]


def test_native_run_export_relaunches_dedicated_profile_once_when_owned_chrome_exits(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    output = tmp_path / "export.json"
    ownership_path = tmp_path / "chrome-ownership.json"
    ownership_path.write_text("{}")
    ensure_pids = iter([20518, 20519])
    readiness_calls = {"count": 0}
    termination: dict[str, Any] = {}

    def fake_ensure_window(cfg):
        pid = next(ensure_pids)
        return {
            "ok": True,
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "chromeProcessIds": [pid],
                "launched": True,
            },
        }

    class FakeDevToolsBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_whatsapp_ready(self, **kwargs):
            readiness_calls["count"] += 1
            if readiness_calls["count"] == 1:
                raise RuntimeError("Chrome DevTools request failed: connection refused")
            return {"ready": True}

    class FakeCollector:
        def collect_dashboard_export(self, **kwargs):
            return _good_export()

    def fake_terminate(profile_dir, **kwargs):
        termination["profileDir"] = profile_dir
        termination.update(kwargs)
        return {"matchedProcessIds": [20519], "forcedProcessIds": [], "remainingProcessIds": []}

    monkeypatch.setattr(bridge, "_ensure_window", fake_ensure_window)
    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "chrome_profile_process_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(bridge, "_collector", lambda cfg: FakeCollector())
    monkeypatch.setattr(bridge, "terminate_profile_processes", fake_terminate)
    monkeypatch.setenv(bridge.CHROME_OWNERSHIP_PATH_ENV, str(ownership_path))

    result = bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert result["ok"] is True
    assert readiness_calls["count"] == 2
    assert termination["expected_pids"] == {20519}
    assert json.loads(ownership_path.read_text())["expectedChromeProcessIds"] == [20519]


def test_native_run_export_refuses_unidentified_chrome_process(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    monkeypatch.setattr(
        bridge,
        "_ensure_window",
        lambda cfg: {"ok": True, "window": {"profileDir": str(cfg["profile_dir"]), "chromeProcessIds": []}},
    )

    with pytest.raises(RuntimeError, match="could not identify its exact Chrome process"):
        bridge.dispatch("run-export", {"outputPath": str(tmp_path / "export.json"), "profileDir": str(tmp_path / "profile")})


def test_native_run_export_records_exact_process_when_window_setup_fails(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    ownership_path = tmp_path / "chrome-ownership.json"
    ownership_path.write_text("{}")

    def fake_ensure_window(cfg):
        raise RuntimeError("placement failed")

    monkeypatch.setenv(bridge.CHROME_OWNERSHIP_PATH_ENV, str(ownership_path))
    monkeypatch.setattr(bridge, "_ensure_window", fake_ensure_window)
    monkeypatch.setattr(bridge, "chrome_profile_process_ids", lambda *args, **kwargs: [20518])

    with pytest.raises(RuntimeError, match="Could not open the dedicated Chrome profile"):
        bridge.dispatch("run-export", {"outputPath": str(tmp_path / "export.json"), "profileDir": str(tmp_path / "profile")})

    ownership = json.loads(ownership_path.read_text())
    assert ownership["profileDir"] == str(tmp_path / "profile")
    assert ownership["debugPort"] == 19220
    assert ownership["expectedChromeProcessIds"] == [20518]


def test_native_run_export_keeps_owned_window_for_login_instead_of_relaunching(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    ensure_calls = {"count": 0}

    def fake_ensure_window(cfg):
        ensure_calls["count"] += 1
        return {
            "ok": True,
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "chromeProcessIds": [20518],
                "launched": True,
            },
        }

    class FakeDevToolsBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_whatsapp_ready(self, **kwargs):
            raise RuntimeError("WhatsApp Web is not logged in; scan the QR code in the dedicated Chrome profile before exporting.")

    monkeypatch.setattr(bridge, "_ensure_window", fake_ensure_window)
    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "chrome_profile_process_ids", lambda *args, **kwargs: [20518])

    with pytest.raises(RuntimeError, match="not logged in"):
        bridge.dispatch("run-export", {"outputPath": str(tmp_path / "export.json"), "profileDir": str(tmp_path / "profile")})

    assert ensure_calls["count"] == 1


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


def test_native_close_window_targets_nothing_without_captured_pids(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    monkeypatch.setattr(
        bridge,
        "terminate_profile_processes",
        lambda *args, **kwargs: pytest.fail("cleanup must not target Chrome without captured process IDs"),
    )

    result = bridge.dispatch(
        "close-window",
        {"profileDir": str(tmp_path / "Chrome Profile"), "debugPort": 19220},
    )

    assert result["ok"] is True
    assert result["window"]["closeAttempted"] is False
    assert result["window"]["expectedChromeProcessIds"] == []


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
    monkeypatch.setattr(
        bridge,
        "_ensure_window",
        lambda cfg: {
            "ok": True,
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "chromeProcessIds": [20518],
                "launched": False,
            },
        },
    )
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: None)

    with pytest.raises(bridge.ExportQualityError) as exc_info:
        bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert calls["count"] == 3
    assert exc_info.value.report["restoredLastGood"] == str(last_good)
    assert json.loads(output.read_text())["threads"][0]["chatTitle"] == "Good Thread"


def test_native_quality_failure_does_not_roll_valid_current_back_to_older_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bridge = _load_native_bridge()
    output = tmp_path / "export.json"
    current = _good_export()
    current["threads"][0]["chatTitle"] = "Current Newest"
    output.write_text(json.dumps(current))
    original_bytes = output.read_bytes()
    original_mtime = output.stat().st_mtime_ns
    backup = tmp_path / "backup"
    backup.mkdir()
    older = _good_export()
    older["threads"][0]["chatTitle"] = "Older Backup"
    (backup / "export.20260624-200000.json").write_text(json.dumps(older))

    class FakeCollector:
        def collect_dashboard_export(self, **kwargs):
            return _degraded_export()

    class FakeDevToolsBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_whatsapp_ready(self, **kwargs):
            return {"ready": True}

    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "_collector", lambda cfg: FakeCollector())
    monkeypatch.setattr(
        bridge,
        "_ensure_window",
        lambda cfg: {
            "ok": True,
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "chromeProcessIds": [20518],
                "launched": False,
            },
        },
    )
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: None)

    with pytest.raises(bridge.ExportQualityError) as exc_info:
        bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert output.read_bytes() == original_bytes
    assert output.stat().st_mtime_ns == original_mtime
    assert exc_info.value.report["exportRecovery"]["status"] == "retained-current"
    assert "restoredLastGood" not in exc_info.value.report
    assert json.loads(output.read_text())["threads"][0]["chatTitle"] == "Current Newest"


def test_native_readiness_failure_retains_valid_current_export(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    output = tmp_path / "export.json"
    output.write_text(json.dumps(_good_export()))
    original_bytes = output.read_bytes()

    class FakeDevToolsBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_whatsapp_ready(self, **kwargs):
            raise RuntimeError("simulated readiness timeout")

    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "chrome_profile_process_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        bridge,
        "_ensure_window",
        lambda cfg: {
            "ok": True,
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "chromeProcessIds": [20518],
                "launched": True,
            },
        },
    )

    with pytest.raises(RuntimeError, match="simulated readiness timeout") as exc_info:
        bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert output.read_bytes() == original_bytes
    assert exc_info.value.export_recovery["status"] == "retained-current"
    assert not (tmp_path / "backup").exists()


def test_native_collection_failure_restores_backup_when_current_is_missing(tmp_path: Path, monkeypatch) -> None:
    bridge = _load_native_bridge()
    output = tmp_path / "export.json"
    last_good = tmp_path / "backup" / "export.20260624-200000.json"
    last_good.parent.mkdir()
    last_good.write_text(json.dumps(_good_export()))

    class FakeCollector:
        def collect_dashboard_export(self, **kwargs):
            raise RuntimeError("simulated collection failure")

    class FakeDevToolsBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_whatsapp_ready(self, **kwargs):
            return {"ready": True}

    monkeypatch.setattr(bridge, "ChromeDevToolsBridge", FakeDevToolsBridge)
    monkeypatch.setattr(bridge, "_collector", lambda cfg: FakeCollector())
    monkeypatch.setattr(
        bridge,
        "_ensure_window",
        lambda cfg: {
            "ok": True,
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "chromeProcessIds": [20518],
                "launched": True,
            },
        },
    )

    with pytest.raises(RuntimeError, match="simulated collection failure") as exc_info:
        bridge.dispatch("run-export", {"outputPath": str(output), "profileDir": str(tmp_path / "profile")})

    assert exc_info.value.export_recovery["status"] == "restored-backup"
    assert exc_info.value.export_recovery["sourcePath"] == str(last_good)
    assert json.loads(output.read_text())["threads"][0]["chatTitle"] == "Good Thread"
