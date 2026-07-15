#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


def _bootstrap_paths() -> None:
    resource_dir = Path(os.environ.get("WA_COLLECTOR_NATIVE_RESOURCE_DIR", "")).expanduser()
    repo_root = Path(os.environ.get("WA_COLLECTOR_REPO_ROOT", "")).expanduser()
    candidates = [
        resource_dir / "python",
        repo_root / "src",
        Path.cwd() / "src",
        Path(__file__).resolve().parents[2] / "src" if len(Path(__file__).resolve().parents) > 2 else Path.cwd() / "src",
    ]
    for candidate in candidates:
        if candidate.exists():
            sys.path.insert(0, str(candidate))


_bootstrap_paths()

from whatsapp_collector.chrome_session import ChromeTarget, ChromeWhatsAppSession  # noqa: E402
from whatsapp_collector.collector import (  # noqa: E402
    DEFAULT_ALL_VIEW_CHAT_LIMIT,
    GROUP_INCLUDE_STANDARD,
    MAX_MESSAGE_LOOKBACK_HARD_LIMIT,
    WhatsAppCollector,
)
from whatsapp_collector.attachment_store import DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES  # noqa: E402
from whatsapp_collector.devtools_bridge import ChromeDevToolsBridge  # noqa: E402
from whatsapp_collector.export_quality import (  # noqa: E402
    ExportQualityError,
    restore_latest_acceptable_backup,
    validate_export_quality,
)
from whatsapp_collector.launcher import (  # noqa: E402
    DEFAULT_DEBUG_PORT,
    DEFAULT_MARKER_TITLE,
    DEFAULT_MARKER_URL_SUBSTRING,
    DEFAULT_PROFILE_DIR,
    DEFAULT_TARGET_URL,
    chrome_profile_process_ids,
    ensure_dedicated_whatsapp_window,
    terminate_profile_processes,
)
from whatsapp_collector.scheduler import install_native_schedule, remove_schedule, schedule_status  # noqa: E402
from whatsapp_collector.web_ui import (  # noqa: E402
    DEFAULT_UI_OUTPUT_PATH,
    _ai_harness_prompt,
    _read_export_summary,
    _sorted_unique_labels,
    _write_atomic_json,
    default_collect_labels,
)

