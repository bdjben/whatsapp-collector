from __future__ import annotations

import json
import plistlib
from pathlib import Path

from whatsapp_collector.scheduler import (
    LAUNCH_AGENT_LABEL,
    ScheduleConfig,
    build_launch_agent_plist,
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
    assert "http://127.0.0.1:8765/api/status" in script
    assert "http://127.0.0.1:8765/api/export/run" in script
    assert "--data-binary" in script
    assert str(payload_path) in script
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
