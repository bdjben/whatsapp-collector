from __future__ import annotations

import json
import os
import plistlib
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

APP_NAME = "WhatsApp Collector"
LAUNCH_AGENT_LABEL = "studio.bdjben.whatsapp-collector.scheduled-export"
SUPPORT_DIR = Path("~/Library/Application Support/WhatsApp Collector").expanduser()
LOG_DIR = Path("~/Library/Logs/WhatsApp Collector").expanduser()
LAUNCH_AGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()
SCHEDULE_STATE_PATH = SUPPORT_DIR / "scheduled-export.json"
SCHEDULE_PAYLOAD_PATH = SUPPORT_DIR / "scheduled-export-payload.json"
SCHEDULE_SCRIPT_PATH = SUPPORT_DIR / "scheduled-export.sh"
SCHEDULE_RUN_STATE_PATH = SUPPORT_DIR / "scheduled-export-run-state.json"
SCHEDULE_PLIST_PATH = LAUNCH_AGENTS_DIR / f"{LAUNCH_AGENT_LABEL}.plist"
SCHEDULE_STDOUT_PATH = LOG_DIR / "scheduled-export.out.log"
SCHEDULE_STDERR_PATH = LOG_DIR / "scheduled-export.err.log"
DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_FAILED_RUN_CHROME_GRACE_SECONDS = 5 * 60


@dataclass(frozen=True)
class ScheduleConfig:
    interval_minutes: int
    ui_url: str
    payload: dict[str, Any]
    mode: str = "web"
    bridge_path: Path | None = None
    python_executable: str | None = None
    resource_dir: Path | None = None
    repo_root: Path | None = None
    plist_path: Path = SCHEDULE_PLIST_PATH
    script_path: Path = SCHEDULE_SCRIPT_PATH
    payload_path: Path = SCHEDULE_PAYLOAD_PATH
    run_state_path: Path = SCHEDULE_RUN_STATE_PATH
    stdout_path: Path = SCHEDULE_STDOUT_PATH
    stderr_path: Path = SCHEDULE_STDERR_PATH


def default_schedule_config(
    *,
    ui_url: str = "http://127.0.0.1:8765",
    payload: dict[str, Any] | None = None,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    mode: str = "web",
    bridge_path: Path | None = None,
    python_executable: str | None = None,
    resource_dir: Path | None = None,
    repo_root: Path | None = None,
    run_state_path: Path = SCHEDULE_RUN_STATE_PATH,
) -> ScheduleConfig:
    return ScheduleConfig(
        interval_minutes=_bounded_interval_minutes(interval_minutes),
        ui_url=ui_url.rstrip("/"),
        payload=dict(payload or {}),
        mode=mode,
        bridge_path=bridge_path,
        python_executable=python_executable,
        resource_dir=resource_dir,
        repo_root=repo_root,
        run_state_path=run_state_path,
    )


def _run_state_helpers() -> str:
    return """write_run_state() {
  status="$1"
  message="${2:-}"
  /usr/bin/python3 - "$RUN_STATE_PATH" "$status" "$message" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
status = sys.argv[2]
message = sys.argv[3] if len(sys.argv) > 3 else ""
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
previous = {}
try:
    previous = json.loads(path.read_text())
except Exception:
    previous = {}

started_at = now if status == "running" else previous.get("startedAt") or now
payload = {
    "status": status,
    "startedAt": started_at,
    "updatedAt": now,
    "completedAt": None if status == "running" else now,
    "message": message,
    "pid": os.getppid(),
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\\n")
PY
}
"""


