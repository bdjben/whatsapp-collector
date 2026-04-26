from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class LabelStat:
    name: str
    chat_count: int


@dataclass(frozen=True)
class ChatRow:
    chat_name: str
    timestamp_label: str
    preview: str
    unread_count: int
    unread_flag: bool


@dataclass(frozen=True)
class RecentMessage:
    message_id: str
    timestamp: int | None
    iso_timestamp: str | None
    direction: str
    sender: str | None
    text: str | None
    text_available: bool
    message_type: str
    subtype: str | None


@dataclass(frozen=True)
class IndexedDBThread:
    jid: str
    display_name: str
    phone_or_history_id: str | None
    labels: list[str]
    last_message_timestamp: int | None
    unread_count: int
    preview: str
    timestamp_label: str | None
    visible_in_chat_list: bool
    recent_messages: list[RecentMessage] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedEvent:
    source: str
    external_thread_id: str
    display_name: str
    labels: list[str]
    summary: str
    importance: str
    status_hint: str
    unread_count: int
    last_message_timestamp: int | None
    timestamp_label: str | None
    visible_in_chat_list: bool
    recent_message_count: int = 0
    recent_message_text_available_count: int = 0


@dataclass(frozen=True)
class Snapshot:
    page_title: str
    page_url: str
    labels: list[LabelStat]
    chat_list: list[ChatRow]

    def to_dict(
        self,
        *,
        allowed_labels: list[str] | None = None,
        excluded_labels: list[str] | None = None,
        max_recent_messages: int | None = None,
        labeled_threads: list[IndexedDBThread] | None = None,
        events: list[NormalizedEvent] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        return {
            "collected_at": now,
            "source": "whatsapp_business_web",
            "mode": "active_chrome_session_readonly",
            "page": {
                "title": self.page_title,
                "url": self.page_url,
            },
            "allowed_labels": allowed_labels or [],
            "excluded_labels": excluded_labels or [],
            "max_recent_messages": max_recent_messages,
            "labels": [asdict(label) for label in self.labels],
            "chat_list": [asdict(chat) for chat in self.chat_list],
            "labeled_threads": [asdict(thread) for thread in (labeled_threads or [])],
            "events": [asdict(event) for event in (events or [])],
        }
