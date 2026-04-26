from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from whatsapp_collector.chrome_session import ChromeTarget, ChromeWhatsAppSession
from whatsapp_collector.collector import (
    DEFAULT_EXCLUDED_LABELS,
    MAX_MESSAGE_LOOKBACK_HARD_LIMIT,
    WhatsAppCollector,
)
from whatsapp_collector.launcher import (
    DEFAULT_DEBUG_PORT,
    DEFAULT_DISPLAY_NAME,
    DEFAULT_MARKER_TITLE,
    DEFAULT_MARKER_URL_SUBSTRING,
    DEFAULT_PLACEMENT_MODE,
    DEFAULT_PROFILE_DIR,
    DEFAULT_SETTLE_SECONDS,
    DEFAULT_TARGET_URL,
    ensure_dedicated_whatsapp_window,
    terminate_profile_processes,
)
from whatsapp_collector.models import Snapshot
from whatsapp_collector.web_ui import UIConfig, run_ui_server


def _merged_excluded_labels(exclude_labels: list[str] | None) -> list[str]:
    merged: list[str] = []
    for label in [*DEFAULT_EXCLUDED_LABELS, *(exclude_labels or [])]:
        if label not in merged:
            merged.append(label)
    return merged


def _snapshot_payload(
    snapshot: Snapshot,
    allowed_labels: list[str],
    excluded_labels: list[str],
    max_messages: int,
) -> dict:
    return snapshot.to_dict(
        allowed_labels=allowed_labels,
        excluded_labels=excluded_labels,
        max_recent_messages=max_messages,
    )


def _write_snapshot(payload: dict, storage_dir: Path, *, folder: str = "snapshots") -> Path:
    output_dir = storage_dir / folder
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"{timestamp}.json"
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temp_path.replace(path)
    return path


