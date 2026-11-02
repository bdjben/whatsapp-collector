from collections import Counter
from copy import deepcopy
import json

import pytest

from whatsapp_collector.collector import WhatsAppCollector
from whatsapp_collector.export_quality import ExportQualityError, validate_export_quality
from whatsapp_collector.models import IndexedDBThread, Snapshot


def message(jid):
    return {"messageId": f"false_{jid}_one", "timestamp": "2026-10-25T10:00:00+00:00",
            "text": "PRIVATE MESSAGE BODY", "textAvailable": True, "contentAvailable": True}


class RetryCollector(WhatsAppCollector):
    def __init__(self, failure="once"):
        super().__init__(session=object())
        self.calls = Counter()
        self.discovery_calls = 0
        self.failure = failure

    def collect_snapshot(self):
        self.discovery_calls += 1
        return Snapshot("WhatsApp", "https://web.whatsapp.com/", [], [])

    def collect_labeled_threads(self, **kwargs):
        return [IndexedDBThread(jid, jid, None, ["Clients"], None, 0, "", None, False, [])
                for jid in ("good@lid", "retry@lid")]

    def _is_locked_chat(self, jid):
        return False

    def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
        self.calls[thread_key] += 1
        if thread_key == "retry@lid":
            if self.failure == "always" or (self.failure == "once" and self.calls[thread_key] == 1):
                raise RuntimeError("open failed PRIVATE ERROR")
            if self.failure == "empty":
                return []
            if self.failure == "loading":
                self._opened_capture_details[thread_key] = {"isLoading": True}
                return []
        self._opened_capture_details[thread_key] = {
            "isLoading": False, "historyStartReached": True, "identityVerified": True,
            "resolvedThreadKey": thread_key,
        }
        return [message(thread_key)]

    def _excluded_recent_chat_names(self, labels):
        return set()

    def collect_chat_list(self):
        return []

    def _recent_default_view_exports(self, **kwargs):
        # Another source reaches both identities, including an exhausted failure.
        for jid in ("good@lid", "retry@lid"):
            self._refresh_recent_messages_from_opened_chat(
                jid, thread_key=jid, recent_messages=[], max_messages=15,
                attachments_dir=None, force=True, return_diagnostics=True,
            )
        return []

    def _recent_indexeddb_chat_exports(self, **kwargs):
        return []


def test_retries_only_failed_chat_and_reuses_success_across_sources(capsys):
    collector = RetryCollector()
    payload = collector.collect_dashboard_export(allow_labels=["Clients"])
    assert collector.discovery_calls == 1
    assert collector.calls == {"good@lid": 1, "retry@lid": 2}
    assert {t["threadKey"] for t in payload["threads"]} == {"good@lid", "retry@lid"}
    assert validate_export_quality(payload)["ok"]
    captures = {c["threadKey"]: c for c in payload["exportDiagnostics"]["chatCaptures"]}
    assert captures["retry@lid"]["recovered"] is True
    assert captures["good@lid"]["reuseCount"] == 1
    assert all(c["elapsedSeconds"] >= 0 for c in captures.values())
    assert {p["phase"] for p in payload["exportDiagnostics"]["phaseTimings"]} >= {
        "discovery", "labeled-discovery", "recent-discovery", "quality-validation",
    }
    output = capsys.readouterr()
    assert output.out == ""
    assert "PRIVATE" not in output.err
    records = [json.loads(line.removeprefix("WAC_PROGRESS:")) for line in output.err.splitlines()]
    assert all(set(r) == {"phase", "completed", "total", "detail", "elapsedSeconds"} for r in records)
    assert any(r["phase"] == "chat-retry" for r in records)
    assert all(r["total"] is None for r in records if r["phase"] in {"chat-capture", "chat-retry"})
    assert records[-1]["detail"] == "candidate-ready"


@pytest.mark.parametrize("failure,reason", [("always", "chat-open-failed"), ("empty", "empty-unconfirmed"),
                                           ("loading", "chat-content-not-ready")])
