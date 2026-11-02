from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from whatsapp_collector.attachment_store import AttachmentPolicy, AttachmentStore
from whatsapp_collector.collector import WhatsAppCollector


DATA = b"%PDF-1.4\nverified media\n%%EOF"


def filehash(data=DATA):
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def retain(store, message_id="message", data=DATA):
    return store.store_bytes(
        data, thread_key="thread", message_id=message_id, attachment_id="att_one",
        file_name="payload.pdf", expected_size=len(data), expected_filehash=filehash(data),
        mime_type="application/pdf", download_method="test",
    ).attachment


def existing(store, **overrides):
    args = dict(thread_key="thread", message_id="message", attachment_id="att_one",
                file_name="payload.pdf", expected_size=len(DATA), expected_filehash=filehash(),
                mime_type="application/pdf")
    return store.existing(**(args | overrides))


@pytest.mark.parametrize("change", ["corruption", "replacement", "size", "deleted"])
def test_verified_hints_recheck_disk_even_with_preserved_mtime(tmp_path, change):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    attachment = retain(store)
    assert attachment
    assert store.verified_media(thread_key="thread", message_ids=["message"])
    before = attachment.path.stat()
    if change == "deleted":
        attachment.path.unlink()
    else:
        data = DATA.replace(b"verified", b"modified") if change != "size" else DATA + b"!"
        if change == "replacement":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(data)
            os.replace(replacement, attachment.path)
        else:
            attachment.path.write_bytes(data)
        os.utime(attachment.path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not store.verified_media(thread_key="thread", message_ids=["message"])
    assert existing(store) is None


@pytest.mark.parametrize("overrides", [
    {"expected_filehash": filehash(b"different")}, {"expected_size": len(DATA) + 1},
    {"expected_filehash": None}, {"expected_filehash": "invalid"},
    {"message_id": "other-message"}, {"thread_key": "other-thread"},
])
def test_reuse_requires_matching_source_hash_and_size(tmp_path, overrides):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    retain(store)
    assert existing(store, **overrides) is None


def test_verified_hints_only_hash_selected_messages(tmp_path, monkeypatch):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    retain(store)
    retain(store, "historical")
    original = AttachmentStore._validate_path
    checked = []

    def validate(path, **kwargs):
        checked.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(AttachmentStore, "_validate_path", staticmethod(validate))
    assert store.verified_media(thread_key="thread", message_ids=[]) == []
    assert checked == []
    fresh_store = AttachmentStore(store.root, policy=AttachmentPolicy())
    assert fresh_store.verified_media(thread_key="thread", message_ids=["message", "message"]) == [
        {"sourceMessageId": "message", "filehash": filehash(), "sizeBytes": len(DATA)},
    ]
    assert len(checked) == 1
    assert checked[0].parent.parent.name == store._stable_path_token("message")


def test_legacy_file_gets_hint_only_after_source_verification(tmp_path):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    attachment = retain(store)
    record = attachment.path.parent / ".verified-media.json"
    record.unlink()
    assert not store.verified_media(thread_key="thread", message_ids=["message"])
    assert existing(store)
    assert store.verified_media(thread_key="thread", message_ids=["message"])
    metadata = json.loads(record.read_text())
    metadata["sourceMessageId"] = "someone-else"
    record.write_text(json.dumps(metadata))
    assert not store.verified_media(thread_key="thread", message_ids=["message"])


def test_validation_hashes_each_payload_only_once(tmp_path, monkeypatch):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    attachment = retain(store)
    expected = filehash()
    original = hashlib.sha256
    hashed = []

    def sha(data=b""):
        if data == DATA:
            hashed.append(data)
        return original(data)

    monkeypatch.setattr(hashlib, "sha256", sha)
    assert store._validate_path(attachment.path, expected_size=len(DATA), expected_filehash=expected,
                                mime_type="application/pdf", file_name="payload.pdf")
    assert len(hashed) == 1


def test_hashless_downloads_still_work_but_never_reuse_old_bytes(tmp_path):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    for data in (DATA, DATA.replace(b"verified", b"modified")):
        result = store.store_bytes(
            data, thread_key="thread", message_id="message", attachment_id="att_one",
            file_name="payload.pdf", expected_size=len(data), expected_filehash=None,
            mime_type="application/pdf", download_method="test",
        )
        assert result.attachment
        assert not result.attachment.reused
        assert result.attachment.path.read_bytes() == data
        assert not store.verified_media(thread_key="thread", message_ids=["message"])


def test_changed_hash_download_replaces_stale_file_and_hint(tmp_path):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    retain(store)
    changed = DATA.replace(b"verified", b"modified")
    assert existing(store, expected_filehash=filehash(changed)) is None
    updated = retain(store, data=changed)
    assert updated and not updated.reused
    assert updated.path.read_bytes() == changed
    assert store.verified_media(thread_key="thread", message_ids=["message"]) == [
        {"sourceMessageId": "message", "filehash": filehash(changed), "sizeBytes": len(changed)},
    ]


@pytest.mark.parametrize("corrupt_after_hint", [False, True])
def test_capture_passes_selected_verified_hints_and_rechecks_before_reuse(tmp_path, monkeypatch, corrupt_after_hint):
    class Session:
        def click_point(self, expression):
            pass

        def run_json(self, script):
            if corrupt_after_hint:
                for path in tmp_path.rglob("payload.pdf"):
                    path.write_bytes(DATA.replace(b"verified", b"modified"))
            return {"openedChatJid": "thread", "messages": [{"id": "message", "type": "document",
                    "fileName": "payload.pdf", "mimetype": "application/pdf", "size": len(DATA),
                    "filehash": filehash()}]}

    collector = WhatsAppCollector(session=Session())
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    collector._attachment_store = store
    candidate = {"id": "message", "type": "document", "fileName": "payload.pdf",
                 "mimetype": "application/pdf", "size": len(DATA), "filehash": filehash(),
                 "dataUrl": "data:application/pdf;base64," + base64.b64encode(DATA).decode("ascii")}
    collector._recent_messages_for_thread(jid="thread", display_name="Chat", phone_or_history_id=None,
        messages=[candidate], preview="", max_messages=15, attachments_dir=store.root)
    monkeypatch.setattr(collector, "_reset_chat_list_to_top", lambda: None)
    monkeypatch.setattr(collector, "_chat_open_identity", lambda *args: (["Chat"], ["thread"]))
    monkeypatch.setattr(collector, "_opened_chat_matches_identity", lambda *args, **kwargs: True)
    calls = []
    monkeypatch.setattr(collector, "_opened_chat_recent_messages_js", lambda **kwargs: calls.append(kwargs) or "capture")
    messages = collector._opened_chat_recent_messages_for_chat("Chat", max_messages=15,
        attachments_dir=store.root, thread_key="thread")
    assert calls[0]["expected_jids"] == ["thread"]
    assert calls[0]["verified_media"] == [{"sourceMessageId": "message", "filehash": filehash(), "sizeBytes": len(DATA)}]
    attachment = messages[0]["attachments"][0]
    if corrupt_after_hint:
        assert attachment["status"] == "notDownloaded"
        assert attachment["verified"] is False
        assert "localPath" not in attachment
        return
    assert attachment["downloadMethod"] == "existing-verified-file"
    assert attachment["downloadAttempts"] == 0
    assert attachment["verified"] is True
