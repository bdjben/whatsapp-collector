from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator


DEFAULT_MAX_ATTACHMENT_FILE_BYTES = 50_000_000
DEFAULT_MAX_ATTACHMENT_THREAD_BYTES = 150_000_000
DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES = 1_500_000_000


@dataclass(frozen=True)
class AttachmentPolicy:
    enabled: bool = True
    max_file_bytes: int = DEFAULT_MAX_ATTACHMENT_FILE_BYTES
    max_thread_bytes: int = DEFAULT_MAX_ATTACHMENT_THREAD_BYTES
    max_total_bytes: int = DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "enabled": self.enabled,
            "maxFileBytes": self.max_file_bytes,
            "maxThreadBytes": self.max_thread_bytes,
            "maxTotalBytes": self.max_total_bytes,
        }


@dataclass(frozen=True)
class StoredAttachment:
    path: Path
    relative_path: str
    size_bytes: int
    sha256: str
    mime_type: str | None
    file_name: str
    download_method: str
    reused: bool = False


@dataclass(frozen=True)
class StoreResult:
    attachment: StoredAttachment | None = None
    skipped_reason: str | None = None
    note: str | None = None


class AttachmentStore:
    """Validates and atomically retains attachment files within collection budgets."""

    def __init__(self, root: Path, *, policy: AttachmentPolicy) -> None:
        self.root = root.expanduser()
        self.policy = policy
        self.initial_bytes = self._tree_size(self.root)

    def existing(
        self,
        *,
        thread_key: str,
        message_id: str,
        attachment_id: str,
        expected_size: int | None,
        expected_filehash: str | None,
        mime_type: str | None,
        file_name: str,
    ) -> StoredAttachment | None:
        directory = self._attachment_directory(thread_key, message_id, attachment_id)
        if not directory.exists():
            return None
        with self._locked():
            return self._existing_unlocked(
                directory,
                expected_size=expected_size,
                expected_filehash=expected_filehash,
                mime_type=mime_type,
                file_name=file_name,
            )

    def preflight(self, *, thread_key: str, expected_size: int | None) -> StoreResult | None:
        if not self.policy.enabled:
            return StoreResult(
                skipped_reason="attachment-downloads-disabled",
                note="Automatic attachment downloads are turned off; metadata remains in the export.",
            )
        if expected_size is not None and expected_size > self.policy.max_file_bytes:
            return StoreResult(
                skipped_reason="file-size-limit",
                note=f"Attachment is larger than the {self.policy.max_file_bytes:,}-byte per-file limit.",
            )
        with self._locked():
            total_bytes = self._tree_size(self.root)
            thread_bytes = self._tree_size(self._thread_directory(thread_key))
        if total_bytes >= self.policy.max_total_bytes:
            return StoreResult(
                skipped_reason="total-storage-limit",
                note="The configured total attachment storage limit has been reached.",
            )
        if thread_bytes >= self.policy.max_thread_bytes:
            return StoreResult(
                skipped_reason="thread-storage-limit",
                note="The 150 MB attachment limit for this thread has been reached.",
            )
        if expected_size is not None and total_bytes + expected_size > self.policy.max_total_bytes:
            return StoreResult(
                skipped_reason="total-storage-limit",
                note="This attachment would exceed the configured total attachment storage limit.",
            )
        if expected_size is not None and thread_bytes + expected_size > self.policy.max_thread_bytes:
            return StoreResult(
                skipped_reason="thread-storage-limit",
                note="This attachment would exceed the 150 MB attachment limit for this thread.",
            )
        return None

    def store_bytes(
        self,
        data: bytes,
        *,
        thread_key: str,
        message_id: str,
        attachment_id: str,
        file_name: str,
        expected_size: int | None,
        expected_filehash: str | None,
        mime_type: str | None,
        download_method: str,
    ) -> StoreResult:
        if not self.policy.enabled:
            return StoreResult(
                skipped_reason="attachment-downloads-disabled",
                note="Automatic attachment downloads are turned off; metadata remains in the export.",
            )
        validation_error = self._validation_error(
            data,
            expected_size=expected_size,
            expected_filehash=expected_filehash,
            mime_type=mime_type,
            file_name=file_name,
        )
        if validation_error:
            return StoreResult(skipped_reason="attachment-verification-failed", note=validation_error)

        directory = self._attachment_directory(thread_key, message_id, attachment_id)
        output_path = directory / file_name
        sha256 = hashlib.sha256(data).hexdigest()
        with self._locked():
            existing = self._existing_unlocked(
                directory,
                expected_size=expected_size,
                expected_filehash=expected_filehash,
                mime_type=mime_type,
                file_name=file_name,
            )
            if existing:
                return StoreResult(attachment=existing)

            replaced_size = output_path.stat().st_size if output_path.exists() and output_path.is_file() else 0
            total_bytes = self._tree_size(self.root) - replaced_size
            thread_bytes = self._tree_size(self._thread_directory(thread_key)) - replaced_size
            if len(data) > self.policy.max_file_bytes:
                return StoreResult(
                    skipped_reason="file-size-limit",
                    note=f"Attachment is larger than the {self.policy.max_file_bytes:,}-byte per-file limit.",
                )
            if thread_bytes + len(data) > self.policy.max_thread_bytes:
                return StoreResult(
                    skipped_reason="thread-storage-limit",
                    note="This attachment would exceed the 150 MB attachment limit for this thread.",
                )
            if total_bytes + len(data) > self.policy.max_total_bytes:
                return StoreResult(
                    skipped_reason="total-storage-limit",
                    note="This attachment would exceed the configured total attachment storage limit.",
                )

            directory.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(prefix=".pending-", dir=directory, delete=False) as handle:
                    temporary_path = Path(handle.name)
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, output_path)
            finally:
                if temporary_path and temporary_path.exists():
                    temporary_path.unlink(missing_ok=True)

        return StoreResult(
            attachment=StoredAttachment(
                path=output_path,
                relative_path=str(output_path.relative_to(self.root.parent)),
                size_bytes=len(data),
                sha256=sha256,
                mime_type=mime_type or mimetypes.guess_type(file_name)[0],
                file_name=file_name,
                download_method=download_method,
            )
        )

    def _existing_unlocked(
        self,
        directory: Path,
        *,
        expected_size: int | None,
        expected_filehash: str | None,
        mime_type: str | None,
        file_name: str,
    ) -> StoredAttachment | None:
        if not directory.exists():
            return None
        for candidate in sorted(directory.iterdir()):
            if not candidate.is_file() or candidate.name.startswith("."):
                continue
            validation = self._validate_path(
                candidate,
                expected_size=expected_size,
                expected_filehash=expected_filehash,
                mime_type=mime_type,
                file_name=file_name,
            )
            if validation is None:
                continue
            actual_size, sha256 = validation
            return StoredAttachment(
                path=candidate,
                relative_path=str(candidate.relative_to(self.root.parent)),
                size_bytes=actual_size,
                sha256=sha256,
                mime_type=mime_type or mimetypes.guess_type(candidate.name)[0],
                file_name=candidate.name,
                download_method="existing-verified-file",
                reused=True,
            )
        return None

    @staticmethod
    def decode_data_url(data_url: str) -> tuple[bytes, str | None] | None:
        match = re.match(r"^data:(?P<mime>[^;,]+)?(?:;charset=[^;,]+)?;base64,(?P<data>.+)$", data_url, re.DOTALL)
        if not match:
            return None
        try:
            return base64.b64decode(match.group("data"), validate=True), match.group("mime")
        except (ValueError, TypeError):
            return None

    @classmethod
    def expected_sha256(cls, filehash: str | None) -> str | None:
        if not filehash:
            return None
        try:
            digest = base64.b64decode(filehash, validate=True)
        except (ValueError, TypeError):
            return None
        return digest.hex() if len(digest) == hashlib.sha256().digest_size else None

    def _attachment_directory(self, thread_key: str, message_id: str, attachment_id: str) -> Path:
        return self._thread_directory(thread_key) / self._stable_path_token(message_id) / attachment_id

    def _thread_directory(self, thread_key: str) -> Path:
        return self.root / self._stable_path_token(thread_key)

    @staticmethod
    def _stable_path_token(value: str) -> str:
        digest = hashlib.sha256((value or "unknown").encode("utf-8")).hexdigest()[:12]
        readable = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "").strip(".-")[:72] or "unknown"
        return f"{readable}-{digest}"

    @staticmethod
    def _tree_size(root: Path) -> int:
        if not root.exists():
            return 0
        total = 0
        for path in root.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".attachment-store.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _validate_path(
        cls,
        path: Path,
        *,
        expected_size: int | None,
        expected_filehash: str | None,
        mime_type: str | None,
        file_name: str,
    ) -> tuple[int, str] | None:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if cls._validation_error(
            data,
            expected_size=expected_size,
            expected_filehash=expected_filehash,
            mime_type=mime_type,
            file_name=file_name,
        ):
            return None
        return len(data), hashlib.sha256(data).hexdigest()

    @classmethod
    def _validation_error(
        cls,
        data: bytes,
        *,
        expected_size: int | None,
        expected_filehash: str | None,
        mime_type: str | None,
        file_name: str,
    ) -> str | None:
        if expected_size is not None and expected_size > 0 and len(data) != expected_size:
            return f"Expected {expected_size:,} bytes from WhatsApp metadata, received {len(data):,}."
        expected_sha = cls.expected_sha256(expected_filehash)
        actual_sha = hashlib.sha256(data).hexdigest()
        if expected_sha and actual_sha != expected_sha:
            return "Downloaded bytes did not match WhatsApp's SHA-256 file hash."
        if not cls._signature_matches(data, mime_type=mime_type, file_name=file_name):
            return "Downloaded bytes did not match the attachment's declared file type."
        return None

    @staticmethod
    def _signature_matches(data: bytes, *, mime_type: str | None, file_name: str) -> bool:
        mime = (mime_type or mimetypes.guess_type(file_name)[0] or "").split(";", 1)[0].strip().lower()
        suffix = Path(file_name).suffix.lower()
        if mime == "application/pdf" or suffix == ".pdf":
            return data.startswith(b"%PDF-")
        if mime in {"audio/ogg", "application/ogg"} or suffix in {".ogg", ".opus"}:
            return data.startswith(b"OggS")
        if mime in {"image/jpeg", "image/jpg"} or suffix in {".jpg", ".jpeg"}:
            return data.startswith(b"\xff\xd8\xff")
        if mime == "image/png" or suffix == ".png":
            return data.startswith(b"\x89PNG\r\n\x1a\n")
        if mime == "image/webp" or suffix == ".webp":
            return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        if mime == "image/gif" or suffix == ".gif":
            return data.startswith((b"GIF87a", b"GIF89a"))
        if mime.startswith("video/mp4") or mime in {"audio/mp4", "audio/x-m4a", "video/quicktime"} or suffix in {".mp4", ".m4a", ".mov"}:
            return len(data) >= 12 and data[4:8] == b"ftyp"
        if suffix in {".docx", ".xlsx", ".pptx", ".zip"}:
            return data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
        return bool(data)