CHROME_OWNERSHIP_PATH_ENV = "WA_COLLECTOR_CHROME_OWNERSHIP_PATH"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_label_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    labels: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        label = str(item or "").replace("\u200e", "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def _path(value: Any, default: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default.expanduser()
    return Path(str(value)).expanduser()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _pid_set(value: Any) -> set[int] | None:
    if value is None:
        return None
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    pids: set[int] = set()
    for item in raw_items:
        try:
            pid = int(item)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            pids.add(pid)
    return pids


def _record_chrome_ownership(cfg: dict[str, Any], expected_pids: set[int]) -> None:
    ownership_path_raw = os.environ.get(CHROME_OWNERSHIP_PATH_ENV)
    if not ownership_path_raw:
        return
    ownership_path = Path(ownership_path_raw).expanduser()
    try:
        payload = json.loads(ownership_path.read_text())
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update(
        {
            "profileDir": str(cfg["profile_dir"]),
            "debugPort": int(cfg["debug_port"]),
            "expectedChromeProcessIds": sorted(expected_pids),
        }
    )
    ownership_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ownership_path.with_name(f".{ownership_path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temp_path.replace(ownership_path)


def _group_include(value: Any) -> str:
    value = str(value or "").strip()
    if value in {"labeledAlways", "labeled-always", "always-labeled", "alwaysIncludeOnly"}:
        return "labeledAlways"
    return GROUP_INCLUDE_STANDARD


def _config(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_path": _path(payload.get("outputPath"), DEFAULT_UI_OUTPUT_PATH),
        "profile_dir": _path(payload.get("profileDir"), DEFAULT_PROFILE_DIR),
        "debug_port": _int(payload.get("debugPort"), DEFAULT_DEBUG_PORT),
        "marker_title": str(payload.get("markerTitle") or DEFAULT_MARKER_TITLE),
        "marker_url_substring": str(payload.get("markerUrlSubstring") or DEFAULT_MARKER_URL_SUBSTRING),
        "target_url": str(payload.get("targetUrl") or DEFAULT_TARGET_URL),
        "display_name": (str(payload.get("displayName")).strip() if payload.get("displayName") else None),
        "account_label": str(payload.get("accountLabel") or "WhatsApp"),
        "max_messages": max(1, _int(payload.get("maxMessages"), MAX_MESSAGE_LOOKBACK_HARD_LIMIT)),
        "max_all_chats": max(1, _int(payload.get("maxAllChats"), DEFAULT_ALL_VIEW_CHAT_LIMIT)),
        "allow_labels": _normalize_label_list(payload.get("allowLabels")),
        "exclude_labels": _normalize_label_list(payload.get("excludeLabels")),
        "include_groups": _group_include(payload.get("includeGroups")),
        "download_attachments": _bool(payload.get("downloadAttachments"), True),
        "attachment_storage_limit_bytes": max(
            100_000_000,
            min(_int(payload.get("attachmentStorageLimitBytes"), DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES), 100_000_000_000),
        ),
    }


def _schedule_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "maxMessages": cfg["max_messages"],
        "maxAllChats": cfg["max_all_chats"],
        "accountLabel": cfg["account_label"],
        "allowLabels": list(cfg["allow_labels"]),
        "excludeLabels": list(cfg["exclude_labels"]),
        "includeGroups": cfg["include_groups"],
        "displayName": cfg["display_name"],
        "debugPort": cfg["debug_port"],
        "markerTitle": cfg["marker_title"],
        "markerUrlSubstring": cfg["marker_url_substring"],
        "targetUrl": cfg["target_url"],
        "profileDir": str(cfg["profile_dir"]),
        "outputPath": str(cfg["output_path"]),
        "downloadAttachments": cfg["download_attachments"],
        "attachmentStorageLimitBytes": cfg["attachment_storage_limit_bytes"],
    }


def _collector(cfg: dict[str, Any]) -> WhatsAppCollector:
    target = ChromeTarget(
        marker_title=cfg["marker_title"],
        marker_url_substring=cfg["marker_url_substring"],
        target_url_substring=cfg["target_url"],
    )
    session = ChromeWhatsAppSession(target=target, debug_port=cfg["debug_port"], profile_dir=cfg["profile_dir"])
    return WhatsAppCollector(session=session)


def _status(cfg: dict[str, Any]) -> dict[str, Any]:
    summary = _read_export_summary(cfg["output_path"], parse_json=True)
    return {
        "ok": True,
        "command": "status",
        "checkedAt": _now(),
        "export": summary,
        "schedule": schedule_status(),
        "aiPrompt": _ai_harness_prompt(str(cfg["output_path"])),
    }


def _ensure_window(cfg: dict[str, Any]) -> dict[str, Any]:
    result = ensure_dedicated_whatsapp_window(
        profile_dir=cfg["profile_dir"],
        display_name=cfg["display_name"],
        placement_mode="visible",
        settle_seconds=3.0,
        marker_title=cfg["marker_title"],
        marker_url_substring=cfg["marker_url_substring"],
        target_url=cfg["target_url"],
        debug_port=cfg["debug_port"],
    )
    return {"ok": True, "command": "ensure-window", "checkedAt": _now(), "window": result}


def _ensure_export_window(cfg: dict[str, Any]) -> tuple[dict[str, Any], set[int]]:
    try:
        response = _ensure_window(cfg)
    except Exception as exc:
        existing_pids = set(
            chrome_profile_process_ids(
                cfg["profile_dir"],
                debug_port=cfg["debug_port"],
            )
        )
        if existing_pids:
            _record_chrome_ownership(cfg, existing_pids)
        raise RuntimeError(
            f"Could not open the dedicated Chrome profile at {cfg['profile_dir']} for export: {exc}"
        ) from exc
    window = dict(response.get("window") or {})
    expected_pids = _pid_set(window.get("chromeProcessIds")) or set()
    if not expected_pids:
        raise RuntimeError(
            "The dedicated Chrome profile opened, but WhatsApp Collector could not identify its exact Chrome process. "
            "The export was stopped so that other Chrome windows cannot be targeted."
        )
    _record_chrome_ownership(cfg, expected_pids)
    return window, expected_pids


def _wait_for_export_readiness(
    cfg: dict[str, Any],
    window: dict[str, Any],
    expected_pids: set[int],
) -> tuple[dict[str, Any], set[int]]:
    for launch_attempt in range(2):
        try:
            ChromeDevToolsBridge(
                port=int(cfg["debug_port"]),
                marker_title=cfg["marker_title"],
                marker_url_substring=cfg["marker_url_substring"],
                target_url_substring=cfg["target_url"],
            ).wait_until_whatsapp_ready(attempts=60, delay_seconds=0.5, require_ready=True)
            return window, expected_pids
        except RuntimeError as exc:
            message = str(exc).casefold()
            user_action_required = "not logged in" in message or "did not finish rendering" in message
            owned_process_still_running = bool(
                chrome_profile_process_ids(
                    cfg["profile_dir"],
                    debug_port=cfg["debug_port"],
                    expected_pids=expected_pids,
                )
            )
            if launch_attempt > 0 or (user_action_required and owned_process_still_running):
                raise
            window, expected_pids = _ensure_export_window(cfg)
    raise RuntimeError("Could not prepare the dedicated Chrome profile for export")


def _run_export(cfg: dict[str, Any]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    export: dict[str, Any] | None = None
    window, expected_pids = _ensure_export_window(cfg)
    window, expected_pids = _wait_for_export_readiness(cfg, window, expected_pids)
    for attempt in range(1, 4):
        export = _collector(cfg).collect_dashboard_export(
            account_label=cfg["account_label"],
            max_messages=cfg["max_messages"],
            max_all_chats=cfg["max_all_chats"],
            allow_labels=cfg["allow_labels"],
            exclude_labels=cfg["exclude_labels"],
            include_groups=cfg["include_groups"],
            attachments_dir=cfg["output_path"].parent / "Attachments",
            download_attachments=cfg["download_attachments"],
            max_total_attachment_bytes=cfg["attachment_storage_limit_bytes"],
        )
        try:
            validate_export_quality(export)
            break
        except ExportQualityError as exc:
            attempts.append(exc.report)
            if attempt < 3:
                time.sleep(4 * attempt)
                continue
            restored = restore_latest_acceptable_backup(cfg["output_path"])
            report = dict(exc.report)
            report["attempts"] = attempts
            if restored:
                report["restoredLastGood"] = str(restored)
            raise ExportQualityError(report) from exc
    if export is None:
        raise RuntimeError("Collector did not return an export payload")
    _write_atomic_json(export, cfg["output_path"])
    summary = _read_export_summary(cfg["output_path"], parse_json=True)
    thread_count = len(export.get("threads", [])) if isinstance(export.get("threads"), list) else 0
    summary["threadCount"] = thread_count
    summary["exportedAt"] = export.get("exportedAt")
    termination = terminate_profile_processes(
        cfg["profile_dir"],
        debug_port=cfg["debug_port"],
        expected_pids=expected_pids,
        wait_attempts=8,
        delay_seconds=0.2,
    )
    remaining_pids = termination.get("remainingProcessIds", [])
    return {
        "ok": True,
        "command": "run-export",
        "checkedAt": _now(),
        "export": summary,
        "threadCount": thread_count,
        "window": {
            "profileDir": str(cfg["profile_dir"]),
            "debugPort": cfg["debug_port"],
            "chromeProcessIds": sorted(expected_pids),
            "launchedForExport": window.get("launched") is True,
            "closedAfterExport": not remaining_pids,
            "termination": termination,
        },
    }


def _close_window(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    expected_pids = _pid_set(payload.get("expectedChromeProcessIds"))
    if not expected_pids:
        return {
            "ok": True,
            "command": "close-window",
            "checkedAt": _now(),
            "window": {
                "profileDir": str(cfg["profile_dir"]),
                "debugPort": cfg["debug_port"],
                "expectedChromeProcessIds": [],
                "closed": False,
                "closeAttempted": False,
                "reason": "No captured dedicated Chrome process IDs were available; no process was targeted.",
            },
        }
    termination = terminate_profile_processes(
        cfg["profile_dir"],
        debug_port=cfg["debug_port"],
        expected_pids=expected_pids,
        wait_attempts=20,
        delay_seconds=0.25,
    )
    remaining_pids = termination.get("remainingProcessIds", [])
    return {
        "ok": not remaining_pids,
        "command": "close-window",
        "checkedAt": _now(),
        "window": {
            "profileDir": str(cfg["profile_dir"]),
            "debugPort": cfg["debug_port"],
            "expectedChromeProcessIds": sorted(expected_pids),
            "closed": not remaining_pids,
            "closeAttempted": True,
            "termination": termination,
        },
    }


def _labels(cfg: dict[str, Any]) -> dict[str, Any]:
    labels = default_collect_labels(
        debug_port=cfg["debug_port"],
        marker_title=cfg["marker_title"],
        marker_url_substring=cfg["marker_url_substring"],
        target_url=cfg["target_url"],
    )
    combined = _sorted_unique_labels([*labels, *cfg["allow_labels"], *cfg["exclude_labels"]])
    return {
        "ok": True,
        "command": "labels",
        "checkedAt": _now(),
        "labels": combined,
        "allowLabels": cfg["allow_labels"],
        "excludeLabels": cfg["exclude_labels"],
    }


def _schedule_install(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    interval = max(1, min(_int(payload.get("intervalMinutes"), 15), 24 * 60))
    resource_dir_raw = os.environ.get("WA_COLLECTOR_NATIVE_RESOURCE_DIR")
    repo_root_raw = os.environ.get("WA_COLLECTOR_REPO_ROOT")
    result = install_native_schedule(
        bridge_path=Path(__file__).resolve(),
        python_executable=sys.executable,
        payload=_schedule_payload(cfg),
        interval_minutes=interval,
        resource_dir=Path(resource_dir_raw).expanduser() if resource_dir_raw else Path(__file__).resolve().parent,
        repo_root=Path(repo_root_raw).expanduser() if repo_root_raw else None,
    )
    return {"ok": True, "command": "schedule-install", "checkedAt": _now(), "schedule": result}


def _schedule_remove() -> dict[str, Any]:
    return {"ok": True, "command": "schedule-remove", "checkedAt": _now(), "schedule": remove_schedule()}


def dispatch(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    cfg = _config(payload)
    if command == "status":
        return _status(cfg)
    if command == "ensure-window":
        return _ensure_window(cfg)
    if command == "run-export":
        return _run_export(cfg)
    if command == "close-window":
        return _close_window(cfg, payload)
    if command == "labels":
        return _labels(cfg)
    if command == "schedule-status":
        return {"ok": True, "command": "schedule-status", "checkedAt": _now(), "schedule": schedule_status()}
    if command == "schedule-install":
        return _schedule_install(cfg, payload)
    if command == "schedule-remove":
        return _schedule_remove()
    raise ValueError(f"Unknown native bridge command: {command}")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    try:
        result = dispatch(command, payload)
    except Exception as exc:
        error_payload = {
            "ok": False,
            "command": command,
            "checkedAt": _now(),
            "error": str(exc),
            "errorType": type(exc).__name__,
        }
        if isinstance(exc, ExportQualityError):
            error_payload["exportQuality"] = exc.report
        print(json.dumps(error_payload, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