def _write_atomic_json(payload: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        backup_dir = output_path.parent / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{output_path.stem}.{backup_timestamp}{output_path.suffix}"
        suffix = 1
        while backup_path.exists():
            backup_path = backup_dir / f"{output_path.stem}.{backup_timestamp}-{suffix}{output_path.suffix}"
            suffix += 1
        backup_path.write_text(output_path.read_text())
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temp_path.replace(output_path)
    return output_path


def _read_export_summary(output_path: Path) -> dict:
    summary = {
        "path": str(output_path),
        "exists": output_path.exists(),
        "threadCount": 0,
        "sizeBytes": 0,
        "updatedAt": None,
    }
    if not output_path.exists():
        return summary
    stat = output_path.stat()
    summary["sizeBytes"] = stat.st_size
    summary["updatedAt"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()
    try:
        payload = json.loads(output_path.read_text())
    except Exception as exc:
        summary["parseError"] = str(exc)
        return summary
    threads = payload.get("threads", [])
    summary["threadCount"] = len(threads) if isinstance(threads, list) else 0
    summary["exportedAt"] = payload.get("exportedAt")
    return summary


def _status_payload(*, profile_dir: Path, debug_port: int, marker_title: str, marker_url_substring: str, target_url: str, output_path: Path) -> dict:
    payload: dict = {
        "mode": "dedicated_profile_status",
        "profileDir": str(profile_dir),
        "debugPort": int(debug_port),
        "markerTitle": marker_title,
        "markerUrlSubstring": marker_url_substring,
        "targetUrl": target_url,
        "checkedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "export": _read_export_summary(output_path),
    }

    target = ChromeTarget(
        marker_title=marker_title,
        marker_url_substring=marker_url_substring,
        target_url_substring=target_url,
    )
    session = ChromeWhatsAppSession(target=target, debug_port=debug_port)
    collector = WhatsAppCollector(session=session)

    try:
        snapshot = collector.collect_snapshot()
    except Exception as exc:
        payload["devtoolsReady"] = False
        payload["collectorReady"] = False
        payload["error"] = str(exc)
        return payload

    payload["devtoolsReady"] = True
    payload["collectorReady"] = True
    payload["page"] = {
        "title": snapshot.page_title,
        "url": snapshot.page_url,
    }
    payload["whatsAppLoaded"] = "web.whatsapp.com/" in snapshot.page_url
    payload["whatsAppTitleLooksValid"] = "WhatsApp" in snapshot.page_title
    payload["labelsCount"] = len(snapshot.labels)
    payload["labelNames"] = [label.name for label in snapshot.labels]
    payload["chatRowCount"] = len(snapshot.chat_list)
    payload["hasAnyChatRows"] = bool(snapshot.chat_list)
    payload["sampleChatNames"] = [chat.chat_name for chat in snapshot.chat_list[:5]]
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatsapp-collector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--write", action="store_true")
    snapshot.add_argument("--allow-label", action="append", default=[])
    snapshot.add_argument("--exclude-label", action="append", default=[])
    snapshot.add_argument("--max-messages", type=int, default=MAX_MESSAGE_LOOKBACK_HARD_LIMIT)
    snapshot.add_argument("--storage-dir", default="storage")

    labels = subparsers.add_parser("labels")
    labels.add_argument("--storage-dir", default="storage")

    chat_list = subparsers.add_parser("chat-list")
    chat_list.add_argument("--storage-dir", default="storage")

    labeled_threads = subparsers.add_parser("labeled-threads")
    labeled_threads.add_argument("--storage-dir", default="storage")
    labeled_threads.add_argument("--write", action="store_true")
    labeled_threads.add_argument("--allow-label", action="append", default=[])
    labeled_threads.add_argument("--exclude-label", action="append", default=[])
    labeled_threads.add_argument("--max-messages", type=int, default=MAX_MESSAGE_LOOKBACK_HARD_LIMIT)

    events = subparsers.add_parser("events")
    events.add_argument("--storage-dir", default="storage")
    events.add_argument("--write", action="store_true")
    events.add_argument("--allow-label", action="append", default=[])
    events.add_argument("--exclude-label", action="append", default=[])
    events.add_argument("--max-messages", type=int, default=MAX_MESSAGE_LOOKBACK_HARD_LIMIT)

    dashboard_export = subparsers.add_parser("dashboard-export")
    dashboard_export.add_argument(
        "--output",
        default="output/whatsapp-dashboard-export.json",
    )
    dashboard_export.add_argument("--account-label", default="WhatsApp")
    dashboard_export.add_argument("--allow-label", action="append", default=[])
    dashboard_export.add_argument("--exclude-label", action="append", default=[])
    dashboard_export.add_argument("--max-messages", type=int, default=MAX_MESSAGE_LOOKBACK_HARD_LIMIT)

    ensure_window = subparsers.add_parser("ensure-window")
    for window_parser in (ensure_window,):
        window_parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
        window_parser.add_argument("--placement-mode", choices=["edge-hidden", "visible"], default=DEFAULT_PLACEMENT_MODE)
        window_parser.add_argument(
            "--profile-dir",
            default=str(DEFAULT_PROFILE_DIR),
        )
        window_parser.add_argument("--marker-title", default=DEFAULT_MARKER_TITLE)
        window_parser.add_argument("--marker-url-substring", default=DEFAULT_MARKER_URL_SUBSTRING)
        window_parser.add_argument("--target-url", default=DEFAULT_TARGET_URL)
        window_parser.add_argument("--debug-port", type=int, default=DEFAULT_DEBUG_PORT)
        window_parser.add_argument("--settle-seconds", type=float, default=DEFAULT_SETTLE_SECONDS)

    quit_profile = subparsers.add_parser("quit-profile")
    quit_profile.add_argument(
        "--profile-dir",
        default=str(DEFAULT_PROFILE_DIR),
    )

    ui = subparsers.add_parser("ui")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    ui.add_argument("--output", default="output/whatsapp-dashboard-export.json")
    ui.add_argument("--debug-port", type=int, default=DEFAULT_DEBUG_PORT)
    ui.add_argument("--marker-title", default=DEFAULT_MARKER_TITLE)
    ui.add_argument("--marker-url-substring", default=DEFAULT_MARKER_URL_SUBSTRING)
    ui.add_argument("--target-url", default=DEFAULT_TARGET_URL)
    ui.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    ui.add_argument("--account-label", default="WhatsApp")
    ui.add_argument("--max-messages", type=int, default=MAX_MESSAGE_LOOKBACK_HARD_LIMIT)
    ui.add_argument("--open-browser", action="store_true")

    status = subparsers.add_parser("status")
    status.add_argument(
        "--profile-dir",
        default=str(DEFAULT_PROFILE_DIR),
    )
    status.add_argument("--debug-port", type=int, default=DEFAULT_DEBUG_PORT)
    status.add_argument("--marker-title", default=DEFAULT_MARKER_TITLE)
    status.add_argument("--marker-url-substring", default=DEFAULT_MARKER_URL_SUBSTRING)
    status.add_argument("--target-url", default=DEFAULT_TARGET_URL)
    status.add_argument("--output", default="output/whatsapp-dashboard-export.json")
    return parser


def main(argv: Sequence[str] | None = None, *, collector: WhatsAppCollector | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    collector = collector or WhatsAppCollector()
    storage_dir = Path(getattr(args, "storage_dir", "storage"))
    excluded_labels = _merged_excluded_labels(getattr(args, "exclude_label", []))
    max_messages = max(int(getattr(args, "max_messages", MAX_MESSAGE_LOOKBACK_HARD_LIMIT)), 1)

    if args.command == "labels":
        payload = {"labels": [label.__dict__ for label in collector.collect_labels()]}
    elif args.command == "chat-list":
        payload = {"chat_list": [chat.__dict__ for chat in collector.collect_chat_list()]}
    elif args.command == "labeled-threads":
        payload = {
            "labeled_threads": [
                asdict(thread) for thread in collector.collect_labeled_threads(
                    allow_labels=args.allow_label,
                    exclude_labels=excluded_labels,
                    max_messages=max_messages,
                )
            ],
            "allowed_labels": args.allow_label,
            "excluded_labels": excluded_labels,
            "max_recent_messages": max_messages,
        }
        if args.write:
            payload["written_to"] = str(_write_snapshot(payload, storage_dir, folder="labeled_threads"))
    elif args.command == "events":
        payload = {
            "events": [
                asdict(event) for event in collector.collect_events(
                    allow_labels=args.allow_label,
                    exclude_labels=excluded_labels,
                    max_messages=max_messages,
                )
            ],
            "allowed_labels": args.allow_label,
            "excluded_labels": excluded_labels,
            "max_recent_messages": max_messages,
        }
        if args.write:
            payload["written_to"] = str(_write_snapshot(payload, storage_dir, folder="events"))
    elif args.command == "dashboard-export":
        payload = collector.collect_dashboard_export(
            account_label=args.account_label,
            allow_labels=args.allow_label,
            exclude_labels=excluded_labels,
            max_messages=max_messages,
        )
        payload["written_to"] = str(_write_atomic_json(payload, Path(args.output)))
    elif args.command == "ensure-window":
        payload = ensure_dedicated_whatsapp_window(
            display_name=args.display_name,
            placement_mode=args.placement_mode,
            settle_seconds=args.settle_seconds,
            profile_dir=Path(args.profile_dir).expanduser(),
            marker_title=args.marker_title,
            marker_url_substring=args.marker_url_substring,
            target_url=args.target_url,
            debug_port=args.debug_port,
        )
    elif args.command == "quit-profile":
        profile_dir = Path(args.profile_dir).expanduser()
        terminate_profile_processes(profile_dir)
        payload = {
            "mode": "quit_profile",
            "profileDir": str(profile_dir),
        }
    elif args.command == "ui":
        run_ui_server(
            UIConfig(
                output_path=Path(args.output).expanduser(),
                profile_dir=Path(args.profile_dir).expanduser(),
                host=args.host,
                port=args.port,
                debug_port=args.debug_port,
                marker_title=args.marker_title,
                marker_url_substring=args.marker_url_substring,
                target_url=args.target_url,
                display_name=args.display_name,
                account_label=args.account_label,
                max_messages=max_messages,
            ),
            open_browser=args.open_browser,
        )
        return 0
    elif args.command == "status":
        payload = _status_payload(
            profile_dir=Path(args.profile_dir).expanduser(),
            debug_port=args.debug_port,
            marker_title=args.marker_title,
            marker_url_substring=args.marker_url_substring,
            target_url=args.target_url,
            output_path=Path(args.output).expanduser(),
        )
    else:
        payload = collector.collect_full_snapshot(
            allow_labels=args.allow_label,
            exclude_labels=excluded_labels,
            max_messages=max_messages,
        )
        if args.write:
            payload["written_to"] = str(_write_snapshot(payload, storage_dir))

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
