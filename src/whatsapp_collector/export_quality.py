from __future__ import annotations

import json
import math
import shutil
from collections import Counter
from pathlib import Path
from typing import Any


LATEST_TEXT_UNAVAILABLE_THREAD_LIMIT = 2


class ExportQualityError(RuntimeError):
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(quality_error_message(report))


def assess_export_quality(payload: dict[str, Any]) -> dict[str, Any]:
    threads = payload.get("threads")
    thread_items = threads if isinstance(threads, list) else []
    issues: list[dict[str, Any]] = []
    source_view_counts: Counter[str] = Counter()
    latest_text_unavailable_threads: list[str] = []
    per_thread_text_unavailable_violations: list[dict[str, Any]] = []
    thread_count = len(thread_items)
    message_count = 0
    text_available_count = 0
    text_unavailable_count = 0

    for thread in thread_items:
        if not isinstance(thread, dict):
            continue
        source_view_counts[str(thread.get("sourceView") or "labeled/forced")] += 1
        messages = _thread_messages(thread)
        if not messages:
            continue
        message_count += len(messages)
        unavailable = [message for message in messages if _message_text_unavailable(message)]
        available = len(messages) - len(unavailable)
        text_available_count += available
        text_unavailable_count += len(unavailable)
        latest = messages[0]
        if _message_text_unavailable(latest):
            latest_text_unavailable_threads.append(_thread_title(thread))
        threshold = math.ceil(len(messages) / 3)
        if len(unavailable) > threshold:
            per_thread_text_unavailable_violations.append(
                {
                    "chatTitle": _thread_title(thread),
                    "threadKey": str(thread.get("threadKey") or ""),
                    "messageCount": len(messages),
                    "textUnavailableCount": len(unavailable),
                    "allowedTextUnavailableCount": threshold,
                }
            )

    if thread_count <= 0:
        issues.append(
            {
                "code": "no-threads-captured",
                "severity": "error",
                "detail": "The export did not contain any threads.",
            }
        )
    if thread_count > 0 and message_count <= 0:
        issues.append(
            {
                "code": "no-messages-captured",
                "severity": "error",
                "detail": "The export contained threads but no recentMessages/messages arrays with message records.",
            }
        )
    if message_count > 0 and text_available_count <= 0:
        issues.append(
            {
                "code": "no-text-captured",
                "severity": "error",
                "detail": "The export contained message records, but none had readable text.",
            }
        )
    if len(latest_text_unavailable_threads) > LATEST_TEXT_UNAVAILABLE_THREAD_LIMIT:
        issues.append(
            {
                "code": "too-many-latest-messages-without-text",
                "severity": "error",
                "detail": (
                    "More than two exported threads have textAvailable=false on their latest message. "
                    "This usually means WhatsApp Web was not ready or the collector fell back to stale media-only data."
                ),
                "limit": LATEST_TEXT_UNAVAILABLE_THREAD_LIMIT,
                "count": len(latest_text_unavailable_threads),
                "chatTitles": latest_text_unavailable_threads[:20],
            }
        )
    if per_thread_text_unavailable_violations:
        issues.append(
            {
                "code": "thread-text-unavailable-ratio-too-high",
                "severity": "error",
                "detail": (
                    "At least one exported thread has textAvailable=false on more than one third of its messages, "
                    "rounded up to the nearest whole message."
                ),
                "threads": per_thread_text_unavailable_violations[:20],
            }
        )

    max_all_chats = _positive_int(payload.get("maxAllViewChats"))
    indexeddb_count = source_view_counts.get("indexeddb-recent", 0)
    all_view_count = source_view_counts.get("all", 0)
    if max_all_chats > 0 and indexeddb_count > 0 and all_view_count <= 0:
        issues.append(
            {
                "code": "indexeddb-fallback-without-all-view-capture",
                "severity": "error",
                "detail": (
                    "The export included IndexedDB fallback threads but captured zero visible All-view threads. "
                    "This is treated as degraded because IndexedDB recency is not enough proof of the user's requested All-view horizon."
                ),
                "indexedDbFallbackThreadCount": indexeddb_count,
            }
        )

    return {
        "ok": not any(issue.get("severity") == "error" for issue in issues),
        "rulesVersion": 1,
        "thresholds": {
            "latestTextUnavailableThreadLimit": LATEST_TEXT_UNAVAILABLE_THREAD_LIMIT,
            "perThreadTextUnavailableFraction": "1/3 rounded up",
        },
        "metrics": {
            "threadCount": thread_count,
            "messageCount": message_count,
            "textAvailableCount": text_available_count,
            "textUnavailableCount": text_unavailable_count,
            "latestTextUnavailableThreadCount": len(latest_text_unavailable_threads),
            "sourceViewCounts": dict(source_view_counts),
        },
        "issues": issues,
    }


def attach_export_quality(payload: dict[str, Any]) -> dict[str, Any]:
    report = assess_export_quality(payload)
    payload["exportQuality"] = report
    return report


def validate_export_quality(payload: dict[str, Any]) -> dict[str, Any]:
    report = attach_export_quality(payload)
    if not report["ok"]:
        raise ExportQualityError(report)
    return report


def quality_error_message(report: dict[str, Any]) -> str:
    issues = report.get("issues") if isinstance(report, dict) else None
    if not isinstance(issues, list) or not issues:
        return "WhatsApp export failed quality validation."
    codes = [str(issue.get("code") or "unknown") for issue in issues if isinstance(issue, dict)]
    return "WhatsApp export failed quality validation: " + ", ".join(codes)


def restore_latest_acceptable_backup(output_path: Path) -> Path | None:
    backup_dir = output_path.parent / "backup"
    if not backup_dir.exists():
        return None
    for candidate in sorted(backup_dir.glob(f"{output_path.stem}.*{output_path.suffix}"), reverse=True):
        try:
            payload = json.loads(candidate.read_text())
        except Exception:
            continue
        if assess_export_quality(payload)["ok"]:
            shutil.copy2(candidate, output_path)
            return candidate
    return None


def _thread_messages(thread: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("recentMessages", "messages"):
        value = thread.get(key)
        if isinstance(value, list):
            return [message for message in value if isinstance(message, dict)]
    return []


def _message_text_unavailable(message: dict[str, Any]) -> bool:
    if message.get("textAvailable") is False:
        return True
    if "textAvailable" not in message:
        text = message.get("text")
        return not isinstance(text, str) or not text.strip()
    return False


def _thread_title(thread: dict[str, Any]) -> str:
    return str(thread.get("chatTitle") or thread.get("threadKey") or "Unknown")


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
