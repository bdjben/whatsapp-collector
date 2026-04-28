from pathlib import Path

import pytest

from whatsapp_collector.devtools_bridge import ChromeDevToolsBridge

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEDULED_SCRIPT = PROJECT_ROOT / "scripts" / "scheduled_export.sh"
HOURLY_SCRIPT = PROJECT_ROOT / "scripts" / "hourly_tv_export.sh"


def test_native_devtools_bridge_does_not_activate_or_bring_windows_to_front() -> None:
    bridge_source = (PROJECT_ROOT / "src" / "whatsapp_collector" / "devtools_bridge.py").read_text()

    assert "Page.bringToFront" not in bridge_source
    assert "Target.activateTarget" not in bridge_source


def test_devtools_bridge_does_not_shell_out_to_system_node() -> None:
    bridge_source = (PROJECT_ROOT / "src" / "whatsapp_collector" / "devtools_bridge.py").read_text()

    assert '"node"' not in bridge_source
    assert "subprocess.run" not in bridge_source
    assert "chrome_devtools.mjs" not in bridge_source


def test_devtools_bridge_surfaces_native_cdp_error_details(monkeypatch) -> None:
    def fake_fetch_json(self, path):
        raise RuntimeError("No matching Chrome DevTools target found")

    monkeypatch.setattr(ChromeDevToolsBridge, "_fetch_json", fake_fetch_json)

    bridge = ChromeDevToolsBridge(port=19220, marker_title="WhatsApp Collector", target_url_substring="https://web.whatsapp.com/")
    with pytest.raises(RuntimeError) as excinfo:
        bridge.version()

    message = str(excinfo.value)
    assert "Chrome DevTools request failed" in message
    assert "action=version" in message
    assert "port=19220" in message
    assert "No matching Chrome DevTools target found" in message


def test_scheduled_export_uses_edge_hidden_placement_for_scheduled_checker() -> None:
    script_source = SCHEDULED_SCRIPT.read_text()

    assert "--placement-mode edge-hidden" in script_source
    assert "--placement-mode visible" not in script_source


def test_scheduled_export_uses_configured_python_binary_instead_of_bare_python() -> None:
    script_source = SCHEDULED_SCRIPT.read_text()

    assert "PYTHON_BIN=" in script_source
    assert "PYTHONPATH=src python -m" not in script_source


def test_scheduled_export_does_not_assume_display_name_or_old_marker() -> None:
    script_source = SCHEDULED_SCRIPT.read_text()

    assert 'DISPLAY_NAME="${WA_COLLECTOR_DISPLAY_NAME:-}"' in script_source
    assert "ensure-window" in script_source
    assert "WhatsApp Collector" in script_source
    assert "Hermes" not in script_source
    assert "ensure-tv-window" not in script_source


def test_scheduled_export_clears_devtools_env_for_active_session_fallback() -> None:
    script_source = SCHEDULED_SCRIPT.read_text()

    assert "env \\" in script_source
    assert "-u WA_CHROME_DEBUG_PORT" in script_source
    assert "-u WA_CHROME_MARKER_TITLE" in script_source
    assert "-u WA_CHROME_MARKER_URL_SUBSTRING" in script_source
    assert "-u WA_CHROME_TARGET_URL_SUBSTRING" in script_source
    assert '"${RUNNER[@]}" dashboard-export' in script_source


def test_scheduled_export_retries_dedicated_profile_before_active_fallback() -> None:
    script_source = SCHEDULED_SCRIPT.read_text()

    assert "WA_DEDICATED_RETRY_DELAY_SECONDS" in script_source
    assert "WA_DEDICATED_ATTEMPTS" in script_source
    assert "run_dedicated_attempt" in script_source
    assert "for attempt in" in script_source
    assert "cleanup" in script_source
    assert script_source.index("run_dedicated_attempt") < script_source.index("active-session-fallback")


def test_scheduled_export_preserves_current_without_degraded_alert_mode() -> None:
    script_source = SCHEDULED_SCRIPT.read_text()
    hourly_source = HOURLY_SCRIPT.read_text()

    assert "whatsappmonitor-preserved" in script_source
    assert '"mode":"preserved-current"' not in script_source
    assert 'mode == "whatsappmonitor-preserved"' in hourly_source


def test_scheduled_export_passes_env_excluded_labels_to_dashboard_export() -> None:
    script_source = SCHEDULED_SCRIPT.read_text()

    assert "WA_EXCLUDE_LABELS" in script_source
    assert "EXCLUDE_LABEL_ARGS" in script_source
    assert "--exclude-label" in script_source
    assert '"${EXCLUDE_LABEL_ARGS[@]}"' in script_source


def test_local_hourly_wrapper_excludes_school_only_labels_by_default() -> None:
    script_source = HOURLY_SCRIPT.read_text()

    assert "WA_EXCLUDE_LABELS" in script_source
    assert "Harvard" in script_source
    assert "Coller School of Management" in script_source
