from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess

import pytest

from whatsapp_collector.collector import WhatsAppCollector
from whatsapp_collector.export_quality import ExportQualityError, validate_export_quality
from whatsapp_collector.message_expiry import confirmed_empty, expected_expired, expiry_metadata
from whatsapp_collector.models import ChatRow, IndexedDBThread, Snapshot


NOW = datetime(2026, 10, 24, 18, tzinfo=timezone.utc)


def msg(key: str, *, age: int = 2, duration: int | None = None, kept=False) -> dict:
    sent = NOW - timedelta(days=age)
    result = {"messageId": key, "timestamp": sent.isoformat(), "text": "Example", "textAvailable": True}
    if duration:
        result["expiry"] = expiry_metadata({"t": sent.timestamp(), "ephemeralDuration": duration,
                                            "isKept": kept}, source="test")
    return result


def thread(messages: list[dict], *, key="a@lid", title="Example", diagnostics=None) -> dict:
    result = {"threadKey": key, "chatTitle": title, "sourceView": "all", "messages": messages,
              "recentMessages": deepcopy(messages), "labelsRaw": ["Clients"], "labelsNormalized": ["clients"],
              "participants": [{"name": title, "phone": "123@c.us"}]}
    if diagnostics:
        result["sourceDiagnostics"] = diagnostics
    return result


def export(*threads: dict, limit=15) -> dict:
    return {"exportedAt": NOW.isoformat(), "maxRecentMessages": limit, "threads": list(threads)}


def capture(*, count=0, empty=False, loading=False, start=True, identity=True, at=None) -> dict:
    return {"openedChatChecked": True, "openedChatMessageCount": count, "sourcesUsed": ["opened-chat"],
            "captureState": {"identityVerified": identity, "isLoading": loading, "historyStartReached": start,
                             "exportableMessageCount": count, "confirmedEmpty": empty,
                             "capturedAt": at or NOW.isoformat()}}


