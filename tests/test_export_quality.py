from __future__ import annotations

import json
from pathlib import Path

from whatsapp_collector.export_quality import (
    ExportQualityError,
    assess_export_quality,
    quality_error_message,
    restore_latest_acceptable_backup,
    validate_export_quality,
)


def _thread(title: str, messages: list[dict], *, source_view: str | None = "all") -> dict:
    payload = {
        "threadKey": title.lower().replace(" ", "-"),
        "chatTitle": title,
        "recentMessages": messages,
        "messages": list(messages),
    }
    if source_view:
        payload["sourceView"] = source_view
    return payload


def _message(message_id: str, *, text_available: bool = True, attachments: list[dict] | None = None) -> dict:
    return {
        "messageId": message_id,
        "timestamp": "2026-06-24T20:00:00+00:00",
        "direction": "inbound",
        "sender": "sender",
        "text": "Readable text" if text_available else None,
        "textAvailable": text_available,
        "messageType": "chat" if text_available else "image",
        "subtype": None,
        **({"attachments": attachments} if attachments else {}),
    }


def test_incomplete_capture_failure_retains_state_without_message_bodies() -> None:
    thread = _thread("Example", [_message("m1")])
    thread["messages"][0]["text"] = "PRIVATE MESSAGE CONTENT"
    diagnostics = {"captureIncomplete": True, "captureAttempts": 2,
                   "captureState": {"isLoading": True,
                                    "incompleteReasons": ["missing-required-model-messages"],
                                    "missingRequiredMessageCount": 1}}
    thread["sourceDiagnostics"] = diagnostics
    report = assess_export_quality({"threads": [thread]})
    issue = next(issue for issue in report["issues"] if issue["code"] == "live-chat-capture-incomplete")
    assert issue["threads"][0]["sourceDiagnostics"] == diagnostics
    assert "PRIVATE MESSAGE CONTENT" not in json.dumps(report)


def test_missing_grouped_photo_failure_names_chat_and_attachment_without_claiming_loading() -> None:
    thread = _thread("Photo Group", [_message("photo2", text_available=False, attachments=[{
        "kind": "image", "status": "notDownloaded", "sourceMessageId": "photo2", "sizeBytes": 279234,
        "skippedReason": "download-retry-exhausted", "fileName": "attachment-1.jpg",
    }])])
    thread["sourceDiagnostics"] = {"captureIncomplete": True, "captureAttempts": 2, "captureState": {
        "isLoading": True, "activeLoading": False, "contextLoaded": True,
        "missingRequiredMessageCount": 1, "incompleteReasons": ["missing-required-model-messages"],
        "missingRequiredMessages": [{"messageId": "photo2", "messageType": "image",
            "parentMessageId": "photo1", "timestamp": "2026-11-11T19:35:25Z", "sizeBytes": 279234}],
    }}
    report = assess_export_quality({"threads": [thread]})
    problem = report["issues"][0]["threads"][0]
    assert "1 recent message" in problem["detail"]
    assert "had finished loading" in problem["detail"]
    assert "2 capture attempts" in problem["detail"]
    assert len(problem["messageProblems"]) == 1
    assert problem["messageProblems"][0]["parentMessageId"] == "photo1"
    assert problem["messageProblems"][0]["downloadFailure"] == "download-retry-exhausted"
    assert "Photo Group:" in quality_error_message(report)
    assert "still loading" not in quality_error_message(report)


def test_loading_timeout_and_disconnect_failures_keep_their_specific_causes() -> None:
    thread = _thread("Loading Chat", [_message("m1")])
    thread["sourceDiagnostics"] = {"captureIncomplete": True, "captureState": {
        "activeLoading": True, "waitTimedOut": True, "incompleteReasons": ["disconnected"],
    }}
    report = assess_export_quality({"threads": [thread]})
    message = quality_error_message(report)
    assert "Loading Chat:" in message
    assert "still loading" in message
    assert "deadline" in message
    assert "lost its connection" in message


