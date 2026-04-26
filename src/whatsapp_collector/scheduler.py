from __future__ import annotations

import json
import os
import plistlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_NAME = "WhatsApp Collector"
LAUNCH_AGENT_LABEL = "studio.bdjben.whatsapp-collector.scheduled-export"
SUPPORT_DIR = Path("~/Library/Application Support/WhatsApp Collector").expanduser()
LOG_DIR = Path("~/Library/Logs/WhatsApp Collector").expanduser()
LAUNCH_AGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()
SCHEDULE_STATE_PATH = SUPPORT_DIR / "scheduled-export.json"
SCHEDULE_PAYLOAD_PATH = SUPPORT_DIR / "scheduled-export-payload.json"
SCHEDULE_SCRIPT_PATH = SUPPORT_DIR / "scheduled-export.sh"
SCHEDULE_PLIST_PATH = LAUNCH_AGENTS_DIR / f"{LAUNCH_AGENT_LABEL}.plist"
SCHEDULE_STDOUT_PATH = LOG_DIR / "scheduled-export.out.log"
SCHEDULE_STDERR_PATH = LOG_DIR / "scheduled-export.err.log"
DEFAULT_INTERVAL_MINUTES = 15


@dataclass(frozen=True)
class ScheduleConfig:
    interval_minutes: int
    ui_url: str
    payload: dict[str, Any]
    plist_path: Path = SCHEDULE_PLIST_PATH
    script_path: Path = SCHEDULE_SCRIPT_PATH
    payload_path: Path = SCHEDULE_PAYLOAD_PATH
    stdout_path: Path = SCHEDULE_STDOUT_PATH
    stderr_path: Path = SCHEDULE_STDERR_PATH


def default_schedule_config(*, ui_url: str = "http://127.0.0.1:8765", payload: dict[str, Any] | None = None, interval_minutes: int = DEFAULT_INTERVAL_MINUTES) -> ScheduleConfig:
    return ScheduleConfig(
        interval_minutes=_bounded_interval_minutes(interval_minutes),
        ui_url=ui_url.rstrip("/"),
        payload=dict(payload or {}),
    )


def build_schedule_script(*, ui_url: str, payload_path: Path) -> str:
    status_url = f"{ui_url.rstrip('/')}/api/status"
    export_url = f"{ui_url.rstrip('/')}/api/export/run"
    return f"""#!/bin/sh
set -eu

APP_NAME={shlex.quote(APP_NAME)}
STATUS_URL={shlex.quote(status_url)}
EXPORT_URL={shlex.quote(export_url)}
PAYLOAD_PATH={shlex.quote(str(payload_path))}

# Make the menu-bar app/UI available without requiring a Terminal window.
/usr/bin/open -g -a "WhatsApp Collector" >/dev/null 2>&1 || true

attempt=0
while [ "$attempt" -lt 60 ]; do
  if /usr/bin/curl -fsS "$STATUS_URL" >/dev/null 2>&1; then
    break
  fi
  attempt=$((attempt + 1))
  /bin/sleep 1
done

/usr/bin/curl -fsS -X POST "$EXPORT_URL" \
  -H 'Content-Type: application/json' \
  --data-binary @"$PAYLOAD_PATH"
"""


def build_launch_agent_plist(*, interval_minutes: int, script_path: Path, stdout_path: Path, stderr_path: Path) -> bytes:
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": ["/bin/sh", str(script_path)],
        "RunAtLoad": True,
        "StartInterval": _bounded_interval_minutes(interval_minutes) * 60,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    return plistlib.dumps(payload, sort_keys=True)


def install_schedule(*, ui_url: str, payload: dict[str, Any], interval_minutes: int) -> dict[str, Any]:
    config = default_schedule_config(ui_url=ui_url, payload=payload, interval_minutes=interval_minutes)
    _write_schedule_files(config)
    _run(["plutil", "-lint", str(config.plist_path)])
    _launchctl(["bootout", _launchctl_domain(), str(config.plist_path)], check=False)
    _launchctl(["bootstrap", _launchctl_domain(), str(config.plist_path)], check=True)
    _launchctl(["kickstart", "-k", f"{_launchctl_domain()}/{LAUNCH_AGENT_LABEL}"], check=False)
    return schedule_status_payload(config, loaded=is_schedule_loaded())


def remove_schedule() -> dict[str, Any]:
    config = load_schedule_config()
    _launchctl(["bootout", _launchctl_domain(), str(config.plist_path)], check=False)
    for path in (config.plist_path, config.script_path, config.payload_path, SCHEDULE_STATE_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return schedule_status_payload(config, loaded=False)


def schedule_status() -> dict[str, Any]:
    config = load_schedule_config()
    return schedule_status_payload(config, loaded=is_schedule_loaded())


def schedule_status_payload(config: ScheduleConfig, *, loaded: bool) -> dict[str, Any]:
    enabled = config.plist_path.exists()
    if enabled and loaded:
        next_step = "WhatsApp Collector will run exports automatically while you are logged in."
    elif enabled:
        next_step = "Automatic exports are configured but not currently loaded; open WhatsApp Collector or restart login to reload them."
    else:
        next_step = "Automatic exports are off."
    return {
        "enabled": enabled,
        "loaded": bool(loaded),
        "label": LAUNCH_AGENT_LABEL,
        "intervalMinutes": config.interval_minutes,
        "uiUrl": config.ui_url,
        "payload": config.payload,
        "plistPath": str(config.plist_path),
        "scriptPath": str(config.script_path),
        "payloadPath": str(config.payload_path),
        "stdoutPath": str(config.stdout_path),
        "stderrPath": str(config.stderr_path),
        "nextStep": next_step,
    }


def load_schedule_config() -> ScheduleConfig:
    if not SCHEDULE_STATE_PATH.exists():
        return default_schedule_config()
    try:
        state = json.loads(SCHEDULE_STATE_PATH.read_text())
    except Exception:
        return default_schedule_config()
    return default_schedule_config(
        ui_url=str(state.get("uiUrl") or "http://127.0.0.1:8765"),
        payload=dict(state.get("payload") or {}),
        interval_minutes=int(state.get("intervalMinutes") or DEFAULT_INTERVAL_MINUTES),
    )


def is_schedule_loaded() -> bool:
    completed = _launchctl(["print", f"{_launchctl_domain()}/{LAUNCH_AGENT_LABEL}"], check=False)
    return completed.returncode == 0


def _write_schedule_files(config: ScheduleConfig) -> None:
    for directory in (SUPPORT_DIR, LOG_DIR, LAUNCH_AGENTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    config.payload_path.write_text(json.dumps(config.payload, indent=2, ensure_ascii=False) + "\n")
    config.script_path.write_text(build_schedule_script(ui_url=config.ui_url, payload_path=config.payload_path))
    config.script_path.chmod(0o755)
    config.plist_path.write_bytes(
        build_launch_agent_plist(
            interval_minutes=config.interval_minutes,
            script_path=config.script_path,
            stdout_path=config.stdout_path,
            stderr_path=config.stderr_path,
        )
    )
    SCHEDULE_STATE_PATH.write_text(
        json.dumps(
            {
                "intervalMinutes": config.interval_minutes,
                "uiUrl": config.ui_url,
                "payload": config.payload,
                "plistPath": str(config.plist_path),
                "scriptPath": str(config.script_path),
                "payloadPath": str(config.payload_path),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def _bounded_interval_minutes(value: int) -> int:
    return max(1, min(int(value), 24 * 60))


def _launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    return _run(["launchctl", *args], check=check)


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True)
