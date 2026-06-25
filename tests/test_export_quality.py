from __future__ import annotations

import json
from pathlib import Path

from whatsapp_collector.export_quality import (
    ExportQualityError,
    assess_export_quality,
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