@pytest.mark.parametrize("duration", [86400, 604800, 7776000])
def test_each_message_timer_explains_expiration(duration):
    old = msg("old", age=duration // 86400 + 1, duration=duration)
    current = export(thread([msg("still-here", age=0)]))
    report = validate_export_quality(current, previous_export=export(thread([msg("still-here", age=0), old])))
    assert report["ok"]
    assert report["metrics"]["expectedExpiredMessageCount"] == 1
    assert current["exportDiagnostics"]["historyComparison"]["expectedExpirations"]


@pytest.mark.parametrize("change", ["kept", "unknown-keep", "future", "out-of-sync", "current-chat-timer-only", "disabled"])
def test_timer_does_not_exempt_unexplained_missing_messages(change):
    old = msg("old", duration=86400)
    if change == "kept":
        old["expiry"]["kept"] = True
    elif change == "unknown-keep":
        old["expiry"]["kept"] = None
    elif change == "future":
        old["expiry"]["expiresAt"] = (NOW + timedelta(days=1)).isoformat()
    elif change == "out-of-sync":
        old["expiry"]["outOfSync"] = True
    elif change == "disabled":
        old["expiry"]["isEphemeral"] = False
    else:
        old.pop("expiry")
    previous = export(thread([old]))
    current_thread = thread([msg("other", age=4)])
    current_thread["expirySetting"] = {"durationSeconds": 86400}
    with pytest.raises(ExportQualityError, match="unverified-history-regression"):
        validate_export_quality(export(current_thread), previous_export=previous)


def test_new_chat_setting_never_rewrites_old_message_timer():
    old = msg("old", age=2, duration=7776000)
    current = thread([msg("other", age=4)])
    current["expirySetting"] = {"durationSeconds": 86400, "settingChangedAt": NOW.isoformat()}
    with pytest.raises(ExportQualityError):
        validate_export_quality(export(current), previous_export=export(thread([old])))


@pytest.mark.parametrize("seconds,expired", [(119, False), (120, True), (121, True)])
def test_expiration_boundary_has_clock_grace(seconds, expired):
    message = msg("m", duration=86400)
    message["expiry"]["expiresAt"] = (NOW - timedelta(seconds=seconds)).isoformat()
    assert expected_expired(message, NOW.isoformat()) is expired


def test_missing_export_time_never_guesses_expiration():
    assert not expected_expired(msg("m", duration=86400), None)


def test_mixed_timers_in_different_chats_are_independent():
    old = export(thread([msg("a", duration=86400)]), thread([msg("b", duration=7776000)], key="b@lid"))
    current = export(thread([msg("a2", age=0)]), thread([msg("b2", age=0)], key="b@lid"))
    with pytest.raises(ExportQualityError) as error:
        validate_export_quality(current, previous_export=old)
    assert error.value.report["metrics"]["expectedExpiredMessageCount"] == 1
    assert error.value.report["metrics"]["unexplainedMissingMessageCount"] == 1


def test_confirmed_empty_is_valid_even_when_all_threads_are_empty():
    current = export(thread([], diagnostics=capture(empty=True)))
    report = validate_export_quality(current, previous_export=export(thread([msg("old")])))
    assert report["ok"]
    assert report["metrics"]["confirmedEmptyThreadCount"] == 1
    assert any(i["code"] == "history-changed-after-live-capture" for i in report["observations"])


@pytest.mark.parametrize("field,value", [("identityVerified", False), ("isLoading", True),
                                        ("historyStartReached", False), ("confirmedEmpty", False)])
def test_blank_or_loading_chat_is_not_confirmed_empty(field, value):
    diag = capture(empty=True)
    diag["captureState"][field] = value
    assert not confirmed_empty(diag)
    with pytest.raises(ExportQualityError):
        validate_export_quality(export(thread([], diagnostics=diag)), previous_export=export(thread([msg("old")])))


def test_recent_window_shift_is_not_a_loss():
    previous = export(thread([msg("old", age=5), msg("older", age=6)]), limit=2)
    current = export(thread([msg("new", age=0), msg("newer", age=1)]), limit=2)
    report = validate_export_quality(current, previous_export=previous)
    assert report["ok"]
    assert report["metrics"]["unexplainedMissingMessageCount"] == 0


def test_same_count_missing_identity_within_window_is_detected():
    previous = export(thread([msg("new", age=0), msg("missing", age=1)]), limit=2)
    current = export(thread([msg("new", age=0), msg("older", age=2)]), limit=2)
    with pytest.raises(ExportQualityError, match="unverified-history-regression"):
        validate_export_quality(current, previous_export=previous)


def test_all_expired_messages_can_leave_an_older_nonexpiring_message():
    kept = msg("kept", age=40)
    old = [msg("newer", age=2, duration=86400), kept]
    current = export(thread([kept]))
    assert validate_export_quality(current, previous_export=export(thread(old)))["ok"]


def test_skipped_previous_chat_does_not_hide_unverified_loss():
    current = export(thread([msg("other")], key="b@lid"))
    current["exportDiagnostics"] = {"skippedThreads": [{"threadKey": "a@lid", "reason": "no-exportable-content"}]}
    with pytest.raises(ExportQualityError, match="unverified-history-regression"):
        validate_export_quality(current, previous_export=export(thread([msg("old")])))


@pytest.mark.parametrize("empty", [False, True])
def test_authoritative_live_capture_never_resurrects_cached_messages(empty):
    class Collector(WhatsAppCollector):
        def _opened_chat_recent_messages_for_chat(self, *args, **kwargs):
            self._opened_capture_details["a@lid"] = capture(count=0 if empty else 1, empty=empty)["captureState"]
            return [] if empty else [msg("live")]
    collector = Collector(session=object())
    messages, diag = collector._refresh_recent_messages_from_opened_chat(
        "Example", recent_messages=[msg("stale")], max_messages=15, attachments_dir=None,
        thread_key="a@lid", force=True, return_diagnostics=True)
    assert [m["messageId"] for m in messages] == ([] if empty else ["live"])
    assert confirmed_empty(diag) is empty


def test_collector_preserves_confirmed_empty_labeled_thread_in_export():
    class Collector(WhatsAppCollector):
        def collect_snapshot(self):
            return Snapshot("WhatsApp", "https://web.whatsapp.com/", [], [])
        def collect_labeled_threads(self, **kwargs):
            return [IndexedDBThread("a@lid", "Example", None, ["Clients"], None, 0, "stale preview", None, False)]
        def _is_locked_chat(self, jid):
            return False
        def _opened_chat_recent_messages_for_chat(self, *args, **kwargs):
            self._opened_capture_details["a@lid"] = capture(empty=True)["captureState"]
            return []
        def _excluded_recent_chat_names(self, labels):
            return set()
        def collect_chat_list(self):
            return []
        def _recent_default_view_exports(self, **kwargs):
            return []
        def _recent_indexeddb_chat_exports(self, **kwargs):
            return []
    payload = Collector(session=object()).collect_dashboard_export(allow_labels=["Clients"])
    assert payload["exportQuality"]["ok"]
    assert payload["threads"][0]["messages"] == []
    assert payload["threads"][0]["lastMessageText"] == ""
    assert payload["threads"][0]["lastMessageAt"] is None
    assert payload["exportDiagnostics"]["expiry"][0]["confirmedEmpty"] is True


def test_alias_consolidation_preserves_messages_labels_attachments_and_sources():
    first = thread([msg("one")], title="Example Name")
    second = thread([msg("two", age=0), msg("one")], title="+1234567")
    second["labelsRaw"] = ["Important"]
    second["labelsNormalized"] = ["important"]
    second["sourceView"] = "indexeddb-recent"
    first["messages"][0]["attachments"] = [{"attachmentId": "a", "status": "downloaded", "localPath": "/tmp/a"}]
    merged, findings = WhatsAppCollector._consolidate_threads([first, second], max_messages=15)
    assert len(merged) == 1
    assert merged[0]["chatTitle"] == "Example Name"
    assert merged[0]["labelsRaw"] == ["Clients", "Important"]
    assert len(merged[0]["messages"]) == 2
    assert merged[0]["messages"][1]["attachments"][0]["status"] == "downloaded"
    assert merged[0]["lastMessageAt"] == second["messages"][0]["timestamp"]
    assert merged[0]["sourceViews"] == ["all", "indexeddb-recent"]
    assert findings[0]["policy"] == "message-id-union"


def test_newest_complete_alias_does_not_restore_expired_messages():
    stale = thread([msg("stale")], diagnostics=capture(count=1, at=(NOW - timedelta(minutes=1)).isoformat()))
    fresh = thread([], diagnostics=capture(empty=True))
    for records in ([stale, fresh], [fresh, stale]):
        merged, findings = WhatsAppCollector._consolidate_threads(records, max_messages=15)
        assert merged[0]["messages"] == []
        assert confirmed_empty(merged[0]["sourceDiagnostics"])


def test_duplicate_gate_rejects_payload_and_conflicting_timestamps():
    first, second = thread([msg("same")]), thread([msg("same", age=0)])
    with pytest.raises(ExportQualityError, match="duplicate-thread-ids"):
        validate_export_quality(export(first, second))
    merged, findings = WhatsAppCollector._consolidate_threads([first, second], max_messages=15)
    payload = export(*merged)
    payload["exportDiagnostics"] = {"identityConsolidation": findings}
    with pytest.raises(ExportQualityError, match="conflicting-message-identity"):
        validate_export_quality(payload)


def test_similar_titles_do_not_merge_distinct_chat_ids():
    threads, findings = WhatsAppCollector._consolidate_threads(
        [thread([msg("a")], title="Same Name"), thread([msg("b")], title="Same Name", key="b@lid")], max_messages=15)
    assert len(threads) == 2 and not findings


def test_default_view_filters_resolved_ids_not_just_titles():
    class Collector(WhatsAppCollector):
        def _default_view_lookup_maps(self):
            contacts = {"examplename": {"id": "a@lid"}, "digits:1234567": {"id": "a@lid"}}
            return contacts, {}, {}, {}, {}
        def _resolve_default_view_chat(self, *args, **kwargs):
            return None
        def _idb_read_recent_messages(self, *args, **kwargs):
            return []
        def _refresh_recent_messages_from_opened_chat(self, name, **kwargs):
            return [msg("live")], None
    rows = [ChatRow(name, "Today", "preview", 0, False) for name in ("Example Name", "+1234567")]
    collector = Collector(session=object())
    actual = collector._recent_default_view_exports(chat_rows=rows, existing_titles=[], excluded_titles=set(),
                                                   include_groups="standard", max_messages=15)
    assert len(actual) == 1
    existing = [thread([msg("existing")])]
    assert collector._recent_default_view_exports(chat_rows=rows, existing_threads=existing, existing_titles=["Other Name"],
                                                 excluded_titles=set(), include_groups="standard", max_messages=15) == []
    collector._excluded_thread_keys.add("a@lid")
    assert collector._recent_default_view_exports(chat_rows=rows, existing_titles=[], excluded_titles=set(),
                                                 include_groups="standard", max_messages=15) == []


@pytest.mark.parametrize("loading,has_model,context,start,expected", [
    (False, True, True, True, True), (True, True, True, True, False),
    (False, False, True, True, False), (False, True, False, True, False),
    (False, True, True, False, False)])
def test_actual_browser_script_requires_positive_empty_evidence(loading, has_model, context, start, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser script fixtures")
    model = {"id": {"_serialized": "a@lid"}, "msgs": {"_models": [], "msgLoadState": {
        "isLoadingRecentMsgs": loading, "contextLoaded": context, "noEarlierMsgs": start}}}
    fixture = """
        let now = 0;
        global.setTimeout = (fn, ms) => {now += ms; fn(); return 0};
        global.performance = {now: () => now};
        Object.defineProperty(global, 'navigator', {value: {onLine: true}, configurable: true});
        const model = MODEL;
        const header = {__reactFiber$x: {memoizedProps: {chat: model}}};
        global.window = {require: () => ({})};
        global.document = {querySelector: selector => selector === '#main header' ? header : null,
                           querySelectorAll: () => []};
    """.replace("MODEL", json.dumps(model if has_model else None))
    script = WhatsAppCollector._opened_chat_recent_messages_js(max_messages=15)
    result = subprocess.run([node, "-e", fixture + script + ".then(result=>console.log(result));"],
                            check=True, capture_output=True, text=True)
    state = json.loads(result.stdout)["captureState"]
    assert state["confirmedEmpty"] is expected


def test_raw_expiry_survives_message_serialization():
    collector = WhatsAppCollector(session=object())
    raw = {"id": "m", "t": NOW.timestamp(), "type": "chat", "body": "Example", "ephemeralDuration": 604800,
           "expiredTimestamp": (NOW + timedelta(days=7)).timestamp(), "isKept": True}
    messages = collector._recent_messages_for_thread(jid="a@lid", display_name="Example", phone_or_history_id=None,
                                                    messages=[raw], preview="", max_messages=15)
    serialized = collector._serialize_recent_messages(messages)
    assert serialized[0]["expiry"]["durationSeconds"] == 604800
    assert serialized[0]["expiry"]["kept"] is True


def test_explicit_millisecond_expiry_and_kept_state_take_precedence():
    metadata = expiry_metadata({"t": NOW.timestamp(), "ephemeralDuration": 86400,
                                "expiredTimestamp": (NOW + timedelta(days=7)).timestamp() * 1000,
                                "isEphemeral": True, "isKept": True}, source="live")
    assert metadata["expiresAt"] == (NOW + timedelta(days=7)).isoformat()
    assert not expected_expired({"expiry": metadata}, (NOW + timedelta(days=9)).isoformat())


def test_unknown_keep_status_and_non_ephemeral_messages_are_not_assumed_expired():
    for raw in ({"t": NOW.timestamp(), "ephemeralDuration": 86400},
                {"t": NOW.timestamp(), "ephemeralDuration": 86400, "isKept": False, "isEphemeral": False}):
        assert not expected_expired({"expiry": expiry_metadata(raw, source="indexeddb")},
                                    (NOW + timedelta(days=3)).isoformat())


def test_confirmed_empty_after_all_messages_expire_is_informational():
    previous = export(thread([msg("old", duration=86400)]))
    current = export(thread([], diagnostics=capture(empty=True)))
    report = validate_export_quality(current, previous_export=previous)
    assert report["ok"] and report["metrics"]["expectedExpiredMessageCount"] == 1
    assert any(i["code"] == "expected-message-expiration" for i in report["observations"])
    assert not any(i["severity"] in ("error", "warning") for i in report["issues"])


def test_unverified_empty_thread_is_not_hidden_by_other_healthy_threads():
    with pytest.raises(ExportQualityError, match="unverified-empty-thread"):
        validate_export_quality(export(thread([]), thread([msg("other")], key="b@lid")))


@pytest.mark.parametrize("mode", ["default", "indexeddb"])
def test_confirmed_empty_survives_recent_collection_paths(mode):
    class Collector(WhatsAppCollector):
        def _default_view_lookup_maps(self):
            return {"example": {"id": "a@lid"}}, {}, {}, {}, {}
        def _resolve_default_view_chat(self, *args, **kwargs):
            return {"id": "a@lid"}
        def _idb_read_all(self, name):
            return [{"value": {"id": "a@lid"}}] if name == "chat" else []
        def _idb_get_many(self, *args):
            return []
        def _idb_read_recent_messages(self, *args, **kwargs):
            return []
        def _is_locked_chat(self, jid):
            return False
        def _refresh_recent_messages_from_opened_chat(self, name, **kwargs):
            return [], capture(empty=True)
    collector = Collector(session=object())
    if mode == "default":
        result = collector._recent_default_view_exports(chat_rows=[ChatRow("Example", "Today", "stale preview", 0, False)],
            existing_titles=[], excluded_titles=set(), include_groups="standard", max_messages=15)
    else:
        result = collector._recent_indexeddb_chat_exports(existing_threads=[], excluded_labels=[], allow_labels=[],
            include_groups="standard", max_messages=15, limit=15)
    assert len(result) == 1
    assert result[0]["messages"] == [] and result[0]["lastMessageAt"] is None
    assert result[0]["lastMessageText"] == ""


def test_unresolved_titles_remain_separate_and_missing_identity_is_rejected():
    records = [thread([msg("one")], key="visible:one"), thread([msg("two")], key="visible:two")]
    merged, _ = WhatsAppCollector._consolidate_threads(records, max_messages=15)
    assert len(merged) == 2
    merged[0].pop("threadKey")
    merged, _ = WhatsAppCollector._consolidate_threads(merged, max_messages=15)
    with pytest.raises(ExportQualityError, match="missing-thread-id"):
        validate_export_quality(export(*merged))


def test_newer_partial_alias_cannot_silently_lose_new_evidence():
    complete = thread([msg("one", age=2)], diagnostics=capture(count=1, at=(NOW - timedelta(minutes=1)).isoformat()))
    partial = thread([msg("new", age=0)], diagnostics=capture(count=1, start=False))
    merged, findings = WhatsAppCollector._consolidate_threads([complete, partial], max_messages=15)
    payload = export(*merged)
    payload["exportDiagnostics"] = {"identityConsolidation": findings}
    with pytest.raises(ExportQualityError, match="conflicting-message-identity"):
        validate_export_quality(payload)


def test_full_live_window_is_not_truncated_by_newer_untrusted_cache():
    live_messages = [msg(str(i), age=i + 10) for i in range(15)]
    complete = thread(live_messages, diagnostics=capture(count=15))
    stale = thread([msg("not-live", age=0)])
    merged, _ = WhatsAppCollector._consolidate_threads([complete, stale], max_messages=15)
    assert len(merged[0]["messages"]) == 15
    assert {m["messageId"] for m in merged[0]["messages"]} == {m["messageId"] for m in live_messages}


def test_alias_merge_keeps_content_flags_consistent_with_retained_text():
    readable = {**msg("same"), "contentAvailable": True}
    sparse = {"messageId": "same", "text": "", "textAvailable": False, "contentAvailable": False}
    merged = WhatsAppCollector._merge_recent_message_exports([sparse], [readable], max_messages=15)[0]
    assert merged["text"] == readable["text"]
    assert merged["textAvailable"] is True and merged["contentAvailable"] is True


def test_conflicting_alias_text_is_reported_without_leaking_message_bodies():
    first, second = thread([msg("same")]), thread([{**msg("same"), "text": "Edited text"}])
    merged, findings = WhatsAppCollector._consolidate_threads([first, second], max_messages=15)
    assert merged[0]["messages"][0]["text"] == "Edited text"
    assert findings[0]["conflicts"][0]["code"] == "matching-message-text-conflict"
    assert "Edited text" not in json.dumps(findings)


def test_nonfatal_observations_preserve_strict_downstream_issues_contract():
    current = export(thread([], diagnostics=capture(empty=True)))
    quality = validate_export_quality(current, previous_export=export(thread([msg("old", duration=86400)])))
    assert quality["ok"] is True and quality["issues"] == []
    assert {o["code"] for o in quality["observations"]} == {"expected-message-expiration", "confirmed-empty-chats"}
