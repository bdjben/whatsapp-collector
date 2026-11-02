from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from whatsapp_collector.collector import WhatsAppCollector
from whatsapp_collector.export_quality import ExportQualityError, validate_export_quality
from whatsapp_collector.export_safety import assess_export_file
from whatsapp_collector.models import IndexedDBThread, Snapshot


def message(index: int, *, timestamp: str = "2026-10-02T10:40:28+00:00") -> dict:
    return {"messageId": f"false_123@lid_m{index}", "timestamp": timestamp,
            "text": f"Message {index}", "textAvailable": True}


def export(messages: list[dict], *, source: str = "all") -> dict:
    return {"exportedAt": "2026-10-03T14:54:34+00:00", "maxRecentMessages": 15, "maxAllViewChats": 31,
            "threads": [{"threadKey": "123@lid", "chatTitle": "Example", "sourceView": source,
                         "lastMessageAt": messages[0]["timestamp"], "messages": messages,
                         "recentMessages": deepcopy(messages)}]}


def test_previous_export_rejects_sparse_stale_snapshot_and_keeps_comparison_on_revalidation():
    previous = export([message(index) for index in range(15)])
    current = export([message(0, timestamp="2026-09-17T09:02:19+00:00")], source="indexeddb-recent")
    current["threads"].append({"threadKey": "other", "sourceView": "all", "messages": [message(99)]})
    with pytest.raises(ExportQualityError, match="unverified-history-regression"):
        validate_export_quality(current, previous_export=previous)
    with pytest.raises(ExportQualityError, match="unverified-history-regression") as error:
        validate_export_quality(current)
    finding = next(issue for issue in error.value.report["issues"] if issue["code"] == "unverified-history-regression")["threads"][0]
    assert finding["previousMessageCount"] == 15
    assert finding["currentMessageCount"] == 1
    assert finding["latestTimestampRegressed"] is True


def test_live_capture_allows_changed_history_but_reports_it():
    previous = export([message(index) for index in range(15)])
    current = export([message(0, timestamp="2026-09-17T09:02:19+00:00")])
    current["threads"][0]["sourceDiagnostics"] = {"openedChatChecked": True, "openedChatMessageCount": 1,
                                                "sourcesUsed": ["opened-chat"], "issues": []}
    report = validate_export_quality(current, previous_export=previous)
    assert report["ok"] is True
    assert any(issue["code"] == "history-changed-after-live-capture" for issue in report["observations"])


def test_changed_lookback_and_missing_horizon_threads_are_not_false_regressions():
    previous = export([message(index) for index in range(15)])
    previous["threads"].append({"threadKey": "outside-current-horizon", "messages": [message(99)]})
    current = export([message(0)])
    current["maxRecentMessages"] = 1
    assert validate_export_quality(current, previous_export=previous)["ok"] is True


def test_locked_chat_is_not_treated_as_capture_regression():
    previous = export([message(index) for index in range(15)])
    current = export([message(0)])
    current["threads"][0]["sourceDiagnostics"] = {"issues": [{"code": "locked-chat-not-opened"}]}
    assert validate_export_quality(current, previous_export=previous)["ok"] is True


def test_attachment_metadata_does_not_claim_complete_media_delivery():
    current = export([message(0)])
    current["threads"][0]["messages"][0]["attachments"] = [{"attachmentId": "att1", "kind": "image",
                                                              "status": "notDownloaded", "skippedReason": "download-retry-exhausted"}]
    current["threads"][0]["recentMessages"] = current["threads"][0]["messages"]
    report = validate_export_quality(current)
    assert report["ok"] is True
    assert report["attachmentCompleteness"] == {"complete": False, "detected": 1, "downloaded": 0, "notDownloaded": 1}
    assert any(issue["code"] == "attachments-not-downloaded" for issue in report["observations"])


def test_successful_media_merge_clears_failure_metadata_in_both_orders():
    missing = {"attachmentId": "a1", "status": "notDownloaded", "skippedReason": "download-retry-exhausted", "note": "Failed earlier"}
    downloaded = {"attachmentId": "a1", "status": "downloaded", "verified": True, "localPath": "/tmp/a1.jpg"}
    for first, second in ((missing, downloaded), (downloaded, missing)):
        merged = WhatsAppCollector._merge_attachment_exports([first], [second])[0]
        assert merged == downloaded


def test_refresh_uses_previous_recency_even_when_cached_history_is_full():
    class Collector(WhatsAppCollector):
        def _opened_chat_recent_messages_for_chat(self, *args, **kwargs):
            return [message(16)]

    collector = Collector(session=object())
    collector._previous_threads = {"123@lid": export([message(0)])["threads"][0]}
    old = [message(index, timestamp="2026-09-17T09:02:19+00:00") for index in range(15)]
    merged, diagnostics = collector._refresh_recent_messages_from_opened_chat(
        "Example", recent_messages=old, max_messages=15, attachments_dir=None, thread_key="123@lid", return_diagnostics=True,
    )
    assert merged[0]["messageId"] == "false_123@lid_m16"
    assert diagnostics["openedChatMessageCount"] == 1


