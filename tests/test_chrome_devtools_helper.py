from pathlib import Path
import subprocess

import pytest

from whatsapp_collector.devtools_bridge import ChromeDevToolsBridge, NODE_HELPER

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER = NODE_HELPER
SCHEDULED_SCRIPT = PROJECT_ROOT / "scripts" / "scheduled_export.sh"


def test_devtools_helper_is_resolved_from_packaged_resources() -> None:
    assert HELPER.exists()
    assert HELPER.name == "chrome_devtools.mjs"
    assert HELPER.parent.name == "assets"


def test_devtools_helper_does_not_activate_or_bring_windows_to_front() -> None:
    helper_source = HELPER.read_text()

    assert "Page.bringToFront" not in helper_source
    assert "Target.activateTarget" not in helper_source


def test_devtools_bridge_surfaces_node_helper_stderr(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            args[0][0],
            output="",
            stderr="Error: No matching Chrome DevTools target found\n    at chooseTarget",
        )

    monkeypatch.setattr("whatsapp_collector.devtools_bridge.subprocess.run", fake_run)

    bridge = ChromeDevToolsBridge(port=19220, marker_title="WhatsApp Collector", target_url_substring="https://web.whatsapp.com/")
    with pytest.raises(RuntimeError) as excinfo:
        bridge.evaluate("document.title")

    message = str(excinfo.value)
    assert "Chrome DevTools helper failed" in message
    assert "action=evaluate" in message
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
