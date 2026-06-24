#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
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
from whatsapp_collector.launcher import (  # noqa: E402
    DEFAULT_DEBUG_PORT,
    DEFAULT_MARKER_TITLE,
    DEFAULT_MARKER_URL_SUBSTRING,
    DEFAULT_PROFILE_DIR,
    DEFAULT_TARGET_URL,
    ensure_dedicated_whatsapp_window,
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
    }


def _collector(cfg: dict[str, Any]) -> WhatsAppCollector:
    target = ChromeTarget(
        marker_title=cfg["marker_title"],
        marker_url_substring=cfg["marker_url_substring"],
        target_url_substring=cfg["target_url"],
    )
    session = ChromeWhatsAppSession(target=target, debug_port=cfg["debug_port"])
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


def _run_export(cfg: dict[str, Any]) -> dict[str, Any]:
    export = _collector(cfg).collect_dashboard_export(
        account_label=cfg["account_label"],
        max_messages=cfg["max_messages"],
        max_all_chats=cfg["max_all_chats"],
        allow_labels=cfg["allow_labels"],
        exclude_labels=cfg["exclude_labels"],
        include_groups=cfg["include_groups"],
        attachments_dir=cfg["output_path"].parent / "Attachments",
    )
    _write_atomic_json(export, cfg["output_path"])
    summary = _read_export_summary(cfg["output_path"], parse_json=True)
    thread_count = len(export.get("threads", [])) if isinstance(export.get("threads"), list) else 0
    summary["threadCount"] = thread_count
    summary["exportedAt"] = export.get("exportedAt")
    return {
        "ok": True,
        "command": "run-export",
        "checkedAt": _now(),
        "export": summary,
        "threadCount": thread_count,
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
        print(
            json.dumps(
                {
                    "ok": False,
                    "command": command,
                    "checkedAt": _now(),
                    "error": str(exc),
                    "errorType": type(exc).__name__,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
