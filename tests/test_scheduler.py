from __future__ import annotations

import json
import plistlib
from datetime import datetime, timezone
from pathlib import Path

from whatsapp_collector.scheduler import (
    LAUNCH_AGENT_LABEL,
    ScheduleConfig,
    build_launch_agent_plist,
    build_native_schedule_script,
    build_schedule_script,
    schedule_status_payload,
)


def test_schedule_script_opens_menu_bar_app_and_posts_export_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"maxMessages": 15, "outputPath": "/tmp/export.json"}))

    script = build_schedule_script(
        ui_url="http://127.0.0.1:8765",
        payload_path=payload_path,
    )

    assert 'open -g -a "WhatsApp Collector"' in script
    assert "http://127.0.0.1:8765/api/schedule" in script
    assert "--max-time 2" in script
    assert "http://127.0.0.1:8765/api/window/ensure" in script
    assert "http://127.0.0.1:8765/api/export/run" in script
    assert "--data-binary" in script
    assert "mktemp -t whatsapp-collector-export" in script
    assert "WhatsApp Collector scheduled export HTTP" in script
    assert "RUN_STATE_PATH" in script
    assert 'write_run_state running "Scheduled export started."' in script
    assert 'write_run_state succeeded "Scheduled export completed."' in script
    assert 'write_run_state failed "Scheduled export failed with exit $exit_code."' in script
    assert "restoredLastGood" in script
    assert str(payload_path) in script
    assert "/Users/assistant/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json" not in script
    assert "crontab" not in script


def test_launch_agent_plist_runs_scheduler_script_every_interval(tmp_path: Path) -> None:
    script_path = tmp_path / "scheduled-export.sh"
    stdout_path = tmp_path / "out.log"
    stderr_path = tmp_path / "err.log"

    payload = build_launch_agent_plist(
        interval_minutes=15,
        script_path=script_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    info = plistlib.loads(payload)
    assert info["Label"] == LAUNCH_AGENT_LABEL
    assert info["ProgramArguments"] == ["/bin/sh", str(script_path)]
    assert info["StartInterval"] == 15 * 60
    assert info["RunAtLoad"] is True
    assert info["StandardOutPath"] == str(stdout_path)
    assert info["StandardErrorPath"] == str(stderr_path)


def test_native_schedule_script_runs_bridge_without_localhost(tmp_path: Path) -> None:
    bridge_path = tmp_path / "native_bridge.py"
    payload_path = tmp_path / "payload.json"
    bridge_path.write_text("#!/usr/bin/env python3\n")
    payload_path.write_text(json.dumps({"maxMessages": 15, "outputPath": "/tmp/export.json"}))

    script = build_native_schedule_script(
        bridge_path=bridge_path,
        payload_path=payload_path,
        python_executable="/opt/homebrew/bin/python3",
        resource_dir=tmp_path / "Resources",
        repo_root=tmp_path,
    )

    assert "native://bridge" not in script
    assert "127.0.0.1" not in script
    assert "curl" not in script
    assert '"$PYTHON" "$BRIDGE_PATH" ensure-window' in script
    assert '"$PYTHON" "$BRIDGE_PATH" run-export' in script
    assert "RUN_STATE_PATH" in script
    assert 'write_run_state running "Scheduled export started."' in script
    assert "WA_COLLECTOR_NATIVE_RESOURCE_DIR" in script
    assert "WA_COLLECTOR_REPO_ROOT" in script
    assert "restoredLastGood" in script
    assert str(payload_path) in script


def test_schedule_status_payload_is_user_readable(tmp_path: Path) -> None:
    config = ScheduleConfig(
        interval_minutes=15,
        ui_url="http://127.0.0.1:8765",
        payload={"outputPath": "/tmp/export.json"},
        plist_path=tmp_path / "agent.plist",
        script_path=tmp_path / "scheduled-export.sh",
        payload_path=tmp_path / "payload.json",
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    config.plist_path.write_text("plist")

    status = schedule_status_payload(config, loaded=True)

    assert status["enabled"] is True
    assert status["loaded"] is True
    assert status["intervalMinutes"] == 15
    assert status["nextStep"] == "WhatsApp Collector will run exports automatically while you are logged in."
    assert status["plistPath"] == str(config.plist_path)
    assert status["lastSuccessAt"] is None
    assert status["nextRunAfter"] is None
    assert status["currentRunActive"] is False
    assert status["launchctlState"] is None


def test_schedule_status_payload_reports_recent_success_from_log(tmp_path: Path) -> None:
    config = ScheduleConfig(
        interval_minutes=45,
        ui_url="native://bridge",
        mode="native",
        payload={"outputPath": "/tmp/export.json"},
        plist_path=tmp_path / "agent.plist",
        script_path=tmp_path / "scheduled-export.sh",
        payload_path=tmp_path / "payload.json",
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    config.plist_path.write_text("plist")
    config.stdout_path.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "run-export",
                "checkedAt": "2026-06-24T10:15:00+00:00",
                "export": {"path": "/tmp/export.json", "threadCount": 41, "exportedAt": "2026-06-24T10:14:59+00:00"},
                "threadCount": 41,
            },
            indent=2,
        )
        + "\n"
        + json.dumps(
            {
                "ok": True,
                "command": "run-export",
                "checkedAt": "2026-06-24T11:00:00+00:00",
                "export": {"path": "/tmp/export.json", "threadCount": 43, "exportedAt": "2026-06-24T10:59:58+00:00"},
                "threadCount": 43,
            },
            indent=2,
        )
        + "\n"
    )

    status = schedule_status_payload(config, loaded=True)

    assert status["lastSuccessAt"] == "2026-06-24T11:00:00+00:00"
    assert status["lastThreadCount"] == 43
    assert status["lastOutputPath"] == "/tmp/export.json"
    assert status["lastExportedAt"] == "2026-06-24T10:59:58+00:00"
    assert status["nextRunAfter"] == "2026-06-24T11:45:00+00:00"