def test_exhausted_failure_is_bounded_and_stays_fail_closed(failure, reason):
    collector = RetryCollector(failure)
    baseline = {"maxRecentMessages": 15, "threads": [{"threadKey": jid, "messages": [message(jid)]}
                                                    for jid in ("good@lid", "retry@lid")]}
    original = deepcopy(baseline)
    payload = collector.collect_dashboard_export(allow_labels=["Clients"], previous_export=baseline)
    assert collector.calls == {"good@lid": 1, "retry@lid": 2}
    assert baseline == original
    assert [t["threadKey"] for t in payload["threads"]] == ["good@lid"]
    assert payload["exportDiagnostics"]["skippedThreads"][0]["reason"] == reason
    with pytest.raises(ExportQualityError):
        validate_export_quality(payload, previous_export=baseline)
    with pytest.raises(ExportQualityError):
        validate_export_quality(payload)
    assert collector._run_captures is None


def test_capture_cache_does_not_survive_next_export():
    collector = RetryCollector("none")
    first = collector.collect_dashboard_export(allow_labels=["Clients"])
    first["threads"][0]["messages"][0]["text"] = "mutated"
    second = collector.collect_dashboard_export(allow_labels=["Clients"])
    assert collector.calls == {"good@lid": 2, "retry@lid": 2}
    assert all(t["messages"][0]["text"] == "PRIVATE MESSAGE BODY" for t in second["threads"])


def test_resolved_identity_reuses_capture_without_reopening():
    collector = RetryCollector("none")
    collector._run_captures = {}
    first = collector._capture_chat_with_recovery("Name", thread_key="good@lid", max_messages=15, attachments_dir=None)
    collector._run_captures[("visible-name", 15)] = collector._run_captures.pop(("good@lid", 15))
    first["messages"][0]["text"] = "mutated"
    second = collector._capture_chat_with_recovery("Name", thread_key="good@lid", max_messages=15, attachments_dir=None)
    assert collector.calls == {"good@lid": 1}
    assert second["messages"][0]["text"] == "PRIVATE MESSAGE BODY"


def test_indexeddb_phase_is_measured_without_logging_script(capsys):
    class Session:
        def run_async_json(self, script):
            return []

    collector = RetryCollector("none")
    collector.session = Session()
    collector._run_timings = []
    assert collector._run_idb_operation("PRIVATE SCRIPT", operation="PRIVATE OPERATION") == []
    assert collector._run_timings[0]["phase"] == "indexeddb"
    assert "PRIVATE" not in capsys.readouterr().err


def test_sparse_settled_capture_is_reused_without_claiming_complete_history():
    class SparseCollector(RetryCollector):
        def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
            self.calls[thread_key] += 1
            self._opened_capture_details[thread_key] = {
                "state": "content-captured", "isLoading": False, "waitTimedOut": False,
                "historyStartReached": False, "noEarlierMsgs": False,
            }
            return [message(thread_key)]

    collector = SparseCollector()
    payload = collector.collect_dashboard_export(allow_labels=["Clients"])
    assert collector.calls == {"good@lid": 1, "retry@lid": 1}
    assert all(c["usable"] and not c["complete"] for c in payload["exportDiagnostics"]["chatCaptures"])
    assert validate_export_quality(payload)["ok"]