def test_failed_live_refresh_marks_stale_cached_history_as_degraded():
    class Collector(WhatsAppCollector):
        def _opened_chat_recent_messages_for_chat(self, *args, **kwargs):
            raise RuntimeError("Chat was not loaded")

    collector = Collector(session=object())
    old = [message(0, timestamp="2026-09-17T09:02:19+00:00")]
    collector._previous_threads = {"123@lid": {"messages": [message(0)]}}
    messages, diagnostics = collector._refresh_recent_messages_from_opened_chat(
        "Example", recent_messages=old, max_messages=15, attachments_dir=None, thread_key="123@lid",
        expected_latest_timestamp="2026-10-02T10:40:28+00:00", return_diagnostics=True,
    )
    current = export(messages)
    current["threads"][0]["sourceDiagnostics"] = diagnostics
    with pytest.raises(ExportQualityError, match="cached-history-refresh-incomplete"):
        validate_export_quality(current)


@pytest.mark.parametrize("locked", [False, True])
def test_skipped_labeled_chat_keeps_identity_and_capture_state(locked):
    class Collector(WhatsAppCollector):
        capture_calls = 0

        def collect_snapshot(self):
            return Snapshot("WhatsApp", "https://web.whatsapp.com/", [], [])

        def collect_labeled_threads(self, **kwargs):
            return [IndexedDBThread("123@lid", "Example", None, ["Clients"], None, 0, "", None, False, [])]

        def _is_locked_chat(self, jid):
            return locked

        def _opened_chat_recent_messages_for_chat(self, *args, **kwargs):
            self.capture_calls += 1
            self._opened_capture_details["123@lid"] = {"state": "still-loading", "isLoading": True}
            return []

        def _excluded_recent_chat_names(self, labels):
            return set()

        def collect_chat_list(self):
            return []

        def _recent_default_view_exports(self, **kwargs):
            return []

        def _recent_indexeddb_chat_exports(self, **kwargs):
            return []

    collector = Collector(session=object())
    payload = collector.collect_dashboard_export(allow_labels=["Clients"])
    skipped = payload["exportDiagnostics"]["skippedThreads"][0]
    assert skipped["chatTitle"] == "Example"
    assert skipped["threadKey"] == "123@lid"
    assert skipped["reason"] == ("locked-chat" if locked else "chat-content-not-ready")
    assert collector.capture_calls == (0 if locked else 2)
    if not locked:
        assert skipped["sourceDiagnostics"]["captureState"]["state"] == "still-loading"


def test_media_recovery_reopens_chat_and_replaces_missing_bytes(tmp_path: Path):
    class Collector(WhatsAppCollector):
        calls = []

        def _is_locked_chat(self, jid):
            return False

        def _opened_chat_recent_messages_for_chat(self, chat_name, **kwargs):
            self.calls.append((chat_name, kwargs["thread_key"]))
            fresh = message(0)
            fresh["attachments"] = [{"attachmentId": "a1", "status": "downloaded", "verified": True, "localPath": "/tmp/a1.jpg"}]
            return [fresh]

    collector = Collector(session=object())
    missing = message(0)
    missing["attachments"] = [{"attachmentId": "a1", "status": "notDownloaded", "skippedReason": "download-retry-exhausted"}]
    threads = export([missing])["threads"]
    results = collector._recover_missing_media(threads, max_messages=15, attachments_dir=tmp_path)
    assert collector.calls == [("Example", "123@lid")]
    assert results[0]["recovered"] == 1
    assert results[0]["remaining"] == 0
    assert threads[0]["messages"][0]["attachments"][0]["status"] == "downloaded"
    assert "skippedReason" not in threads[0]["messages"][0]["attachments"][0]


def test_export_assessment_exposes_only_acceptable_baseline(tmp_path: Path):
    import json

    output = tmp_path / "export.json"
    previous = export([message(0)])
    output.write_text(json.dumps(previous))
    assert assess_export_file(output).payload == previous
    output.write_text(json.dumps({"threads": []}))
    assert assess_export_file(output).payload is None


def test_nonempty_indexeddb_fallback_is_refreshed_in_the_collection_pipeline():
    class Collector(WhatsAppCollector):
        calls = []

        def _idb_read_all(self, store):
            if store == "chat":
                return [{"key": "123@lid", "value": {"id": "123@lid", "t": 1790937628}}]
            if store == "contact":
                return [{"key": "123@lid", "value": {"id": "123@lid", "name": "Example"}}]
            return []

        def _idb_get_many(self, store, keys):
            return self._idb_read_all(store)

        def _idb_read_recent_messages(self, keys, *, max_messages):
            return [{"key": "false_123@lid_m0", "value": {"id": "false_123@lid_m0", "from": "123@lid",
                                                         "type": "chat", "body": "Old cached message", "t": 1789635739}}]

        def _opened_chat_recent_messages_for_chat(self, chat_name, **kwargs):
            self.calls.append((chat_name, kwargs["thread_key"]))
            return [message(index) for index in range(1, 16)]

    collector = Collector(session=object())
    threads = collector._recent_indexeddb_chat_exports(
        existing_threads=[], excluded_labels=[], allow_labels=[], include_groups="standard", max_messages=15, limit=31,
    )
    assert collector.calls == [("Example", "123@lid")]
    assert len(threads[0]["messages"]) == 15
    assert threads[0]["lastMessageAt"] == "2026-10-02T10:40:28+00:00"
    assert threads[0]["sourceDiagnostics"]["openedChatMessageCount"] == 15


def test_loading_live_capture_is_not_accepted_as_verified_history_regression():
    previous = export([message(index) for index in range(15)])
    current = export([message(0)])
    current["threads"][0]["sourceDiagnostics"] = {
        "openedChatMessageCount": 1, "sourcesUsed": ["opened-chat"], "captureState": {"isLoading": True},
    }
    with pytest.raises(ExportQualityError, match="unverified-history-regression"):
        validate_export_quality(current, previous_export=previous)
