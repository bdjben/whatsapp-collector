from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from whatsapp_collector.attachment_store import (
    AttachmentPolicy,
    AttachmentStore,
    DEFAULT_MAX_ATTACHMENT_FILE_BYTES,
    DEFAULT_MAX_ATTACHMENT_THREAD_BYTES,
    DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES,
)


def _hash(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def test_default_attachment_limits_match_product_policy() -> None:
    assert DEFAULT_MAX_ATTACHMENT_FILE_BYTES == 50_000_000
    assert DEFAULT_MAX_ATTACHMENT_THREAD_BYTES == 150_000_000
    assert DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES == 1_500_000_000


def test_store_accepts_exact_limits_and_reuses_verified_file(tmp_path: Path) -> None:
    data = b"12345678"
    store = AttachmentStore(
        tmp_path / "Attachments",
        policy=AttachmentPolicy(max_file_bytes=8, max_thread_bytes=8, max_total_bytes=8),
    )

    result = store.store_bytes(
        data,
        thread_key="thread",
        message_id="message",
        attachment_id="att_one",
        file_name="payload.bin",
        expected_size=8,
        expected_filehash=_hash(data),
        mime_type="application/octet-stream",
        download_method="test",
    )
    reused = store.existing(
        thread_key="thread",
        message_id="message",
        attachment_id="att_one",
        expected_size=8,
        expected_filehash=_hash(data),
        mime_type="application/octet-stream",
        file_name="better-name.bin",
    )

    assert result.attachment is not None
    assert reused is not None
    assert reused.reused is True
    assert reused.path == result.attachment.path
    assert reused.sha256 == hashlib.sha256(data).hexdigest()


def test_preflight_blocks_file_thread_and_total_boundary_plus_one(tmp_path: Path) -> None:
    root = tmp_path / "Attachments"
    store = AttachmentStore(root, policy=AttachmentPolicy(max_file_bytes=8, max_thread_bytes=10, max_total_bytes=12))

    file_limit = store.preflight(thread_key="a", expected_size=9)
    assert file_limit and file_limit.skipped_reason == "file-size-limit"

    first = store.store_bytes(
        b"12345678",
        thread_key="a",
        message_id="m1",
        attachment_id="att_1",
        file_name="one.bin",
        expected_size=8,
        expected_filehash=None,
        mime_type="application/octet-stream",
        download_method="test",
    )
    assert first.attachment is not None
    thread_limit = store.preflight(thread_key="a", expected_size=3)
    total_limit = store.preflight(thread_key="b", expected_size=5)
    assert thread_limit and thread_limit.skipped_reason == "thread-storage-limit"
    assert total_limit and total_limit.skipped_reason == "total-storage-limit"


def test_store_rejects_wrong_hash_size_and_signature(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy())
    pdf = b"%PDF-1.4\nvalid\n%%EOF"

    wrong_size = store.store_bytes(
        pdf,
        thread_key="a",
        message_id="m1",
        attachment_id="att_size",
        file_name="a.pdf",
        expected_size=len(pdf) + 1,
        expected_filehash=None,
        mime_type="application/pdf",
        download_method="test",
    )
    wrong_hash = store.store_bytes(
        pdf,
        thread_key="a",
        message_id="m2",
        attachment_id="att_hash",
        file_name="a.pdf",
        expected_size=len(pdf),
        expected_filehash=_hash(b"different"),
        mime_type="application/pdf",
        download_method="test",
    )
    wrong_signature = store.store_bytes(
        b"not a pdf",
        thread_key="a",
        message_id="m3",
        attachment_id="att_signature",
        file_name="a.pdf",
        expected_size=9,
        expected_filehash=None,
        mime_type="application/pdf",
        download_method="test",
    )

    assert wrong_size.skipped_reason == "attachment-verification-failed"
    assert wrong_hash.skipped_reason == "attachment-verification-failed"
    assert wrong_signature.skipped_reason == "attachment-verification-failed"
    assert not list((tmp_path / "Attachments").rglob("*.pdf"))


def test_disabled_download_policy_keeps_metadata_only(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "Attachments", policy=AttachmentPolicy(enabled=False))

    preflight = store.preflight(thread_key="thread", expected_size=100)
    result = store.store_bytes(
        b"attachment bytes",
        thread_key="thread",
        message_id="message",
        attachment_id="att_disabled",
        file_name="attachment.bin",
        expected_size=16,
        expected_filehash=None,
        mime_type="application/octet-stream",
        download_method="test",
    )

    assert preflight and preflight.skipped_reason == "attachment-downloads-disabled"
    assert result and result.skipped_reason == "attachment-downloads-disabled"
    assert not list((tmp_path / "Attachments").rglob("attachment.bin"))
