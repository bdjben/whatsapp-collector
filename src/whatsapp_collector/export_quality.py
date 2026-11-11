from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from whatsapp_collector.message_expiry import complete_live_capture, confirmed_empty, epoch, expected_expired


LATEST_CONTENT_UNAVAILABLE_THREAD_LIMIT = 2


class ExportQualityError(RuntimeError):
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(quality_error_message(report))


def _incomplete_chat_problem(thread: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _object(thread.get("sourceDiagnostics"))
    state = _object(diagnostics.get("captureState"))
    missing_count = _positive_int(state.get("missingRequiredMessageCount"))
    reasons = set(state.get("incompleteReasons") or [])
    details: list[str] = []
    if missing_count:
        noun = "message" if missing_count == 1 else "messages"
        details.append(f"{missing_count} recent {noun} could not be captured.")
    if state.get("activeLoading") or "active-loading" in reasons:
        details.append("WhatsApp was still loading the conversation.")
    elif missing_count and state.get("activeLoading") is False and state.get("contextLoaded"):
        details.append("WhatsApp had finished loading, but the collector could not read all required content.")
    if any(state.get(flag) for flag in ("waitTimedOut", "timedOut", "timeout")):
        details.append("The capture deadline was reached before the conversation could be verified.")
    if "disconnected" in reasons:
        details.append("WhatsApp lost its connection during capture.")
    if "unresolved-dom-message-models" in reasons:
        details.append("Some visible message bubbles could not be read.")
    if "missing-chat-identity" in reasons:
        details.append("The opened conversation's identity could not be verified.")
    if "context-not-loaded" in reasons:
        details.append("WhatsApp did not finish initializing the conversation.")
    if "unknown-model-coverage" in reasons:
        details.append("The collector could not verify WhatsApp's recent-message list.")
    if not details:
        details.append("The collector could not verify that all required recent messages were captured.")
    attempts = _positive_int(diagnostics.get("captureAttempts"))
    if attempts > 1:
        details.append(f"This remained incomplete after {attempts} capture attempts.")

    # Retain identifiers and media metadata, never message bodies, for user-facing failures.
    problems: dict[str, dict[str, Any]] = {}
    for missing in state.get("missingRequiredMessages") or []:
        if not isinstance(missing, dict):
            continue
        problem = {key: missing.get(key) for key in (
            "messageId", "messageType", "timestamp", "parentMessageId", "fileName", "sizeBytes")}
        problem["reason"] = "not-captured"
        problems[str(problem.get("messageId") or len(problems))] = problem
    for message in _thread_messages(thread):
        for attachment in message.get("attachments") or []:
            if not isinstance(attachment, dict) or attachment.get("status") != "notDownloaded":
                continue
            message_id = attachment.get("sourceMessageId") or message.get("messageId")
            key = str(message_id or attachment.get("attachmentId") or len(problems))
            problem = problems.setdefault(key, {
                "messageId": message_id, "messageType": attachment.get("kind") or message.get("messageType"),
                "timestamp": message.get("timestamp"), "reason": "attachment-not-downloaded",
            })
            for name in ("fileName", "sizeBytes"):
                if not problem.get(name):
                    problem[name] = attachment.get(name)
            problem["downloadFailure"] = attachment.get("skippedReason")
    return {
        "chatTitle": _thread_title(thread), "threadKey": thread.get("threadKey"),
        "reason": "live-chat-capture-incomplete", "detail": " ".join(details),
        "messageProblems": list(problems.values()), "sourceDiagnostics": diagnostics,
    }


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
    keys = Counter(str(thread.get("threadKey") or "").strip() for thread in thread_items if isinstance(thread, dict))
    duplicates = [key for key, count in keys.items() if key and count > 1]
    if duplicates:
        issues.append({"code": "duplicate-thread-ids", "severity": "error",
                       "detail": "Multiple records have the same resolved chat identity.", "threadKeys": duplicates})
    if keys.get("", 0):
        issues.append({"code": "missing-thread-id", "severity": "error", "count": keys[""]})
    empty_count = sum(confirmed_empty(_object(thread.get("sourceDiagnostics")))
                      for thread in thread_items if isinstance(thread, dict) and not _thread_messages(thread))
    unverified_empty_threads = [{"threadKey": thread.get("threadKey"), "chatTitle": _thread_title(thread)}
                               for thread in thread_items if isinstance(thread, dict) and not _thread_messages(thread)
                               and not confirmed_empty(_object(thread.get("sourceDiagnostics")))]
    if unverified_empty_threads:
        issues.append({"code": "unverified-empty-thread", "severity": "error", "threads": unverified_empty_threads,
                       "detail": "An empty exported thread lacks a completed identity-verified live capture."})
    raw_export_warnings = payload.get("exportWarnings")
    export_warnings = [
        str(warning)
        for warning in (raw_export_warnings if isinstance(raw_export_warnings, list) else [])
        if isinstance(warning, str) and warning.strip()
    ]

    for thread in thread_items:
        if not isinstance(thread, dict):
            continue
        for view in set(thread.get("sourceViews") or [thread.get("sourceView") or "labeled/forced"]):
            source_view_counts[str(view)] += 1
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
    if thread_count > 0 and message_count <= 0 and empty_count != thread_count:
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

    label_collection_warnings = [
        warning for warning in export_warnings if warning.startswith("labeled-thread-export-skipped:")
    ]
    exclusion_filter_warnings = [
        warning for warning in export_warnings if warning.startswith("excluded-label-filter-skipped:")
    ]
    labeled_message_warnings = [
        warning for warning in export_warnings if warning.startswith("labeled-message-capture-failed:")
    ]
    stale_cached_threads = [
        {"chatTitle": _thread_title(thread), "threadKey": thread.get("threadKey"),
         "issues": _object(thread.get("sourceDiagnostics")).get("issues", [])}
        for thread in thread_items if isinstance(thread, dict)
        if any(issue.get("code") == "cached-history-still-stale"
               for issue in _object(thread.get("sourceDiagnostics")).get("issues", []) if isinstance(issue, dict))
    ]
    incomplete_live_threads = [
        _incomplete_chat_problem(thread)
        for thread in thread_items if isinstance(thread, dict)
        if _object(thread.get("sourceDiagnostics")).get("captureIncomplete")
        or any(_object(_object(thread.get("sourceDiagnostics")).get("captureState")).get(flag)
               for flag in ("isLoading", "waitTimedOut", "timedOut", "timeout"))
    ]
    if incomplete_live_threads:
        issues.append({
            "code": "live-chat-capture-incomplete", "severity": "error", "threads": incomplete_live_threads,
            "detail": "Recent-message capture could not be verified. Each affected chat lists its cause and any missing messages or attachments.",
        })
    identity_conflicts = [finding for finding in _object(payload.get("exportDiagnostics")).get("identityConsolidation", [])
                          if any(conflict.get("code") in {"matching-message-timestamp-conflict", "newer-evidence-outside-complete-capture"}
                                 for conflict in finding.get("conflicts", []))]
    if identity_conflicts:
        issues.append({"code": "conflicting-message-identity", "severity": "error", "threads": identity_conflicts,
                       "detail": "Records for one chat disagree on a message timestamp; the collector cannot resolve this safely."})
    unconfirmed_empty = [thread for thread in _object(payload.get("exportDiagnostics")).get("skippedThreads", [])
                         if any(issue.get("code") == "empty-chat-not-confirmed"
                                for issue in _object(thread.get("sourceDiagnostics")).get("issues", []) if isinstance(issue, dict))]
    if unconfirmed_empty:
        issues.append({"code": "empty-chat-capture-unverified", "severity": "error", "threads": unconfirmed_empty,
                       "detail": "A selected conversation returned no messages without positive proof that its live history is empty."})
    if stale_cached_threads:
        issues.append({
            "code": "cached-history-refresh-incomplete", "severity": "error",
            "detail": "Cached messages are older than known chat recency and could not be refreshed from the live conversation.",
            "threads": stale_cached_threads,
        })
    comparison = _object(_object(payload.get("exportDiagnostics")).get("historyComparison"))
    regressions = comparison.get("unverifiedRegressions", [])
    if regressions:
        issues.append({
            "code": "unverified-history-regression", "severity": "error",
            "detail": "A conversation regressed against the previous export without a successful live message capture.",
            "threads": regressions,
        })
    attachment_items = [attachment
                        for thread in thread_items if isinstance(thread, dict)
                        for message in _thread_messages(thread)
                        for attachment in message.get("attachments", []) if isinstance(attachment, dict)]
    missing_attachments = [attachment for attachment in attachment_items if attachment.get("status") != "downloaded"]
    if missing_attachments:
        issues.append({
            "code": "attachments-not-downloaded", "severity": "warning",
            "detail": "Message content includes attachment metadata, but some attachment bytes were not retained.",
            "count": len(missing_attachments),
            "reasonCounts": dict(Counter(str(item.get("skippedReason") or "unknown") for item in missing_attachments)),
        })
    live_regressions = comparison.get("liveCapturedRegressions", [])
    if live_regressions:
        issues.append({
            "code": "history-changed-after-live-capture", "severity": "warning",
            "detail": "Live-captured history is shorter or older than the previous export; messages may have been removed or expired.",
            "threads": live_regressions,
        })
    if comparison.get("expectedExpirations"):
        issues.append({"code": "expected-message-expiration", "severity": "info",
                       "detail": "Missing messages passed their observed expiry deadlines and were not marked kept.",
                       "threads": comparison["expectedExpirations"]})
    if empty_count:
        issues.append({"code": "confirmed-empty-chats", "severity": "info", "count": empty_count,
                       "detail": "Identity-verified live conversations finished loading and contain no exportable messages."})
    if payload.get("allowLabels") and label_collection_warnings:
        issues.append(
            {
                "code": "configured-label-collection-incomplete",
                "severity": "error",
                "detail": (
                    "The export did not complete its configured Always Include label collection, so labeled chats may be missing."
                ),
                "warnings": label_collection_warnings[:5],
            }
        )
    if payload.get("excludeLabels") and exclusion_filter_warnings:
        issues.append(
            {
                "code": "configured-label-exclusion-incomplete",
                "severity": "error",
                "detail": (
                    "The export did not complete its configured Never Include label filter, so excluded chats may be present."
                ),
                "warnings": exclusion_filter_warnings[:5],
            }
        )
    if payload.get("allowLabels") and labeled_message_warnings:
        prior_report = payload.get("exportQuality")
        prior_issues = prior_report.get("issues", []) if isinstance(prior_report, dict) else []
        failed_threads = next((
            issue.get("threads", []) for issue in prior_issues
            if isinstance(issue, dict) and issue.get("code") == "configured-labeled-chat-message-capture-incomplete"
        ), [])
        issues.append(
            {
                "code": "configured-labeled-chat-message-capture-incomplete",
                "severity": "error",
                "detail": "At least one non-locked Always Include chat could not be opened to capture its recent messages.",
                "warnings": labeled_message_warnings[:5],
                **({"threads": failed_threads} if failed_threads else {}),
            }
        )

    return {
        "ok": not any(issue.get("severity") == "error" for issue in issues),
        "rulesVersion": 5,
        "attachmentCompleteness": {
            "complete": not missing_attachments,
            "detected": len(attachment_items),
            "downloaded": len(attachment_items) - len(missing_attachments),
            "notDownloaded": len(missing_attachments),
        },
        "thresholds": {
            "latestContentUnavailableThreadLimit": LATEST_CONTENT_UNAVAILABLE_THREAD_LIMIT,
            "perThreadContentUnavailableFraction": "1/3 rounded up",
            "textAvailableFalseWithCapturedAttachment": "allowed",
            "configuredLabelRulePhaseFailures": "not allowed",
        },
        "metrics": {
            "threadCount": thread_count,
            "messageCount": message_count,
            "confirmedEmptyThreadCount": empty_count,
            "expectedExpiredMessageCount": comparison.get("expectedExpiredMessageCount", 0),
            "unexplainedMissingMessageCount": comparison.get("unexplainedMissingMessageCount", 0),
            "textAvailableCount": text_available_count,
            "textUnavailableCount": text_unavailable_count,
            "contentAvailableCount": content_available_count,
            "contentUnavailableCount": content_unavailable_count,
            "latestContentUnavailableThreadCount": len(latest_content_unavailable_threads),
            "sourceViewCounts": dict(source_view_counts),
            "selectionContractWarningCount": len(
                label_collection_warnings + exclusion_filter_warnings + labeled_message_warnings
            ),
        },
        # Existing downstream consumers treat every issue as a blocking error.
        "issues": [issue for issue in issues if issue.get("severity") == "error"],
        "observations": [issue for issue in issues if issue.get("severity") != "error"],
    }


def compare_export_history(payload: dict[str, Any], previous_export: dict[str, Any]) -> dict[str, Any]:
    previous_threads: dict[str, list[dict[str, Any]]] = {}
    for thread in previous_export.get("threads", []):
        if isinstance(thread, dict) and thread.get("threadKey"):
            previous_threads.setdefault(str(thread["threadKey"]), []).append(thread)
    unverified: list[dict[str, Any]] = []
    live_captured: list[dict[str, Any]] = []
    expirations: list[dict[str, Any]] = []
    window_shifts: list[dict[str, Any]] = []
    expired_count = missing_count = 0
    comparable_counts = payload.get("maxRecentMessages") == previous_export.get("maxRecentMessages")
    limit = _positive_int(payload.get("maxRecentMessages"))
    selected = list(payload.get("threads", []))
    present = {str(t.get("threadKey")) for t in selected if isinstance(t, dict)}
    for skipped in _object(payload.get("exportDiagnostics")).get("skippedThreads", []):
        if skipped.get("threadKey") and str(skipped["threadKey"]) not in present and skipped.get("reason") != "locked-chat":
            selected.append({**skipped, "messages": [], "recentMessages": []})
    for thread in selected:
        if not isinstance(thread, dict):
            continue
        previous_records = previous_threads.get(str(thread.get("threadKey")))
        if not previous_records:
            continue
        previous = previous_records[0]
        old_by_id = {str(message.get("messageId")): message for record in previous_records
                     for message in _thread_messages(record) if message.get("messageId")}
        old_messages = sorted(old_by_id.values(), key=lambda m: epoch(m.get("timestamp")) or 0, reverse=True)
        if limit:
            old_messages = old_messages[:limit]
        messages = _thread_messages(thread)
        latest, old_latest = _latest_message_epoch(messages), _latest_message_epoch(old_messages)
        older = old_latest is not None and (latest is None or latest < old_latest - 1)
        shorter = comparable_counts and len(messages) < len(old_messages)
        diagnostics = _object(thread.get("sourceDiagnostics"))
        if any(issue.get("code") == "locked-chat-not-opened" for issue in diagnostics.get("issues", []) if isinstance(issue, dict)):
            continue
        current_ids = {str(message.get("messageId")) for message in messages if message.get("messageId")}
        absent = [message for message in old_messages if str(message.get("messageId")) not in current_ids]
        oldest_current = min((epoch(m.get("timestamp")) for m in messages if epoch(m.get("timestamp"))), default=None)
        outside_window = [m for m in absent if limit and len(messages) >= limit and oldest_current
                          and epoch(m.get("timestamp")) and epoch(m["timestamp"]) < oldest_current]
        absent = [m for m in absent if m not in outside_window]
        expired = [m for m in absent if expected_expired(m, payload.get("exportedAt"))]
        unexplained = [m for m in absent if m not in expired]
        if not comparable_counts and not older:
            unexplained = []
        expired_count += len(expired)
        missing_count += len(unexplained)
        if not older and not shorter and not absent:
            if outside_window:
                window_shifts.append({"threadKey": thread.get("threadKey"), "messageCount": len(outside_window)})
            continue
        if not comparable_counts and not older and not expired:
            continue
        freshly_captured = complete_live_capture(diagnostics, limit)
        finding = {
            "chatTitle": _thread_title(thread), "threadKey": thread.get("threadKey"),
            "previousMessageCount": len(old_messages), "currentMessageCount": len(messages),
            "previousLatestAt": previous.get("lastMessageAt"), "currentLatestAt": thread.get("lastMessageAt"),
            "latestTimestampRegressed": older, "messageCountDecreased": shorter,
            "expectedExpiredMessageCount": len(expired), "unexplainedMissingMessageCount": len(unexplained),
            "outsideRecentWindowMessageCount": len(outside_window), "confirmedEmpty": confirmed_empty(diagnostics),
            "expiryEvidenceMissingCount": sum(not m.get("expiry") for m in unexplained),
            "missingMessages": [{"messageId": m.get("messageId"), "timestamp": m.get("timestamp"),
                                 "reason": "expected-expiration" if m in expired else "unexplained",
                                 "expiry": m.get("expiry")} for m in absent],
        }
        finding["classification"] = ("expected-expiration" if expired and not unexplained else
                                     "confirmed-live-history-change" if freshly_captured else "unverified-loss")
        if expired and not unexplained:
            expirations.append(finding)
        else:
            (live_captured if freshly_captured else unverified).append(finding)
    return {
        "previousExportedAt": previous_export.get("exportedAt"),
        "unverifiedRegressions": unverified, "liveCapturedRegressions": live_captured,
        "expectedExpirations": expirations, "recentWindowShifts": window_shifts,
        "expectedExpiredMessageCount": expired_count, "unexplainedMissingMessageCount": missing_count,
        "expiryBoundaryGraceSeconds": 120,
    }


def _latest_message_epoch(messages: list[dict[str, Any]]) -> float | None:
    timestamps: list[float] = []
    for message in messages:
        try:
            timestamps.append(datetime.fromisoformat(str(message.get("timestamp") or "").replace("Z", "+00:00")).timestamp())
        except (ValueError, OverflowError, OSError):
            continue
    return max(timestamps) if timestamps else None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def attach_export_quality(payload: dict[str, Any], *, previous_export: dict[str, Any] | None = None) -> dict[str, Any]:
    if previous_export:
        payload.setdefault("exportDiagnostics", {})["historyComparison"] = compare_export_history(payload, previous_export)
    report = assess_export_quality(payload)
    payload["exportQuality"] = report
    return report


def validate_export_quality(payload: dict[str, Any], *, previous_export: dict[str, Any] | None = None) -> dict[str, Any]:
    report = attach_export_quality(payload, previous_export=previous_export)
    if not report["ok"]:
        raise ExportQualityError(report)
    return report


def quality_error_message(report: dict[str, Any]) -> str:
    issues = report.get("issues") if isinstance(report, dict) else None
    if not isinstance(issues, list) or not issues:
        return "WhatsApp export failed quality validation."
    codes = [str(issue.get("code") or "unknown") for issue in issues if isinstance(issue, dict)]
    message = "WhatsApp export failed quality validation: " + ", ".join(codes)
    failed_chats: dict[str, str] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        for thread in issue.get("threads", []):
            if not isinstance(thread, dict):
                continue
            name = str(thread.get("chatTitle") or thread.get("threadKey") or "Unknown chat")
            failed_chats[name] = str(thread.get("detail") or issue.get("detail") or "Capture could not be verified.")
        for name in issue.get("chatTitles", []) or issue.get("threadKeys", []):
            failed_chats.setdefault(str(name), str(issue.get("detail") or "Capture could not be verified."))
    if failed_chats:
        message += ". " + " ".join(f"{name}: {detail}" for name, detail in list(failed_chats.items())[:5])
        if len(failed_chats) > 5:
            message += f" Also affected: {len(failed_chats) - 5} more chats."
    return message


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
