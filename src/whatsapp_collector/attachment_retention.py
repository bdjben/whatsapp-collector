from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from whatsapp_collector.message_expiry import epoch


class AttachmentStorageError(RuntimeError):
    def __init__(self, detail: str, *, category: str, **context: Any) -> None:
        super().__init__(f"Attachment storage problem ({category}): {detail}")
        self.storage_diagnostics = {"category": category, "detail": detail, **context}


@dataclass(frozen=True)
class RetainedFile:
    path: Path
    relative: Path
    size: int
    modified_ns: int
    order_ns: int
    fingerprint: tuple[int, int, int, int]
    managed: bool


class AttachmentRetention:
    """Called only while the attachment store lock is held."""

    def __init__(self, root: Path, *, chat_limit: int, total_limit: int) -> None:
        self.root = root.absolute()
        self.chat_limit = chat_limit
        self.total_limit = total_limit
        self.protected: set[Path] = set()
        self.admitted: set[Path] = set()
        self.events: list[dict[str, Any]] = []
        self.blocked: set[str] = set()
        self.failure: AttachmentStorageError | None = None

    def fail(self, detail: str, *, category: str = "retained-attachment-storage", **context: Any) -> None:
        error = AttachmentStorageError(detail, category=category, attachmentsRoot=str(self.root),
                                       chatLimitBytes=self.chat_limit, totalLimitBytes=self.total_limit, **context)
        self.failure = self.failure or error
        raise error

    def protect_export(self, payload: dict[str, Any] | None) -> None:
        for thread in (payload or {}).get("threads", []):
            for key in ("messages", "recentMessages"):
                if isinstance(thread.get(key), list):
                    self.protect_messages(thread[key])

    def protect_messages(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            for attachment in message.get("attachments", []) or []:
                path = self.attachment_path(attachment)
                if path is not None:
                    self.protected.add(path)

    def attachment_path(self, attachment: dict[str, Any]) -> Path | None:
        raw = attachment.get("relativePath")
        if isinstance(raw, str) and raw:
            path = self.root.parent / raw
        else:
            raw = attachment.get("localPath")
            if not isinstance(raw, str) or not raw:
                return None
            path = Path(raw)
        path = Path(os.path.abspath(path))
        return path if path.is_relative_to(self.root) else None

    def safe_path(self, path: Path) -> bool:
        return (not self.root.is_symlink() and path.is_relative_to(self.root)
                and path.resolve() == self.root.resolve() / path.relative_to(self.root))

    def inventory(self) -> list[RetainedFile]:
        if self.root.is_symlink():
            self.fail("The attachment folder is a symbolic link; automatic cleanup cannot safely use it.")
        result = []
        try:
            for directory, dirs, names in os.walk(self.root, followlinks=False, onerror=self._walk_error):
                dirs[:] = [name for name in dirs if not name.startswith(".") and not (Path(directory) / name).is_symlink()]
                for name in names:
                    if name.startswith("."):
                        continue
                    path = Path(directory) / name
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    relative = path.relative_to(self.root)
                    managed = len(relative.parts) == 4 and relative.parts[2].startswith("att_") and info.st_nlink == 1
                    order_ns = info.st_mtime_ns
                    try:
                        metadata = json.loads((path.parent / ".verified-media.json").read_text())
                        message_time = epoch(metadata.get("messageTimestamp")) if isinstance(metadata, dict) else None
                        if (message_time and message_time <= 10_000_000_000 and metadata.get("fileName") == name
                                and metadata.get("sizeBytes") == info.st_size):
                            order_ns = int(message_time * 1_000_000_000)
                    except (OSError, ValueError, TypeError):
                        pass
                    result.append(RetainedFile(path, relative, info.st_size, info.st_mtime_ns, order_ns,
                                               (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), managed))
        except OSError as exc:
            self.fail(f"Could not inspect retained attachment storage: {exc}")
        return result

    @staticmethod
    def _walk_error(error: OSError) -> None:
        raise error

    def enforce(self, *, chat: str | None = None, incoming_bytes: int = 0,
                replacing: Path | None = None, all_chats: bool = False) -> dict[str, Any] | None:
        files = self.inventory()
        if replacing is not None:
            files = [item for item in files if item.path != replacing.absolute()]
        chats = sorted({item.relative.parts[0] for item in files}) if all_chats else ([chat] if chat else [])
        first_block = None
        for name in chats:
            subset = [item for item in files if item.relative.parts[0] == name]
            block = self._trim(subset, scope="per-chat", key=name, limit=self.chat_limit,
                               incoming_bytes=incoming_bytes if name == chat else 0)
            first_block = first_block or block
        files = [item for item in files if item.path.exists()]
        total_block = self._trim(files, scope="app-wide", key="*", limit=self.total_limit, incoming_bytes=incoming_bytes)
        return first_block or total_block

    def _trim(self, files: list[RetainedFile], *, scope: str, key: str, limit: int,
              incoming_bytes: int) -> dict[str, Any] | None:
        used = sum(item.size for item in files)
        target = min((limit * 3 - 1) // 4, max(0, limit - incoming_bytes))
        if used <= target:
            self.blocked.discard(key)
        if used < limit and used + incoming_bytes <= limit and key not in self.blocked:
            return None
        event: dict[str, Any] = {
            "scope": scope, "chatStorageKey": key if scope == "per-chat" else None,
            "limitBytes": limit, "beforeBytes": used, "targetBytes": target,
            "incomingBytes": incoming_bytes, "deletedFiles": [], "deletedBytes": 0,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.events.append(event)
        for item in sorted(files, key=lambda item: (item.order_ns, str(item.relative))):
            if used <= target:
                break
            if item.path in self.protected or not item.managed:
                continue
            try:
                if not self.safe_path(item.path):
                    self.fail("An attachment path changed during cleanup.", path=str(item.path))
                current = item.path.lstat()
                fingerprint = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                if fingerprint != item.fingerprint or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                    self.fail("An attachment changed during cleanup; no further files were deleted.", path=str(item.path))
                self._record_pruning(item, event)
                item.path.unlink()
            except OSError as exc:
                self.fail(f"Could not safely remove an old local attachment: {exc}", path=str(item.path), scope=scope)
            used -= item.size
            event["deletedFiles"].append(str(item.relative))
            event["deletedBytes"] += item.size
        event["afterBytes"] = used
        event["protectedBytes"] = sum(item.size for item in files if item.path in self.protected)
        event["blocked"] = used > target
        if event["blocked"]:
            if any(not item.managed and item.path not in self.protected for item in files):
                self.fail("Unrecognized files prevent safe automatic cleanup of the attachment folder.", scope=scope,
                          usedBytes=used, targetBytes=target)
            self.blocked.add(key)
            return event
        self.blocked.discard(key)
        return None

    def _record_pruning(self, item: RetainedFile, event: dict[str, Any]) -> None:
        marker = item.path.parent / ".pruned-by-storage.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=item.path.parent,
                                             prefix=".pending-retention-", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"fileName": item.path.name, "sizeBytes": item.size, "prunedAt": event["at"],
                           "scope": event["scope"], "limitBytes": event["limitBytes"]}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, marker)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def validate_export(self, payload: dict[str, Any]) -> None:
        if self.failure is not None:
            raise self.failure
        for thread in payload.get("threads", []):
            for message in thread.get("messages", []) or thread.get("recentMessages", []):
                for attachment in message.get("attachments", []) or []:
                    if attachment.get("status") != "downloaded":
                        continue
                    path = self.attachment_path(attachment)
                    category = "incoming-message-attachment" if path in self.admitted else "retained-attachment-storage"
                    try:
                        if path is None or not self.safe_path(path) or not path.is_file():
                            self.fail("The export claims an attachment is retained, but its local file is missing or unsafe.",
                                      category=category, threadKey=thread.get("threadKey"), attachmentId=attachment.get("attachmentId"))
                        if path.stat().st_size != attachment.get("sizeBytes"):
                            self.fail("A retained attachment's size no longer matches the attempted export.", category=category,
                                      threadKey=thread.get("threadKey"), attachmentId=attachment.get("attachmentId"),
                                      expectedBytes=attachment.get("sizeBytes"), actualBytes=path.stat().st_size)
                    except OSError as exc:
                        self.fail(str(exc), category=category, threadKey=thread.get("threadKey"))
        files = self.inventory()
        groups: dict[str, int] = {}
        for item in files:
            key = item.relative.parts[0]
            groups[key] = groups.get(key, 0) + item.size
        if sum(groups.values()) > self.total_limit and "*" not in self.blocked:
            self.fail("Retained storage exceeded the app-wide limit without an accounted-for protected-file shortage.",
                      usedBytes=sum(groups.values()))
        for key, size in groups.items():
            if size > self.chat_limit and key not in self.blocked:
                self.fail("Retained storage exceeded a per-chat limit without an accounted-for protected-file shortage.",
                          chatStorageKey=key, usedBytes=size)

    def summary(self) -> dict[str, Any]:
        return {"order": "oldest-message-first-with-file-time-fallback", "targetPercent": 75, "protectCurrentExport": True,
                "deletedFileCount": sum(len(event["deletedFiles"]) for event in self.events),
                "deletedBytes": sum(event["deletedBytes"] for event in self.events), "events": self.events}