@pytest.mark.parametrize("failure", ["isLoading", "waitTimedOut", "loading-then-error", "stale"])
def test_exhausted_nonempty_capture_blocks_even_when_baseline_counts_match(failure):
    class LoadingCollector(RetryCollector):
        def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
            self.calls[thread_key] += 1
            if thread_key == "retry@lid":
                if failure == "loading-then-error" and self.calls[thread_key] == 2:
                    raise RuntimeError("open failed")
                state = {"historyStartReached": False, "isLoading": False}
                if failure != "stale":
                    state["isLoading" if failure == "loading-then-error" else failure] = True
                self._opened_capture_details[thread_key] = state
            return [message(thread_key)]

    collector = LoadingCollector()
    baseline = {"maxRecentMessages": 15, "threads": [{"threadKey": jid, "messages": [message(jid)]}
                                                    for jid in ("good@lid", "retry@lid")]}
    # Exercise nonempty cached fallback independently of discovery/serialization.
    collector._run_captures = {}
    if failure == "stale":
        newer = {**message("retry@lid"), "timestamp": "2026-10-26T10:00:00+00:00"}
        collector._previous_threads = {"retry@lid": {"messages": [newer]}}
    messages, diagnostics = collector._refresh_recent_messages_from_opened_chat(
        "retry", thread_key="retry@lid", recent_messages=[message("retry@lid")], max_messages=15,
        attachments_dir=None, force=True, return_diagnostics=True,
        expected_latest_timestamp="2026-10-26T10:00:00+00:00" if failure == "stale" else None,
    )
    assert collector.calls["retry@lid"] == 2
    assert len(messages) == 1
    payload = deepcopy(baseline)
    payload["threads"][1].update(messages=messages, sourceDiagnostics=diagnostics)
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality(payload, previous_export=baseline)
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality(payload)


def test_cached_content_with_plain_open_error_keeps_existing_policy():
    collector = RetryCollector("always")
    messages, diagnostics = collector._refresh_recent_messages_from_opened_chat(
        "retry", thread_key="retry@lid", recent_messages=[message("retry@lid")], max_messages=15,
        attachments_dir=None, force=True, return_diagnostics=True,
    )
    assert collector.calls["retry@lid"] == 2
    payload = {"threads": [{"threadKey": "retry@lid", "messages": messages, "sourceDiagnostics": diagnostics}]}
    assert validate_export_quality(payload)["ok"]


def test_loading_then_settled_recovery_clears_incomplete_evidence():
    class RecoveringCollector(RetryCollector):
        def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
            self.calls[thread_key] += 1
            self._opened_capture_details[thread_key] = {
                "state": "content-captured", "isLoading": self.calls[thread_key] == 1,
                "historyStartReached": False,
            }
            return [message(thread_key)]

    collector = RecoveringCollector()
    payload = collector.collect_dashboard_export(allow_labels=["Clients"])
    assert collector.calls == {"good@lid": 2, "retry@lid": 2}
    assert all(c["recovered"] and not c["incomplete"] for c in payload["exportDiagnostics"]["chatCaptures"])
    assert validate_export_quality(payload)["ok"]


def test_confirmed_empty_does_not_retry_or_restore_previous_messages():
    class EmptyCollector(RetryCollector):
        def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
            self.calls[thread_key] += 1
            self._opened_capture_details[thread_key] = {
                "identityVerified": True, "confirmedEmpty": True, "historyStartReached": True,
                "isLoading": False, "exportableMessageCount": 0,
            }
            return []

    collector = EmptyCollector()
    payload = collector.collect_dashboard_export(allow_labels=["Clients"])
    assert collector.calls == {"good@lid": 1, "retry@lid": 1}
    assert all(t["messages"] == [] for t in payload["threads"])
    assert validate_export_quality(payload)["ok"]


def test_capture_scope_is_discarded_after_export_exception():
    class BrokenCollector(RetryCollector):
        def collect_chat_list(self):
            raise ValueError("discovery failed")

    collector = BrokenCollector("none")
    with pytest.raises(ValueError, match="discovery failed"):
        collector.collect_dashboard_export(allow_labels=["Clients"])
    assert collector._run_captures is None
    assert collector._run_timings is None


def test_reused_sparse_capture_is_revalidated_against_later_source_recency():
    class SparseCollector(RetryCollector):
        def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
            self.calls[thread_key] += 1
            self._opened_capture_details[thread_key] = {"isLoading": False, "historyStartReached": False}
            return [message(thread_key)]

    collector = SparseCollector()
    collector._run_captures = {}
    kwargs = dict(thread_key="good@lid", recent_messages=[], max_messages=15, attachments_dir=None,
                  force=True, return_diagnostics=True)
    collector._refresh_recent_messages_from_opened_chat("good", **kwargs)
    collector._previous_threads = {"good@lid": {"messages": [
        {**message("good@lid"), "timestamp": "2026-10-26T10:00:00+00:00"},
    ]}}
    messages, diagnostics = collector._refresh_recent_messages_from_opened_chat(
        "good", expected_latest_timestamp="2026-10-26T10:00:00+00:00", **kwargs,
    )
    assert collector.calls == {"good@lid": 1}
    with pytest.raises(ExportQualityError, match="cached-history-refresh-incomplete"):
        validate_export_quality({"threads": [{"threadKey": "good@lid", "messages": messages,
                                               "sourceDiagnostics": diagnostics}]})