def test_schedule_status_payload_reports_active_run_state(tmp_path: Path) -> None:
    run_state_path = tmp_path / "run-state.json"
    config = ScheduleConfig(
        interval_minutes=15,
        ui_url="native://bridge",
        mode="native",
        payload={"outputPath": "/tmp/export.json"},
        plist_path=tmp_path / "agent.plist",
        script_path=tmp_path / "scheduled-export.sh",
        payload_path=tmp_path / "payload.json",
        run_state_path=run_state_path,
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    config.plist_path.write_text("plist")
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    run_state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "startedAt": started_at,
                "updatedAt": started_at,
                "completedAt": None,
                "message": "Scheduled export started.",
            }
        )
    )

    status = schedule_status_payload(config, loaded=True)

    assert status["currentRunStatus"] == "running"
    assert status["currentRunActive"] is True
    assert status["currentRunStartedAt"] == started_at
    assert status["runStatePath"] == str(run_state_path)


def test_schedule_status_payload_uses_launchctl_running_fallback(tmp_path: Path) -> None:
    config = ScheduleConfig(
        interval_minutes=15,
        ui_url="native://bridge",
        mode="native",
        payload={"outputPath": "/tmp/export.json"},
        plist_path=tmp_path / "agent.plist",
        script_path=tmp_path / "scheduled-export.sh",
        payload_path=tmp_path / "payload.json",
        stdout_path=tmp_path / "out.log",
        stderr_path=tmp_path / "err.log",
    )
    config.plist_path.write_text("plist")

    status = schedule_status_payload(config, loaded=True, launchctl_summary={"loaded": True, "state": "running", "activeCount": 1})

    assert status["launchctlState"] == "running"
    assert status["launchctlActiveCount"] == 1
    assert status["currentRunStatus"] == "running"
    assert status["currentRunMessage"] == "LaunchAgent is currently running."
    assert status["currentRunActive"] is True