def build_schedule_script(*, ui_url: str, payload_path: Path, run_state_path: Path = SCHEDULE_RUN_STATE_PATH) -> str:
    ready_url = f"{ui_url.rstrip('/')}/api/schedule"
    window_ensure_url = f"{ui_url.rstrip('/')}/api/window/ensure"
    export_url = f"{ui_url.rstrip('/')}/api/export/run"
    return f"""#!/bin/sh
set -eu

APP_NAME={shlex.quote(APP_NAME)}
READY_URL={shlex.quote(ready_url)}
WINDOW_ENSURE_URL={shlex.quote(window_ensure_url)}
EXPORT_URL={shlex.quote(export_url)}
PAYLOAD_PATH={shlex.quote(str(payload_path))}
RUN_STATE_PATH={shlex.quote(str(run_state_path))}

{_run_state_helpers()}
write_run_state running "Scheduled export started." || true
export_completed=0

# Make the menu-bar app/UI available without requiring a Terminal window.
/usr/bin/open -g -a "WhatsApp Collector" >/dev/null 2>&1 || true

attempt=0
while [ "$attempt" -lt 60 ]; do
  if /usr/bin/curl --max-time 2 -fsS "$READY_URL" >/dev/null 2>&1; then
    break
  fi
  attempt=$((attempt + 1))
  /bin/sleep 1
done

post_json() {{
  url="$1"
  output_path="$2"
  http_code=$(/usr/bin/curl -sS -X POST "$url" \
    -H 'Content-Type: application/json' \
    --data-binary @"$PAYLOAD_PATH" \
    -o "$output_path" \
    -w '%{{http_code}}')
  case "$http_code" in
    2*) return 0 ;;
    *)
      echo "WhatsApp Collector scheduled export HTTP $http_code from $url" >&2
      /bin/cat "$output_path" >&2
      return 22
      ;;
  esac
}}

ensure_response="$(/usr/bin/mktemp -t whatsapp-collector-ensure)"
tmp_response="$(/usr/bin/mktemp -t whatsapp-collector-export)"
cleanup() {{
  /bin/rm -f "$ensure_response" "$tmp_response"
}}
finish() {{
  exit_code="$?"
  if [ "$exit_code" -eq 0 ] && [ "$export_completed" -eq 1 ]; then
    write_run_state succeeded "Scheduled export completed." || true
  elif [ "$exit_code" -ne 0 ]; then
    write_run_state failed "Scheduled export failed with exit $exit_code." || true
  fi
  cleanup
}}
trap finish EXIT

post_json "$WINDOW_ENSURE_URL" "$ensure_response"
post_json "$EXPORT_URL" "$tmp_response"
/bin/cat "$tmp_response"

/usr/bin/python3 - "$tmp_response" <<'PY'
import json
import sys
from pathlib import Path

response_path = Path(sys.argv[1])
response = json.loads(response_path.read_text())
if response.get("ok") is not True:
    raise SystemExit("WhatsApp export endpoint did not report success")

export_summary = response.get("export") or {{}}
count = int(response.get("threadCount") or export_summary.get("threadCount") or 0)
if count <= 0:
    raise SystemExit("WhatsApp export endpoint reported success with zero threads")
PY
export_completed=1
"""


