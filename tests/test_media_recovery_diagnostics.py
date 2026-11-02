from copy import deepcopy

import pytest

from whatsapp_collector.collector import WhatsAppCollector
from whatsapp_collector.export_quality import ExportQualityError, validate_export_quality


def message(key="m1", *, downloaded=False):
    attachment = {"attachmentId": "a1", "kind": "image", "status": "notDownloaded",
                  "skippedReason": "download-retry-exhausted"}
    if downloaded:
        attachment = {"attachmentId": "a1", "kind": "image", "status": "downloaded",
                      "verified": True, "localPath": "/fixture/image.jpg"}
    return {"messageId": key, "text": "Existing content", "textAvailable": True,
            "timestamp": "2026-07-06T08:27:49+00:00", "attachments": [attachment]}


def payload():
    return {"maxRecentMessages": 15, "threads": [{
        "threadKey": "a@lid", "chatTitle": "Example", "sourceView": "all",
        "lastMessageText": "Existing content", "lastMessageAt": "2026-07-06T08:27:49+00:00",
        "messages": [message()], "recentMessages": [message()],
        "sourceDiagnostics": {"openedChatChecked": True, "openedChatMessageCount": 1,
                              "sourcesUsed": ["opened-chat"], "issues": [],
                              "captureAttempts": 2, "elapsedSeconds": 5.0,
                              "captureState": {"isLoading": False, "identityVerified": True,
                                               "historyStartReached": False}},
    }]}


class RecoveryCollector(WhatsAppCollector):
    def __init__(self, state, opened=None, error=None):
        super().__init__(session=object())
        self.state = state
        self.opened = [message(downloaded=True)] if opened is None else opened
        self.error = error

    def _is_locked_chat(self, jid):
        return False

    def _opened_chat_recent_messages_for_chat(self, name, *, thread_key, **kwargs):
        if self.state is not None:
            self._opened_capture_details[thread_key] = deepcopy(self.state)
        if self.error:
            raise self.error
        return deepcopy(self.opened)


READY = {"isLoading": False, "identityVerified": True, "historyStartReached": False}


def test_media_recovery_cannot_clear_stale_or_incomplete_content_findings(tmp_path):
    candidate = payload()
    diagnostic = candidate["threads"][0]["sourceDiagnostics"]
    diagnostic.update(captureIncomplete=True)
    diagnostic["issues"] = [{"code": "cached-history-still-stale", "expectedLatestAt": "2026-07-07T08:39:55+00:00"}]
    original = deepcopy(diagnostic)
    with pytest.raises(ExportQualityError):
        validate_export_quality(candidate)
    result = RecoveryCollector(READY)._recover_missing_media(candidate["threads"], max_messages=15, attachments_dir=tmp_path)
    assert result[0]["recovered"] == 1
    after = candidate["threads"][0]["sourceDiagnostics"]
    assert all(after[key] == value for key, value in original.items())
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality(candidate)
    with pytest.raises(ExportQualityError, match="cached-history-refresh-incomplete"):
        validate_export_quality(candidate)


@pytest.mark.parametrize("state", [
    {**READY, "isLoading": True}, {**READY, "waitTimedOut": True},
    {**READY, "timedOut": True}, {**READY, "timeout": True},
    {**READY, "identityVerified": False}, {**READY, "domModelsResolved": False},
    {**READY, "captureIncomplete": True},
    {**READY, "emptyEvidence": {"domModelsResolved": False}}, None,
])
def test_new_incomplete_capture_blocks_even_when_original_was_healthy(tmp_path, state):
    candidate = payload()
    assert validate_export_quality(candidate)["ok"]
    collector = RecoveryCollector(state)
    # A missing new capture state must not inherit success from an earlier open.
    collector._opened_capture_details["a@lid"] = deepcopy(READY)
    collector._recover_missing_media(candidate["threads"], max_messages=15, attachments_dir=tmp_path)
    diagnostic = candidate["threads"][0]["sourceDiagnostics"]
    assert diagnostic["captureIncomplete"]
    assert diagnostic["mediaRecoveryCapture"]["captureState"] == (state or {})
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality(candidate)


@pytest.mark.parametrize("error", [TimeoutError("capture timeout"), ValueError("invalid capture")])
def test_capture_exception_preserves_content_and_blocks_quality(tmp_path, error):
    candidate = payload()
    before = deepcopy(candidate["threads"][0]["messages"])
    result = RecoveryCollector(None, error=error)._recover_missing_media(
        candidate["threads"], max_messages=15, attachments_dir=tmp_path)
    assert result[0]["error"] == str(error)
    assert candidate["threads"][0]["messages"] == before
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality(candidate)


@pytest.mark.parametrize("confirmed", [False, True])
def test_empty_media_recovery_never_deletes_existing_messages(tmp_path, confirmed):
    candidate = payload()
    before = deepcopy(candidate["threads"][0]["messages"])
    state = {**READY, "confirmedEmpty": confirmed, "historyStartReached": confirmed, "exportableMessageCount": 0}
    RecoveryCollector(state, opened=[])._recover_missing_media(candidate["threads"], max_messages=15, attachments_dir=tmp_path)
    assert candidate["threads"][0]["messages"] == before
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality(candidate)


def test_successful_media_recovery_repairs_only_matching_attachments(tmp_path):
    candidate = payload()
    thread = candidate["threads"][0]
    thread["messages"].append({**message("m2"), "attachments": []})
    before = deepcopy(thread)
    opened = [message(downloaded=True), message("new-message", downloaded=True)]
    opened[0].update(text="Changed text", timestamp="2026-07-07T10:00:00+00:00")
    opened[0]["attachments"].append({"attachmentId": "unexpected", "status": "downloaded"})
    results = RecoveryCollector({**READY, "historyStartReached": True}, opened)._recover_missing_media(
        candidate["threads"], max_messages=15, attachments_dir=tmp_path)
    assert results[0]["recovered"] == 1
    assert results[0]["remaining"] == 0
    assert results[0]["elapsedSeconds"] >= 0
    assert [m["messageId"] for m in thread["messages"]] == ["m1", "m2"]
    assert thread["messages"][0]["text"] == before["messages"][0]["text"]
    assert thread["messages"][0]["timestamp"] == before["messages"][0]["timestamp"]
    assert thread["lastMessageAt"] == before["lastMessageAt"]
    assert thread["lastMessageText"] == before["lastMessageText"]
    assert thread["messages"][0]["attachments"] == [message(downloaded=True)["attachments"][0]]
    assert thread["recentMessages"] == thread["messages"]
    assert validate_export_quality(candidate)["ok"]


def test_plain_media_open_failure_does_not_erase_prior_findings(tmp_path):
    candidate = payload()
    candidate["threads"][0]["sourceDiagnostics"]["captureIncomplete"] = True
    RecoveryCollector(None, error=RuntimeError("open failed"))._recover_missing_media(
        candidate["threads"], max_messages=15, attachments_dir=tmp_path)
    with pytest.raises(ExportQualityError, match="live-chat-capture-incomplete"):
        validate_export_quality(candidate)