@pytest.mark.parametrize("preview", ["Voice call", "Missed voice call", "System activity", ""])
def test_newer_raw_activity_does_not_retry_or_reject_settled_exportable_content(preview):
    class SparseCollector(RetryCollector):
        def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
            self.calls[thread_key] += 1
            self._opened_capture_details[thread_key] = {
                "state": "content-captured", "isLoading": False, "waitTimedOut": False,
                "historyStartReached": False, "identityVerified": True,
            }
            return [{**message(thread_key), "timestamp": "2026-07-06T08:27:49+00:00"}]

    collector = SparseCollector()
    old = {**message("good@lid"), "timestamp": "2024-04-25T15:12:11+00:00"}
    collector._previous_threads = {"good@lid": {"messages": [old]}}
    messages, diagnostics = collector._refresh_recent_messages_from_opened_chat(
        "Good", thread_key="good@lid", recent_messages=[old], max_messages=15, attachments_dir=None,
        expected_latest_timestamp="2026-07-07T08:39:55+00:00", preview=preview, return_diagnostics=True,
    )
    assert collector.calls == {"good@lid": 1}
    assert diagnostics["captureAttempts"] == 1
    assert diagnostics["captureIncomplete"] is False
    assert diagnostics["attempts"][0]["outcome"] == "captured"
    assert not any(i["code"] == "cached-history-still-stale" for i in diagnostics["issues"])
    assert validate_export_quality({"threads": [{"threadKey": "good@lid", "messages": messages,
                                                 "sourceDiagnostics": diagnostics}]})["ok"]


@pytest.mark.parametrize("source", ["cached", "previous"])
@pytest.mark.parametrize("content", ["text", "attachment"])
def test_newer_exportable_evidence_still_retries_and_rejects(source, content):
    class SparseCollector(RetryCollector):
        def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
            self.calls[thread_key] += 1
            self._opened_capture_details[thread_key] = {"isLoading": False, "historyStartReached": False}
            return [message(thread_key)]

    collector = SparseCollector()
    newer = {**message("good@lid"), "timestamp": "2026-10-26T10:00:00+00:00"}
    if content == "attachment":
        newer.update(text="", textAvailable=False, attachments=[{"attachmentId": "new", "kind": "image"}])
    if source == "previous":
        collector._previous_threads = {"good@lid": {"messages": [newer]}}
    messages, diagnostics = collector._refresh_recent_messages_from_opened_chat(
        "Good", thread_key="good@lid", recent_messages=[newer] if source == "cached" else [],
        max_messages=15, attachments_dir=None, force=True, return_diagnostics=True,
    )
    assert collector.calls == {"good@lid": 2}
    assert diagnostics["captureIncomplete"]
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality({"threads": [{"threadKey": "good@lid", "messages": messages,
                                               "sourceDiagnostics": diagnostics}]})


def test_exportable_recency_ignores_expired_messages_and_empty_system_records():
    collector = RetryCollector()
    current = message("good@lid")
    expired = {**current, "timestamp": "2026-10-26T10:00:00+00:00", "expiry": {
        "expiresAt": "2000-01-01T00:00:00+00:00", "isEphemeral": True,
        "kept": False, "basis": "explicit-expiry",
    }}
    system = {"messageId": "call", "messageType": "call_log", "timestamp": "2026-10-27T10:00:00+00:00"}
    collector._previous_threads = {"good@lid": {"messages": [expired, system]}}
    assert collector._known_exportable_timestamp("good@lid", [current, expired, system]) == collector._exported_message_epoch(current)