def build_native_schedule_script(
    *,
    bridge_path: Path,
    payload_path: Path,
    python_executable: str,
    run_state_path: Path = SCHEDULE_RUN_STATE_PATH,
    resource_dir: Path | None = None,
    repo_root: Path | None = None,
    failed_run_chrome_grace_seconds: int = DEFAULT_FAILED_RUN_CHROME_GRACE_SECONDS,
) -> str:
    resource_dir = resource_dir or bridge_path.parent
    repo_root_export = ""
    if repo_root:
        repo_root_export = f"WA_COLLECTOR_REPO_ROOT={shlex.quote(str(repo_root))}\nexport WA_COLLECTOR_REPO_ROOT\n"
    return f"""#!/bin/sh
set -eu

PYTHON={shlex.quote(str(python_executable))}
BRIDGE_PATH={shlex.quote(str(bridge_path))}
PAYLOAD_PATH={shlex.quote(str(payload_path))}
RUN_STATE_PATH={shlex.quote(str(run_state_path))}
FAILED_RUN_CHROME_GRACE_SECONDS={max(0, int(failed_run_chrome_grace_seconds))}
WA_COLLECTOR_NATIVE_RESOURCE_DIR={shlex.quote(str(resource_dir))}
export WA_COLLECTOR_NATIVE_RESOURCE_DIR
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE
{repo_root_export}
# Run the native bridge directly. No localhost web server or app process is required.

{_run_state_helpers()}
write_run_state running "Scheduled export started." || true
export_completed=0

tmp_response="$(/usr/bin/mktemp -t whatsapp-collector-native-export)"
cleanup_payload="$(/usr/bin/mktemp -t whatsapp-collector-native-cleanup)"
/bin/cp "$PAYLOAD_PATH" "$cleanup_payload"
WA_COLLECTOR_CHROME_OWNERSHIP_PATH="$cleanup_payload"
export WA_COLLECTOR_CHROME_OWNERSHIP_PATH
cleanup() {{
  /bin/rm -f "$tmp_response" "$cleanup_payload"
}}
close_collector_chrome() {{
  "$PYTHON" "$BRIDGE_PATH" close-window < "$cleanup_payload" >/dev/null 2>&1 || true
}}
report_failure_response() {{
  if [ -s "$tmp_response" ]; then
    /bin/cat "$tmp_response" >&2
  fi
}}
finish() {{
  exit_code="$?"
  trap - EXIT
  if [ "$exit_code" -eq 0 ] && [ "$export_completed" -eq 1 ]; then
    close_collector_chrome
    write_run_state succeeded "Scheduled export completed." || true
  elif [ "$exit_code" -ne 0 ]; then
    report_failure_response
    write_run_state failed "Scheduled export failed with exit $exit_code. Dedicated Chrome will close within five minutes." || true
    if [ "$FAILED_RUN_CHROME_GRACE_SECONDS" -gt 0 ]; then
      /bin/sleep "$FAILED_RUN_CHROME_GRACE_SECONDS"
    fi
    close_collector_chrome
  else
    close_collector_chrome
  fi
  cleanup
  exit "$exit_code"
}}
trap finish EXIT

"$PYTHON" "$BRIDGE_PATH" run-export < "$PAYLOAD_PATH" > "$tmp_response"
/bin/cat "$tmp_response"

/usr/bin/python3 - "$tmp_response" <<'PY'
import json
import sys
from pathlib import Path

response_path = Path(sys.argv[1])
response = json.loads(response_path.read_text())
if response.get("ok") is not True:
    raise SystemExit("WhatsApp native export bridge did not report success")

export_summary = response.get("export") or {{}}
count = int(response.get("threadCount") or export_summary.get("threadCount") or 0)
if count <= 0:
    raise SystemExit("WhatsApp native export bridge reported success with zero threads")
PY
export_completed=1
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


def install_native_schedule(
    *,
    bridge_path: Path,
    python_executable: str,
    payload: dict[str, Any],
    interval_minutes: int,
    resource_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    config = default_schedule_config(
        ui_url="native://bridge",
        payload=payload,
        interval_minutes=interval_minutes,
        mode="native",
        bridge_path=bridge_path,
        python_executable=python_executable,
        resource_dir=resource_dir,
        repo_root=repo_root,
    )
    _write_schedule_files(config)
    _run(["plutil", "-lint", str(config.plist_path)])
    _launchctl(["bootout", _launchctl_domain(), str(config.plist_path)], check=False)
    _launchctl(["bootstrap", _launchctl_domain(), str(config.plist_path)], check=True)
    _launchctl(["kickstart", "-k", f"{_launchctl_domain()}/{LAUNCH_AGENT_LABEL}"], check=False)
    return schedule_status_payload(config, loaded=is_schedule_loaded())


def remove_schedule() -> dict[str, Any]:
    config = load_schedule_config()
    _launchctl(["bootout", _launchctl_domain(), str(config.plist_path)], check=False)
    for path in (config.plist_path, config.script_path, config.payload_path, config.run_state_path, SCHEDULE_STATE_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return schedule_status_payload(config, loaded=False)


def schedule_status() -> dict[str, Any]:
    config = load_schedule_config()
    launchctl_summary = _schedule_launchctl_summary()
    return schedule_status_payload(config, loaded=launchctl_summary["loaded"], launchctl_summary=launchctl_summary)


def schedule_status_payload(
    config: ScheduleConfig,
    *,
    loaded: bool,
    launchctl_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enabled = config.plist_path.exists()
    launchctl_summary = launchctl_summary or {"loaded": bool(loaded)}
    run_summary = _schedule_run_summary(config, launchctl_state=_string_value(launchctl_summary, "state"))
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
        "mode": config.mode,
        "intervalMinutes": config.interval_minutes,
        "uiUrl": config.ui_url,
        "payload": config.payload,
        "bridgePath": str(config.bridge_path) if config.bridge_path else None,
        "pythonExecutable": config.python_executable,
        "resourceDir": str(config.resource_dir) if config.resource_dir else None,
        "repoRoot": str(config.repo_root) if config.repo_root else None,
        "plistPath": str(config.plist_path),
        "scriptPath": str(config.script_path),
        "payloadPath": str(config.payload_path),
        "stdoutPath": str(config.stdout_path),
        "stderrPath": str(config.stderr_path),
        "launchctlState": _string_value(launchctl_summary, "state"),
        "launchctlActiveCount": _int_value(launchctl_summary, "activeCount"),
        "nextStep": next_step,
        **run_summary,
    }


def _schedule_run_summary(config: ScheduleConfig, *, launchctl_state: str | None = None) -> dict[str, Any]:
    stdout_updated_at = _path_updated_at(config.stdout_path)
    stderr_updated_at = _path_updated_at(config.stderr_path)
    current_run = _read_schedule_run_state(config, launchctl_state=launchctl_state)
    stdout_objects = _read_recent_json_objects(config.stdout_path)
    stderr_objects = _read_recent_json_objects(config.stderr_path)
    last_run = _last_matching(stdout_objects, lambda value: value.get("command") == "run-export")
    last_success = _last_matching(
        stdout_objects,
        lambda value: value.get("ok") is True and value.get("command") == "run-export",
    )
    last_failure = _last_matching(stderr_objects, lambda value: value.get("ok") is False)

    last_success_at = _string_value(last_success, "checkedAt") or stdout_updated_at
    next_run_after = _next_run_after(last_success_at, config.interval_minutes)
    export_summary = last_success.get("export") if isinstance(last_success.get("export"), dict) else {}

    return {
        "stdoutUpdatedAt": stdout_updated_at,
        "stderrUpdatedAt": stderr_updated_at,
        "lastRunAt": _string_value(last_run, "checkedAt") or stdout_updated_at,
        "lastSuccessAt": last_success_at,
        "lastFailureAt": _string_value(last_failure, "checkedAt") or stderr_updated_at,
        "lastFailureMessage": _string_value(last_failure, "error"),
        "lastThreadCount": _int_value(last_success, "threadCount", export_summary.get("threadCount")),
        "lastExportedAt": _string_value(export_summary, "exportedAt"),
        "lastOutputPath": _string_value(export_summary, "path"),
        "nextRunAfter": next_run_after,
        **current_run,
    }


def _read_schedule_run_state(config: ScheduleConfig, *, launchctl_state: str | None = None) -> dict[str, Any]:
    try:
        raw = json.loads(config.run_state_path.read_text())
    except Exception:
        raw = {}
    status = _string_value(raw, "status")
    started_at = _string_value(raw, "startedAt")
    updated_at = _string_value(raw, "updatedAt")
    completed_at = _string_value(raw, "completedAt")
    message = _string_value(raw, "message")
    launchctl_running = launchctl_state == "running"
    active = (
        status == "running" and _is_recent(updated_at, max(timedelta(hours=6), timedelta(minutes=config.interval_minutes * 3)))
    ) or launchctl_running
    if launchctl_running and status != "running":
        status = "running"
        message = "LaunchAgent is currently running."
    if status == "running" and not active:
        status = "stale"
        message = "Scheduled export state was left running by an older or interrupted job."
    return {
        "currentRunStatus": status,
        "currentRunStartedAt": started_at,
        "currentRunUpdatedAt": updated_at,
        "currentRunCompletedAt": completed_at,
        "currentRunMessage": message,
        "currentRunActive": active,
        "runStatePath": str(config.run_state_path),
    }


def _read_recent_json_objects(path: Path, *, max_bytes: int = 1_000_000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    decoder = json.JSONDecoder()
    values: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            values.append(value)
        index = end
    return values


def _last_matching(values: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    for value in reversed(values):
        if predicate(value):
            return value
    return {}


def _path_updated_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()
    except OSError:
        return None


def _next_run_after(value: str | None, interval_minutes: int) -> str | None:
    date = _parse_iso_datetime(value)
    if not date:
        return None
    return (date + timedelta(minutes=_bounded_interval_minutes(interval_minutes))).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_recent(value: str | None, window: timedelta) -> bool:
    parsed = _parse_iso_datetime(value)
    if not parsed:
        return False
    return datetime.now(timezone.utc) - parsed <= window


def _string_value(source: Any, key: str) -> str | None:
    if not isinstance(source, dict):
        return None
    value = source.get(key)
    if value is None:
        return None
    return str(value)


def _int_value(source: Any, key: str, fallback: Any = None) -> int | None:
    if not isinstance(source, dict):
        value = fallback
    else:
        value = source.get(key, fallback)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        mode=str(state.get("mode") or "web"),
        bridge_path=Path(state["bridgePath"]) if state.get("bridgePath") else None,
        python_executable=str(state["pythonExecutable"]) if state.get("pythonExecutable") else None,
        resource_dir=Path(state["resourceDir"]) if state.get("resourceDir") else None,
        repo_root=Path(state["repoRoot"]) if state.get("repoRoot") else None,
        run_state_path=Path(state["runStatePath"]) if state.get("runStatePath") else SCHEDULE_RUN_STATE_PATH,
    )


def is_schedule_loaded() -> bool:
    return bool(_schedule_launchctl_summary()["loaded"])


def _schedule_launchctl_summary() -> dict[str, Any]:
    completed = _launchctl(["print", f"{_launchctl_domain()}/{LAUNCH_AGENT_LABEL}"], check=False)
    state = None
    active_count = None
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("state = "):
                state = stripped.removeprefix("state = ").strip()
            elif stripped.startswith("active count = "):
                active_count = _int_value({"activeCount": stripped.removeprefix("active count = ").strip()}, "activeCount")
    return {
        "loaded": completed.returncode == 0,
        "state": state,
        "activeCount": active_count,
    }


def _write_schedule_files(config: ScheduleConfig) -> None:
    for directory in (SUPPORT_DIR, LOG_DIR, LAUNCH_AGENTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    config.payload_path.write_text(json.dumps(config.payload, indent=2, ensure_ascii=False) + "\n")
    if config.mode == "native":
        if config.bridge_path is None or not config.python_executable:
            raise ValueError("Native schedules require a bridge path and Python executable")
        script = build_native_schedule_script(
            bridge_path=config.bridge_path,
            payload_path=config.payload_path,
            python_executable=config.python_executable,
            run_state_path=config.run_state_path,
            resource_dir=config.resource_dir,
            repo_root=config.repo_root,
        )
    else:
        script = build_schedule_script(ui_url=config.ui_url, payload_path=config.payload_path, run_state_path=config.run_state_path)
    config.script_path.write_text(script)
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
                "mode": config.mode,
                "payload": config.payload,
                "bridgePath": str(config.bridge_path) if config.bridge_path else None,
                "pythonExecutable": config.python_executable,
                "resourceDir": str(config.resource_dir) if config.resource_dir else None,
                "repoRoot": str(config.repo_root) if config.repo_root else None,
                "plistPath": str(config.plist_path),
                "scriptPath": str(config.script_path),
                "payloadPath": str(config.payload_path),
                "runStatePath": str(config.run_state_path),
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
