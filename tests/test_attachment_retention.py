from __future__ import annotations

import os
from pathlib import Path

import pytest

from whatsapp_collector.attachment_retention import AttachmentStorageError
from whatsapp_collector.attachment_store import AttachmentPolicy, AttachmentStore
from whatsapp_collector.collector import WhatsAppCollector
from whatsapp_collector.export_safety import protected_export, write_atomic_json


def seed(root: Path, chat: str, name: str, size: int, age: int, message_time: int | None = None) -> Path:
    store = AttachmentStore(root, policy=AttachmentPolicy(max_file_bytes=10_000, max_thread_bytes=100_000, max_total_bytes=100_000))
    result = store.store_bytes(b"x" * size, thread_key=chat, message_id=name, attachment_id=f"att_{name}",
                               file_name=f"{name}.bin", expected_size=size, expected_filehash=None,
                               mime_type="application/octet-stream", download_method="fixture", message_timestamp=message_time)
    assert result.attachment is not None
    os.utime(result.attachment.path, (age, age))
    return result.attachment.path


def current_export(*paths: Path) -> dict:
    return {"threads": [{"threadKey": "chat", "messages": [{"messageId": "message", "attachments": [
        {"attachmentId": path.parent.name, "status": "downloaded", "localPath": str(path), "sizeBytes": path.stat().st_size}
        for path in paths
    ]}]}]}


def test_per_chat_prunes_oldest_only_in_that_chat_to_strictly_below_75_percent(tmp_path):
    root = tmp_path / "Attachments"
    old = seed(root, "a", "old", 25, 100)
    next_old = seed(root, "a", "next", 25, 200)
    keep = seed(root, "a", "keep", 50, 300)
    other = seed(root, "b", "other", 10, 1)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=100, max_total_bytes=1000))
    store.enforce_limits()
    assert not old.exists() and not next_old.exists()
    assert keep.exists() and other.exists()
    event = store.retention.events[0]
    assert event["scope"] == "per-chat" and event["afterBytes"] == 50
    assert event["targetBytes"] == 74
    assert event["deletedFiles"] == [str(old.relative_to(root)), str(next_old.relative_to(root))]


def test_app_wide_prunes_across_chats_in_oldest_first_order(tmp_path):
    root = tmp_path / "Attachments"
    oldest = seed(root, "b", "oldest", 30, 1)
    next_oldest = seed(root, "a", "next", 30, 2)
    recent_a = seed(root, "a", "recenta", 30, 3)
    recent_b = seed(root, "b", "recentb", 30, 4)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=1000, max_total_bytes=100))
    store.enforce_limits()
    assert not oldest.exists() and not next_oldest.exists()
    assert recent_a.exists() and recent_b.exists()
    assert [event["scope"] for event in store.retention.events] == ["app-wide"]
    assert store.retention.events[0]["afterBytes"] == 60


def test_message_chronology_takes_precedence_over_download_order(tmp_path):
    root = tmp_path / "Attachments"
    newer_message = seed(root, "a", "newer", 60, 100, message_time=300)
    older_message = seed(root, "a", "older", 40, 200, message_time=50)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=100, max_total_bytes=1000))
    store.enforce_limits()
    assert newer_message.exists() and not older_message.exists()


def test_both_scopes_run_independently_and_recalculate_after_chat_cleanup(tmp_path):
    root = tmp_path / "Attachments"
    seed(root, "a", "old", 30, 1)
    seed(root, "a", "next", 30, 2)
    seed(root, "a", "recent", 40, 3)
    seed(root, "b", "other", 60, 4)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=100, max_total_bytes=100))
    store.enforce_limits()
    assert [event["scope"] for event in store.retention.events] == ["per-chat", "app-wide"]
    assert store.retention.events[1]["beforeBytes"] == 130
    assert store._tree_size(root) < 75


@pytest.mark.parametrize("scope", ["per-chat", "app-wide"])
def test_protected_files_survive_and_incoming_attachment_is_not_allowed(tmp_path, scope):
    root = tmp_path / "Attachments"
    protected = seed(root, "a", "protected", 80, 1)
    old = seed(root, "a", "old", 20, 2)
    policy = AttachmentPolicy(max_thread_bytes=100 if scope == "per-chat" else 1000,
                              max_total_bytes=100 if scope == "app-wide" else 1000)
    store = AttachmentStore(root, policy=policy)
    store.protect_export(current_export(protected))
    store.enforce_limits()
    blocked = store.preflight(thread_key="a", expected_size=5)
    assert blocked and blocked.skipped_reason.endswith("storage-protected")
    assert "Not allowed to download" in blocked.note
    assert protected.read_bytes() == b"x" * 80 and not old.exists()
    collector = WhatsAppCollector(session=object())
    item = collector._attachment_from_candidate(
        {"kind": "document", "fileName": "incoming.bin", "sizeBytes": 5}, store=store,
        thread_key="a", message_id="incoming", index=0, allow_ui_download=False,
    )
    assert item.status == "notAllowedToDownload" and item.download_attempts == 0
    store.validate_export(current_export(protected))


def test_files_selected_for_in_progress_export_are_protected(tmp_path):
    root = tmp_path / "Attachments"
    oldest = seed(root, "a", "oldest", 20, 1)
    old = seed(root, "a", "old", 20, 2)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=100, max_total_bytes=1000))
    added = store.store_bytes(b"y" * 30, thread_key="a", message_id="new", attachment_id="att_new", file_name="new.bin",
                              expected_size=30, expected_filehash=None, mime_type=None, download_method="fixture")
    assert added.attachment
    assert store.preflight(thread_key="a", expected_size=40) is None
    assert not oldest.exists() and old.exists() and added.attachment.path.exists()
    assert store.retention.events[-1]["afterBytes"] == 50


