from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any


LATEST_CONTENT_UNAVAILABLE_THREAD_LIMIT = 2


class ExportQualityError(RuntimeError):
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(quality_error_message(report))


def assess_export_quality(payload: dict[str, Any]) -> dict[str, Any]:
    threads = payload.get("threads")
    thread_items = threads if isinstance(threads, list) else []
    issues: list[dict[str, Any]] = []
    source_view_counts: Counter[str] = Counter()
    latest_content_unavailable_threads: list[str] = []
    per_thread_content_unavailable_violations: list[dict[str, Any]] = []
    thread_count = len(thread_items)
    message_count = 0
    text_available_count = 0
    text_unavailable_count = 0
    content_available_count = 0
    content_unavailable_count = 0

    for thread in thread_items:
        if not isinstance(thread, dict):
            continue
        source_view_counts[str(thread.get("sourceView") or "labeled/forced")] += 1
        messages = _thread_messages(thread)
        if not messages:
            continue
        message_count += len(messages)
        text_unavailable = [message for message in messages if _message_text_unavailable(message)]
        content_unavailable = [message for message in messages if _message_content_unavailable(message)]
        text_available_count += len(messages) - len(text_unavailable)
        text_unavailable_count += len(text_unavailable)
        content_available_count += len(messages) - len(content_unavailable)
        content_unavailable_count += len(content_unavailable)
        latest = messages[0]
        if _message_content_unavailable(latest):
            latest_content_unavailable_threads.append(_thread_title(thread))
        threshold = math.ceil(len(messages) / 3)
        if len(content_unavailable) > threshold:
            per_thread_content_unavailable_violations.append(
                {
                    "chatTitle": _thread_title(thread),
                    "threadKey": str(thread.get("threadKey") or ""),
                    "messageCount": len(messages),
                    "contentUnavailableCount": len(content_unavailable),
                    "allowedContentUnavailableCount": threshold,
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
    if message_count > 0 and content_available_count <= 0:
        issues.append(
            {
                "code": "no-content-captured",
                "severity": "error",
                "detail": "The export contained message records, but none had readable text or captured attachment metadata.",
            }
        )
    if len(latest_content_unavailable_threads) > LATEST_CONTENT_UNAVAILABLE_THREAD_LIMIT:
        issues.append(
            {
                "code": "too-many-latest-messages-without-content",
                "severity": "error",
                "detail": (
                    "More than two exported threads have no readable text and no captured attachment metadata "
                    "on their latest message. This usually means WhatsApp Web was not ready or the collector "
                    "fell back to stale data."
                ),
                "limit": LATEST_CONTENT_UNAVAILABLE_THREAD_LIMIT,
                "count": len(latest_content_unavailable_threads),
                "chatTitles": latest_content_unavailable_threads[:20],
            }
        )
    if per_thread_content_unavailable_violations:
        issues.append(
            {
                "code": "thread-content-unavailable-ratio-too-high",
                "severity": "error",
                "detail": (
                    "At least one exported thread has neither readable text nor captured attachment metadata on "
                    "more than one third of its messages, rounded up to the nearest whole message."
                ),
                "threads": per_thread_content_unavailable_violations[:20],
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
        "rulesVersion": 2,
        "thresholds": {
            "latestContentUnavailableThreadLimit": LATEST_CONTENT_UNAVAILABLE_THREAD_LIMIT,
            "perThreadContentUnavailableFraction": "1/3 rounded up",
            "textAvailableFalseWithCapturedAttachment": "allowed",
        },
        "metrics": {
            "threadCount": thread_count,
            "messageCount": message_count,
            "textAvailableCount": text_available_count,
            "textUnavailableCount": text_unavailable_count,
            "contentAvailableCount": content_available_count,
            "contentUnavailableCount": content_unavailable_count,
            "latestContentUnavailableThreadCount": len(latest_content_unavailable_threads),
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
    from whatsapp_collector.export_safety import ensure_last_good_export

    recovery = ensure_last_good_export(output_path)
    if recovery.status == "restored-backup":
        return recovery.source_path
    return None


def _thread_messages(thread: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("recentMessages", "messages"):
        value = thread.get(key)
        if isinstance(value, list):
            return [message for message in value if isinstance(message, dict)]
    return []


def _message_text_unavailable(message: dict[str, Any]) -> bool:
    if message.get("textAvailable") is False:
        text = message.get("text")
        return not isinstance(text, str) or not text.strip()
    if "textAvailable" not in message:
        text = message.get("text")
        return not isinstance(text, str) or not text.strip()
    return False


def _message_content_unavailable(message: dict[str, Any]) -> bool:
    return not _message_content_available(message)


def _message_content_available(message: dict[str, Any]) -> bool:
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return True
    if message.get("textAvailable") is True:
        return True
    return _message_has_attachment_metadata(message)


def _message_has_attachment_metadata(message: dict[str, Any]) -> bool:
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        return False
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        status = str(attachment.get("status") or "").strip().lower()
        if status in {"failed", "error", "unavailable"}:
            continue
        if any(
            attachment.get(key)
            for key in ("kind", "mimeType", "fileName", "relativePath", "localPath", "attachmentId", "note")
        ):
            return True
    return False


def _thread_title(thread: dict[str, Any]) -> str:
    return str(thread.get("chatTitle") or thread.get("threadKey") or "Unknown")


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