def test_export_quality_accepts_readable_all_view_export() -> None:
    payload = {
        "maxAllViewChats": 15,
        "threads": [
            _thread("Ana", [_message("a1"), _message("a2")]),
            _thread("Ben", [_message("b1")]),
        ],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is True
    assert report["metrics"]["threadCount"] == 2
    assert report["metrics"]["latestContentUnavailableThreadCount"] == 0


def test_export_quality_accepts_media_only_messages_with_attachment_metadata() -> None:
    payload = {
        "maxAllViewChats": 15,
        "threads": [
            _thread(
                "Ana",
                [
                    _message(
                        "a1",
                        text_available=False,
                        attachments=[
                            {
                                "attachmentId": "att-a1",
                                "kind": "image",
                                "mimeType": "image/jpeg",
                                "fileName": "attachment-1.jpg",
                                "status": "downloaded",
                                "relativePath": "Attachments/Ana/a1/attachment-1.jpg",
                            }
                        ],
                    )
                ],
            )
        ],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is True
    assert report["metrics"]["textUnavailableCount"] == 1
    assert report["metrics"]["contentUnavailableCount"] == 0


def test_export_quality_rejects_more_than_two_latest_messages_without_content() -> None:
    payload = {
        "maxAllViewChats": 15,
        "threads": [
            _thread("One", [_message("m1", text_available=False)]),
            _thread("Two", [_message("m2", text_available=False)]),
            _thread("Three", [_message("m3", text_available=False)]),
        ],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is False
    assert "too-many-latest-messages-without-content" in [issue["code"] for issue in report["issues"]]
    try:
        validate_export_quality(payload)
    except ExportQualityError as exc:
        assert exc.report["metrics"]["latestContentUnavailableThreadCount"] == 3
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected ExportQualityError")


def test_export_quality_rejects_thread_with_more_than_one_third_messages_without_content() -> None:
    payload = {
        "maxAllViewChats": 15,
        "threads": [
            _thread(
                "Ana",
                [
                    _message("m1"),
                    _message("m2", text_available=False),
                    _message("m3", text_available=False),
                    _message("m4", text_available=False),
                ],
            )
        ],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is False
    issue = next(issue for issue in report["issues"] if issue["code"] == "thread-content-unavailable-ratio-too-high")
    assert issue["threads"][0]["messageCount"] == 4
    assert issue["threads"][0]["allowedContentUnavailableCount"] == 2
    assert issue["threads"][0]["contentUnavailableCount"] == 3


def test_export_quality_rejects_indexeddb_fallback_without_all_view_capture() -> None:
    payload = {
        "maxAllViewChats": 30,
        "threads": [_thread("Tuvia Chertok", [_message("m1")], source_view="indexeddb-recent")],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is False
    assert "indexeddb-fallback-without-all-view-capture" in [issue["code"] for issue in report["issues"]]


def test_export_quality_rejects_configured_label_phase_timeout() -> None:
    payload = {
        "allowLabels": ["Clients"],
        "maxAllViewChats": 15,
        "threads": [_thread("Example Contact", [_message("a1")])],
        "exportWarnings": [
            "labeled-thread-export-skipped:RuntimeError:Timed out waiting for Chrome async result"
        ],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is False
    assert "configured-label-collection-incomplete" in [issue["code"] for issue in report["issues"]]
    assert report["metrics"]["selectionContractWarningCount"] == 1


def test_export_quality_rejects_configured_exclusion_phase_timeout() -> None:
    payload = {
        "excludeLabels": ["Low Priority"],
        "maxAllViewChats": 15,
        "threads": [_thread("Example Contact", [_message("a1")])],
        "exportWarnings": [
            "excluded-label-filter-skipped:RuntimeError:Timed out waiting for Chrome async result"
        ],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is False
    assert "configured-label-exclusion-incomplete" in [issue["code"] for issue in report["issues"]]


def test_export_quality_allows_locked_and_empty_labeled_chat_omissions() -> None:
    payload = {
        "allowLabels": ["Clients"],
        "maxAllViewChats": 15,
        "threads": [_thread("Example Contact", [_message("a1")])],
        "exportWarnings": [
            "message-capture-skipped:2",
            "labeled-chat-unavailable-while-locked:1",
            "labeled-chat-without-exportable-messages:1",
        ],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is True
    assert report["metrics"]["selectionContractWarningCount"] == 0


def test_export_quality_rejects_non_locked_labeled_chat_capture_failure() -> None:
    payload = {
        "allowLabels": ["Clients"],
        "maxAllViewChats": 15,
        "threads": [_thread("Example Contact", [_message("a1")])],
        "exportWarnings": ["labeled-message-capture-failed:1"],
    }

    report = assess_export_quality(payload)

    assert report["ok"] is False
    assert "configured-labeled-chat-message-capture-incomplete" in [
        issue["code"] for issue in report["issues"]
    ]


def test_restore_latest_acceptable_backup_skips_degraded_backups(tmp_path: Path) -> None:
    output = tmp_path / "whatsapp-dashboard-export.json"
    backup = tmp_path / "backup"
    backup.mkdir()
    output.write_text(json.dumps({"threads": []}))
    (backup / "whatsapp-dashboard-export.20260624-010000.json").write_text(
        json.dumps(
            {
                "maxAllViewChats": 30,
                "threads": [_thread("Bad", [_message("m1", text_available=False)], source_view="indexeddb-recent")],
            }
        )
    )
    good = backup / "whatsapp-dashboard-export.20260624-020000.json"
    good.write_text(json.dumps({"maxAllViewChats": 30, "threads": [_thread("Good", [_message("m2")])]}))

    restored = restore_latest_acceptable_backup(output)

    assert restored == good
    assert json.loads(output.read_text())["threads"][0]["chatTitle"] == "Good"