def test_no_age_based_attachment_pruning_and_oversized_file_does_not_delete_old_files(tmp_path):
    root = tmp_path / "Attachments"
    old = seed(root, "a", "old", 10, 1)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_file_bytes=20, max_thread_bytes=100, max_total_bytes=1000))
    store.enforce_limits()
    assert store.preflight(thread_key="a", expected_size=21).skipped_reason == "file-size-limit"
    assert old.exists() and store.retention.events == []


def test_relative_paths_and_resume_capture_references_are_protected(tmp_path):
    root = tmp_path / "Attachments"
    old = seed(root, "a", "old", 100, 1)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=100, max_total_bytes=1000))
    store.retention.protect_messages([{"attachments": [{"relativePath": str(old.relative_to(root.parent))}]}])
    store.enforce_limits()
    assert old.exists() and store.retention.events[0]["blocked"]


def test_unexpected_incoming_size_fails_with_structured_storage_diagnostics(tmp_path):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy(max_file_bytes=8))
    with pytest.raises(AttachmentStorageError) as error:
        store.store_bytes(b"x" * 9, thread_key="a", message_id="m", attachment_id="att_m", file_name="m.bin",
                          expected_size=8, expected_filehash=None, mime_type=None, download_method="fixture")
    assert error.value.storage_diagnostics["category"] == "incoming-message-attachment"
    assert error.value.storage_diagnostics["actualBytes"] == 9
    with pytest.raises(AttachmentStorageError):
        store.validate_export({"threads": []})


def test_cleanup_io_failure_is_fatal_not_an_ordinary_protected_shortage(tmp_path, monkeypatch):
    root = tmp_path / "Attachments"
    old = seed(root, "a", "old", 100, 1)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=100))
    unlink = Path.unlink
    def fail(path, *args, **kwargs):
        if path == old:
            raise PermissionError("simulated filesystem failure")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(AttachmentStorageError, match="Could not safely remove") as error:
        store.enforce_limits()
    assert error.value.storage_diagnostics["category"] == "retained-attachment-storage"
    assert old.exists()


def test_guard_detects_missing_or_changed_retained_file_before_commit(tmp_path):
    root = tmp_path / "Attachments"
    path = seed(root, "a", "old", 10, 1)
    payload = current_export(path)
    store = AttachmentStore(root, policy=AttachmentPolicy())
    path.write_bytes(b"bad-size")
    with pytest.raises(AttachmentStorageError, match="size no longer matches"):
        store.validate_export(payload)


def test_guard_detects_unaccounted_capacity_violation(tmp_path):
    root = tmp_path / "Attachments"
    seed(root, "a", "old", 101, 1)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_thread_bytes=100))
    with pytest.raises(AttachmentStorageError, match="without an accounted-for"):
        store.validate_export({"threads": []})


def test_symlink_targets_and_unrecognized_files_are_never_deleted(tmp_path):
    root = tmp_path / "Attachments"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 100)
    (root / "linked.bin").symlink_to(outside)
    store = AttachmentStore(root, policy=AttachmentPolicy(max_total_bytes=10))
    store.enforce_limits()
    assert outside.read_bytes() == b"x" * 100
    unknown = root / "not-a-collector-file.bin"
    unknown.write_bytes(b"y" * 100)
    with pytest.raises(AttachmentStorageError, match="Unrecognized files"):
        store.enforce_limits()
    assert unknown.exists() and outside.exists()


def test_symbolic_link_attachment_root_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "Attachments"
    root.symlink_to(outside, target_is_directory=True)
    store = AttachmentStore(root, policy=AttachmentPolicy())
    with pytest.raises(AttachmentStorageError, match="symbolic link"):
        store.enforce_limits()


def test_unsafe_incoming_filename_cannot_escape_attachment_storage(tmp_path):
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    with pytest.raises(AttachmentStorageError, match="Invalid attachment filename"):
        store.store_bytes(b"x", thread_key="a", message_id="m", attachment_id="att_m", file_name="../../escape.bin",
                          expected_size=1, expected_filehash=None, mime_type=None, download_method="fixture")


def test_swallowed_storage_error_still_fails_collection_and_preserves_last_good_export(tmp_path):
    class BrokenCollector(WhatsAppCollector):
        def _collect_dashboard_export(self, **kwargs):
            try:
                self._attachment_store.retention.fail("simulated unexpected storage failure",
                                                     category="incoming-message-attachment", threadKey="chat")
            except RuntimeError:
                return {"threads": [], "exportDiagnostics": {}}
    output = tmp_path / "export.json"
    baseline = {"source": "whatsapp", "threads": [{"threadKey": "chat", "chatTitle": "Example chat",
                "messages": [{"messageId": "m", "text": "saved text", "textAvailable": True}]}]}
    write_atomic_json(baseline, output)
    original = output.read_bytes()
    with pytest.raises(AttachmentStorageError) as error:
        with protected_export(output) as current:
            BrokenCollector(session=object()).collect_dashboard_export(
                attachments_dir=tmp_path / "Attachments", previous_export=current.payload,
            )
    assert output.read_bytes() == original
    assert error.value.storage_diagnostics["category"] == "incoming-message-attachment"
    assert error.value.storage_diagnostics["chatTitle"] == "Example chat"
