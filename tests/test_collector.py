from whatsapp_collector.collector import (
    CHAT_LIST_BODY_JS,
    DEFAULT_EXCLUDED_LABELS,
    LABELS_BODY_JS,
    WhatsAppCollector,
)
from whatsapp_collector.models import ChatRow, IndexedDBThread, LabelStat, NormalizedEvent, RecentMessage, Snapshot


class StubSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_json(self, js: str):
        self.calls.append(js)
        if "PAGE_META" in js:
            return {"title": "(2) WhatsApp Business", "url": "https://web.whatsapp.com/"}
        if "LABELS_BODY" in js:
            return {
                "body": "Labels\nImportant\n3 chats\nFollow Up\n31 chats\nExcluded Label\n35 chats\nBusiness\n4 chats"
            }
        if "CHAT_LIST_BODY" in js:
            return {
                "body": "All\nUnread\nFavorites\nGroups\nExample Contact\nTuesday\nNeed to push to beginning of may\nExample Lead\nMonday\nHaha i hear you\n7 unread messages\nNew Prospect\nSunday\nWould love to connect\nExcluded Contact\nSunday\nShould be excluded\n"
            }
        raise AssertionError(f"Unexpected js: {js}")

    def run_async_json(self, script: str, result_var: str = "__hermes_async_result"):
        self.calls.append(f"ASYNC:{script[:60]}")
        if 'objectStoreNames' in script and 'model-storage' in script:
            return {
                "stores": [
                    "chat",
                    "contact",
                    "group-metadata",
                    "label",
                    "label-association",
                    "message",
                ]
            }
        if "allRecords.push({ key: cursor.key, value: cursor.value })" in script and '"label"' in script:
            return [
                {"key": "1", "value": {"id": "1", "name": "Important", "colorIndex": 1}},
                {"key": "2", "value": {"id": "2", "name": "Follow Up", "colorIndex": 2}},
                {"key": "3", "value": {"id": "3", "name": "Excluded Label", "colorIndex": 3}},
            ]
        if "allRecords.push({ key: cursor.key, value: cursor.value })" in script and '"label-association"' in script:
            return [
                {
                    "key": ["1", "141394635137028@lid", "jid"],
                    "value": {"labelId": "1", "associationId": "141394635137028@lid", "type": "jid"},
                },
                {
                    "key": ["2", "141394635137028@lid", "jid"],
                    "value": {"labelId": "2", "associationId": "141394635137028@lid", "type": "jid"},
                },
                {
                    "key": ["2", "245530529685647@lid", "jid"],
                    "value": {"labelId": "2", "associationId": "245530529685647@lid", "type": "jid"},
                },
                {
                    "key": ["3", "999999@lid", "jid"],
                    "value": {"labelId": "3", "associationId": "999999@lid", "type": "jid"},
                },
            ]
        if "allRecords.push({ key: cursor.key, value: cursor.value })" in script and '"contact"' in script:
            return [
                {
                    "key": "141394635137028@lid",
                    "value": {
                        "id": "141394635137028@lid",
                        "name": "Example Contact",
                        "phoneNumber": "12017046817@c.us",
                    },
                },
                {
                    "key": "245530529685647@lid",
                    "value": {
                        "id": "245530529685647@lid",
                        "name": "Example Lead",
                        "phoneNumber": "16103895325@c.us",
                    },
                },
                {
                    "key": "999999@lid",
                    "value": {
                        "id": "999999@lid",
                        "name": "Excluded Contact",
                        "phoneNumber": "19999999999@c.us",
                    },
                },
                {
                    "key": "777777@lid",
                    "value": {
                        "id": "777777@lid",
                        "name": "New Prospect",
                        "phoneNumber": "15551230000@c.us",
                    },
                },
            ]
        if "allRecords.push({ key: cursor.key, value: cursor.value })" in script and '"group-metadata"' in script:
            return []
        if "allRecords.push({ key: cursor.key, value: cursor.value })" in script and '"chat"' in script:
            return [
                {
                    "key": "141394635137028@lid",
                    "value": {
                        "id": "141394635137028@lid",
                        "t": 1776500000,
                        "unreadCount": 0,
                        "historyChatId": "12017046817@c.us",
                    },
                },
                {
                    "key": "245530529685647@lid",
                    "value": {
                        "id": "245530529685647@lid",
                        "t": 1776400000,
                        "unreadCount": 7,
                        "historyChatId": "16103895325@c.us",
                    },
                },
                {
                    "key": "999999@lid",
                    "value": {
                        "id": "999999@lid",
                        "t": 1776300000,
                        "unreadCount": 2,
                        "historyChatId": "19999999999@c.us",
                    },
                },
                {
                    "key": "777777@lid",
                    "value": {
                        "id": "777777@lid",
                        "t": 1776450000,
                        "unreadCount": 0,
                        "historyChatId": "15551230000@c.us",
                    },
                },
            ]
        if "allRecords.push({ key: cursor.key, value: cursor.value })" in script and '"message"' in script:
            return [
                {
                    "key": "false_141394635137028@lid_msg3",
                    "value": {"id": "false_141394635137028@lid_msg3", "t": 1776500000, "type": "chat", "from": "12017046817@c.us"},
                },
                {
                    "key": "true_141394635137028@lid_msg2",
                    "value": {"id": "true_141394635137028@lid_msg2", "t": 1776499900, "type": "chat", "from": "12122037591@c.us", "body": "Follow-up sent"},
                },
                {
                    "key": "false_141394635137028@lid_msg1",
                    "value": {"id": "false_141394635137028@lid_msg1", "t": 1776499800, "type": "image", "from": "12017046817@c.us"},
                },
                {
                    "key": "false_245530529685647@lid_msg2",
                    "value": {"id": "false_245530529685647@lid_msg2", "t": 1776400000, "type": "chat", "from": "16103895325@c.us"},
                },
                {
                    "key": "true_245530529685647@lid_msg1",
                    "value": {"id": "true_245530529685647@lid_msg1", "t": 1776399900, "type": "chat", "from": "12122037591@c.us", "body": "Sounds good"},
                },
                {
                    "key": "false_999999@lid_msg1",
                    "value": {"id": "false_999999@lid_msg1", "t": 1776300000, "type": "chat", "from": "19999999999@c.us", "body": "exclude me"},
                },
                {
                    "key": "false_777777@lid_msg2",
                    "value": {"id": "false_777777@lid_msg2", "t": 1776450000, "type": "chat", "from": "15551230000@c.us", "body": "Would love to connect"},
                },
                {
                    "key": "true_777777@lid_msg1",
                    "value": {"id": "true_777777@lid_msg1", "t": 1776449900, "type": "chat", "from": "12122037591@c.us", "body": "Happy to chat"},
                },
            ]
        raise AssertionError(f"Unexpected async script: {script[:200]}")


def test_navigation_js_targets_all_and_collapsed_labels_filter() -> None:
    assert 'getElementById("labels-filter")' in LABELS_BODY_JS
    assert 'getElementById("all-filter")' in CHAT_LIST_BODY_JS
    assert '\\d{1,2}:\\d{2}(?:\\s?[AP]M)?' in CHAT_LIST_BODY_JS


def test_plan_all_view_rows_uses_initial_top_15_then_adds_newcomers_up_to_hard_cap() -> None:
    collector = WhatsAppCollector(session=StubSession())
    initial_rows = [
        ChatRow(chat_name=f"Chat {idx}", timestamp_label="Today", preview=f"Preview {idx}", unread_count=0, unread_flag=False)
        for idx in range(1, 16)
    ] + [
        ChatRow(chat_name="(You)", timestamp_label="Today", preview="note", unread_count=0, unread_flag=False),
        ChatRow(chat_name="Chat 16", timestamp_label="Today", preview="Preview 16", unread_count=0, unread_flag=False),
    ]
    refreshed_rows = [
        ChatRow(chat_name=f"Chat {idx}", timestamp_label="Today", preview=f"Preview {idx}", unread_count=0, unread_flag=False)
        for idx in range(6, 16)
    ] + [
        ChatRow(chat_name="New Chat A", timestamp_label="Today", preview="A", unread_count=0, unread_flag=False),
        ChatRow(chat_name="New Chat B", timestamp_label="Today", preview="B", unread_count=0, unread_flag=False),
        ChatRow(chat_name="New Chat C", timestamp_label="Today", preview="C", unread_count=0, unread_flag=False),
        ChatRow(chat_name="New Chat D", timestamp_label="Today", preview="D", unread_count=0, unread_flag=False),
        ChatRow(chat_name="New Chat E", timestamp_label="Today", preview="E", unread_count=0, unread_flag=False),
        ChatRow(chat_name="New Chat F", timestamp_label="Today", preview="F", unread_count=0, unread_flag=False),
    ]

    planned = collector._plan_all_view_rows(initial_rows, refreshed_rows)

    assert [row.chat_name for row in planned] == [
        "Chat 1", "Chat 2", "Chat 3", "Chat 4", "Chat 5",
        "Chat 6", "Chat 7", "Chat 8", "Chat 9", "Chat 10",
        "Chat 11", "Chat 12", "Chat 13", "Chat 14", "Chat 15",
        "New Chat A", "New Chat B", "New Chat C", "New Chat D", "New Chat E",
    ]


def test_collect_snapshot_returns_structured_models() -> None:
    collector = WhatsAppCollector(session=StubSession())

    snapshot = collector.collect_snapshot()

    assert snapshot == Snapshot(
        page_title="(2) WhatsApp Business",
        page_url="https://web.whatsapp.com/",
        labels=[
            LabelStat(name="Important", chat_count=3),
            LabelStat(name="Follow Up", chat_count=31),
            LabelStat(name="Excluded Label", chat_count=35),
            LabelStat(name="Business", chat_count=4),
        ],
        chat_list=[
            ChatRow(
                chat_name="Example Contact",
                timestamp_label="Tuesday",
                preview="Need to push to beginning of may",
                unread_count=0,
                unread_flag=False,
            ),
            ChatRow(
                chat_name="Example Lead",
                timestamp_label="Monday",
                preview="Haha i hear you",
                unread_count=7,
                unread_flag=True,
            ),
            ChatRow(
                chat_name="New Prospect",
                timestamp_label="Sunday",
                preview="Would love to connect",
                unread_count=0,
                unread_flag=False,
            ),
            ChatRow(
                chat_name="Excluded Contact",
                timestamp_label="Sunday",
                preview="Should be excluded",
                unread_count=0,
                unread_flag=False,
            ),
        ],
    )


def test_collect_chat_list_prefers_structured_dom_rows_for_all_view_order() -> None:
    class StructuredChatListSession(StubSession):
        def run_json(self, js: str):
            if "CHAT_LIST_BODY" in js:
                return {
                    "rows": [
                        {
                            "chat_name": "Example Contact",
                            "timestamp_label": "Tuesday",
                            "preview": "I want to work with you - timing - need to push to beginning of may - I have travel next week will that work?",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                        {
                            "chat_name": "Example Lead",
                            "timestamp_label": "4/13/2026",
                            "preview": "Haha i hear you",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                        {
                            "chat_name": "Izaac Fouladi",
                            "timestamp_label": "Thursday",
                            "preview": "Let’s catch up tomorrow",
                            "unread_count": 2,
                            "unread_flag": True,
                        },
                        {
                            "chat_name": "CotC Community!",
                            "timestamp_label": "8:09 PM",
                            "preview": "Rabbi Eli : Stepping Up To The Plate There is no greater act than putting your own life on the line to protect human life.",
                            "unread_count": 2,
                            "unread_flag": True,
                        },
                        {
                            "chat_name": "Gilad Bentov",
                            "timestamp_label": "7:09 PM",
                            "preview": "I have to play it by ear I might not have the capacity to attend the commemoration events after all",
                            "unread_count": 1,
                            "unread_flag": True,
                        },
                        {
                            "chat_name": "Ariella Charny",
                            "timestamp_label": "5:50 PM",
                            "preview": "Great!",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                        {
                            "chat_name": "Ari Goldman",
                            "timestamp_label": "5:37 PM",
                            "preview": "Hi Ari! How are you?",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                        {
                            "chat_name": "+972 58-699-1569",
                            "timestamp_label": "4:45 PM",
                            "preview": "An expensive strawberry cheesecake has been delivered to your front door. Please retrieve it and refrigerate it before it spoils!",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                    ],
                    "body": "stale flattened text omitted",
                }
            return super().run_json(js)

    collector = WhatsAppCollector(session=StructuredChatListSession())

    rows = collector.collect_chat_list()

    assert [row.chat_name for row in rows[:8]] == [
        "Example Contact",
        "Example Lead",
        "Izaac Fouladi",
        "CotC Community!",
        "Gilad Bentov",
        "Ariella Charny",
        "Ari Goldman",
        "+972 58-699-1569",
    ]
    assert rows[7].preview.startswith("An expensive strawberry cheesecake")
    assert rows[3].unread_count == 2
    assert rows[4].unread_count == 1


def test_collect_labeled_threads_joins_indexeddb_with_visible_chat_list_and_excludes_excluded_label() -> None:
    collector = WhatsAppCollector(session=StubSession())

    threads = collector.collect_labeled_threads()

    assert threads == [
        IndexedDBThread(
            jid="141394635137028@lid",
            display_name="Example Contact",
            phone_or_history_id="12017046817@c.us",
            labels=["Follow Up", "Important"],
            last_message_timestamp=1776500000,
            unread_count=0,
            preview="Need to push to beginning of may",
            timestamp_label="Tuesday",
            visible_in_chat_list=True,
            recent_messages=[
                RecentMessage(
                    message_id="true_141394635137028@lid_msg2",
                    timestamp=1776499900,
                    iso_timestamp="2026-04-18T08:11:40+00:00",
                    direction="outbound",
                    sender="Me",
                    text="Follow-up sent",
                    text_available=True,
                    message_type="chat",
                    subtype=None,
                ),
            ],
        ),
        IndexedDBThread(
            jid="245530529685647@lid",
            display_name="Example Lead",
            phone_or_history_id="16103895325@c.us",
            labels=["Follow Up"],
            last_message_timestamp=1776400000,
            unread_count=7,
            preview="Haha i hear you",
            timestamp_label="Monday",
            visible_in_chat_list=True,
            recent_messages=[
                RecentMessage(
                    message_id="true_245530529685647@lid_msg1",
                    timestamp=1776399900,
                    iso_timestamp="2026-04-17T04:25:00+00:00",
                    direction="outbound",
                    sender="Me",
                    text="Sounds good",
                    text_available=True,
                    message_type="chat",
                    subtype=None,
                ),
            ],
        ),
    ]


def test_collect_labeled_threads_enforces_allowlist_and_lookback_cap() -> None:
    collector = WhatsAppCollector(session=StubSession())

    threads = collector.collect_labeled_threads(allow_labels=["Important"], max_messages=50)

    assert [thread.jid for thread in threads] == ["141394635137028@lid"]
    assert len(threads[0].recent_messages) == 1


def test_collect_labeled_threads_includes_all_labeled_chats_except_archive_label_only_or_excluded_label_only() -> None:
    class MixedLabelSession:
        def run_json(self, js: str):
            if "PAGE_META" in js:
                return {"title": "WhatsApp Business", "url": "https://web.whatsapp.com/"}
            if "LABELS_BODY" in js:
                return {"body": "Labels"}
            if "CHAT_LIST_BODY" in js:
                return {"rows": [], "body": "All"}
            raise AssertionError(f"Unexpected js: {js}")

        def run_async_json(self, script: str, result_var: str = "__hermes_async_result"):
            if 'objectStoreNames' in script and 'model-storage' in script:
                return {"stores": ["chat", "contact", "group-metadata", "label", "label-association", "message"]}
            if '"label"' in script:
                return [
                    {"key": "1", "value": {"id": "1", "name": "Archive Label"}},
                    {"key": "2", "value": {"id": "2", "name": "Excluded Label"}},
                    {"key": "3", "value": {"id": "3", "name": "Business"}},
                    {"key": "4", "value": {"id": "4", "name": "Important"}},
                ]
            if '"label-association"' in script:
                return [
                    {"key": ["1", "archive-label-only@lid", "jid"], "value": {"labelId": "1", "associationId": "archive-label-only@lid", "type": "jid"}},
                    {"key": ["2", "excluded-label-only@lid", "jid"], "value": {"labelId": "2", "associationId": "excluded-label-only@lid", "type": "jid"}},
                    {"key": ["1", "archive-label-business@lid", "jid"], "value": {"labelId": "1", "associationId": "archive-label-business@lid", "type": "jid"}},
                    {"key": ["3", "archive-label-business@lid", "jid"], "value": {"labelId": "3", "associationId": "archive-label-business@lid", "type": "jid"}},
                    {"key": ["2", "excluded-label-business@lid", "jid"], "value": {"labelId": "2", "associationId": "excluded-label-business@lid", "type": "jid"}},
                    {"key": ["3", "excluded-label-business@lid", "jid"], "value": {"labelId": "3", "associationId": "excluded-label-business@lid", "type": "jid"}},
                    {"key": ["4", "important-only@lid", "jid"], "value": {"labelId": "4", "associationId": "important-only@lid", "type": "jid"}},
                ]
            if '"contact"' in script:
                return [
                    {"key": "archive-label-only@lid", "value": {"id": "archive-label-only@lid", "name": "Archive Label Only", "phoneNumber": "1@c.us"}},
                    {"key": "excluded-label-only@lid", "value": {"id": "excluded-label-only@lid", "name": "Excluded Label Only", "phoneNumber": "2@c.us"}},
                    {"key": "archive-label-business@lid", "value": {"id": "archive-label-business@lid", "name": "Archive Label Business", "phoneNumber": "3@c.us"}},
                    {"key": "excluded-label-business@lid", "value": {"id": "excluded-label-business@lid", "name": "Excluded Label Business", "phoneNumber": "4@c.us"}},
                    {"key": "important-only@lid", "value": {"id": "important-only@lid", "name": "Important Only", "phoneNumber": "5@c.us"}},
                ]
            if '"group-metadata"' in script:
                return []
            if '"chat"' in script:
                return [
                    {"key": "archive-label-only@lid", "value": {"id": "archive-label-only@lid", "t": 100, "unreadCount": 0}},
                    {"key": "excluded-label-only@lid", "value": {"id": "excluded-label-only@lid", "t": 200, "unreadCount": 0}},
                    {"key": "archive-label-business@lid", "value": {"id": "archive-label-business@lid", "t": 300, "unreadCount": 0}},
                    {"key": "excluded-label-business@lid", "value": {"id": "excluded-label-business@lid", "t": 400, "unreadCount": 0}},
                    {"key": "important-only@lid", "value": {"id": "important-only@lid", "t": 500, "unreadCount": 0}},
                ]
            if '"message"' in script:
                return [
                    {"key": "false_archive-label-business@lid_m1", "value": {"id": "false_archive-label-business@lid_m1", "t": 300, "type": "chat", "from": "3@c.us", "body": "hb"}},
                    {"key": "false_excluded_label-business@lid_m1", "value": {"id": "false_excluded_label-business@lid_m1", "t": 400, "type": "chat", "from": "4@c.us", "body": "cb"}},
                    {"key": "false_important-only@lid_m1", "value": {"id": "false_important-only@lid_m1", "t": 500, "type": "chat", "from": "5@c.us", "body": "io"}},
                ]
            raise AssertionError(f"Unexpected async script: {script[:200]}")

    collector = WhatsAppCollector(session=MixedLabelSession())

    threads = collector.collect_labeled_threads(allow_labels=[])

    assert [thread.jid for thread in threads] == [
        "important-only@lid",
        "excluded-label-business@lid",
        "archive-label-business@lid",
    ]


def test_excluded_recent_chat_names_only_excludes_archive_label_only_or_excluded_label_only() -> None:
    class MixedRecentLabelSession:
        def run_json(self, js: str):
            raise AssertionError(f"Unexpected js: {js}")

        def run_async_json(self, script: str, result_var: str = "__hermes_async_result"):
            if '"label"' in script:
                return [
                    {"key": "1", "value": {"id": "1", "name": "Archive Label"}},
                    {"key": "2", "value": {"id": "2", "name": "Excluded Label"}},
                    {"key": "3", "value": {"id": "3", "name": "Business"}},
                ]
            if '"label-association"' in script:
                return [
                    {"key": ["1", "archive-label-only@lid", "jid"], "value": {"labelId": "1", "associationId": "archive-label-only@lid", "type": "jid"}},
                    {"key": ["2", "excluded-label-only@lid", "jid"], "value": {"labelId": "2", "associationId": "excluded-label-only@lid", "type": "jid"}},
                    {"key": ["1", "archive-label-business@lid", "jid"], "value": {"labelId": "1", "associationId": "archive-label-business@lid", "type": "jid"}},
                    {"key": ["3", "archive-label-business@lid", "jid"], "value": {"labelId": "3", "associationId": "archive-label-business@lid", "type": "jid"}},
                    {"key": ["2", "excluded-label-business@lid", "jid"], "value": {"labelId": "2", "associationId": "excluded-label-business@lid", "type": "jid"}},
                    {"key": ["3", "excluded-label-business@lid", "jid"], "value": {"labelId": "3", "associationId": "excluded-label-business@lid", "type": "jid"}},
                ]
            if '"contact"' in script:
                return [
                    {"key": "archive-label-only@lid", "value": {"id": "archive-label-only@lid", "name": "Archive Label Only"}},
                    {"key": "excluded-label-only@lid", "value": {"id": "excluded-label-only@lid", "name": "Excluded Label Only"}},
                    {"key": "archive-label-business@lid", "value": {"id": "archive-label-business@lid", "name": "Archive Label Business"}},
                    {"key": "excluded-label-business@lid", "value": {"id": "excluded-label-business@lid", "name": "Excluded Label Business"}},
                ]
            if '"group-metadata"' in script:
                return []
            raise AssertionError(f"Unexpected async script: {script[:200]}")

    collector = WhatsAppCollector(session=MixedRecentLabelSession())

    excluded_names = collector._excluded_recent_chat_names(["Archive Label", "Excluded Label"])

    assert excluded_names == {
        collector._normalized_chat_identity("Archive Label Only"),
        collector._normalized_chat_identity("Excluded Label Only"),
    }


def test_collect_events_projects_status_importance_and_recent_message_counts() -> None:
    collector = WhatsAppCollector(session=StubSession())

    events = collector.collect_events(allow_labels=["Important", "Follow Up"])

    assert events == [
        NormalizedEvent(
            source="whatsapp_business",
            external_thread_id="141394635137028@lid",
            display_name="Example Contact",
            labels=["Follow Up", "Important"],
            summary="Need to push to beginning of may",
            importance="high",
            status_hint="follow_up",
            unread_count=0,
            last_message_timestamp=1776500000,
            timestamp_label="Tuesday",
            visible_in_chat_list=True,
            recent_message_count=1,
            recent_message_text_available_count=1,
        ),
        NormalizedEvent(
            source="whatsapp_business",
            external_thread_id="245530529685647@lid",
            display_name="Example Lead",
            labels=["Follow Up"],
            summary="Haha i hear you",
            importance="medium",
            status_hint="follow_up",
            unread_count=7,
            last_message_timestamp=1776400000,
            timestamp_label="Monday",
            visible_in_chat_list=True,
            recent_message_count=1,
            recent_message_text_available_count=1,
        ),
    ]


def test_collect_dashboard_export_emits_labeled_plus_recent_default_view_threads_with_dedupe() -> None:
    collector = WhatsAppCollector(session=StubSession())

    payload = collector.collect_dashboard_export(account_label="WhatsApp", allow_labels=["Important", "Follow Up"])

    assert payload["source"] == "whatsapp"
    assert payload["account"] == {
        "platform": "whatsapp-web",
        "accountLabel": "WhatsApp",
    }
    assert payload["excludeLabels"] == DEFAULT_EXCLUDED_LABELS
    assert payload["maxRecentMessages"] == 15
    assert payload["threads"] == [
        {
            "threadKey": "141394635137028@lid",
            "chatTitle": "Example Contact",
            "chatType": "direct",
            "participants": [{"name": "Example Contact", "phone": "12017046817@c.us"}],
            "labelsRaw": ["Follow Up", "Important"],
            "labelsNormalized": ["follow-up", "important"],
            "unread": False,
            "starred": False,
            "requiresResponse": True,
            "lastMessageAt": "2026-04-18T08:11:40+00:00",
            "lastMessageDirection": "outbound",
            "lastMessageSender": "Me",
            "lastMessageText": "Follow-up sent",
            "recentMessages": [
                {
                    "messageId": "true_141394635137028@lid_msg2",
                    "timestamp": "2026-04-18T08:11:40+00:00",
                    "direction": "outbound",
                    "sender": "Me",
                    "text": "Follow-up sent",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                },
            ],
            "messages": [
                {
                    "messageId": "true_141394635137028@lid_msg2",
                    "timestamp": "2026-04-18T08:11:40+00:00",
                    "direction": "outbound",
                    "sender": "Me",
                    "text": "Follow-up sent",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                },
            ],
        },
        {
            "threadKey": "245530529685647@lid",
            "chatTitle": "Example Lead",
            "chatType": "direct",
            "participants": [{"name": "Example Lead", "phone": "16103895325@c.us"}],
            "labelsRaw": ["Follow Up"],
            "labelsNormalized": ["follow-up"],
            "unread": True,
            "starred": False,
            "requiresResponse": True,
            "lastMessageAt": "2026-04-17T04:25:00+00:00",
            "lastMessageDirection": "outbound",
            "lastMessageSender": "Me",
            "lastMessageText": "Sounds good",
            "recentMessages": [
                {
                    "messageId": "true_245530529685647@lid_msg1",
                    "timestamp": "2026-04-17T04:25:00+00:00",
                    "direction": "outbound",
                    "sender": "Me",
                    "text": "Sounds good",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                },
            ],
            "messages": [
                {
                    "messageId": "true_245530529685647@lid_msg1",
                    "timestamp": "2026-04-17T04:25:00+00:00",
                    "direction": "outbound",
                    "sender": "Me",
                    "text": "Sounds good",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                },
            ],
        },
        {
            "threadKey": "777777@lid",
            "chatTitle": "New Prospect",
            "chatType": "direct",
            "participants": [{"name": "New Prospect", "phone": "15551230000@c.us"}],
            "labelsRaw": ["Unlabeled"],
            "labelsNormalized": ["unlabeled"],
            "unread": False,
            "starred": False,
            "requiresResponse": False,
            "lastMessageAt": "2026-04-17T18:20:00+00:00",
            "lastMessageDirection": "inbound",
            "lastMessageSender": "15551230000@c.us",
            "lastMessageText": "Would love to connect",
            "timestampLabel": "Sunday",
            "sourceView": "all",
            "recentMessages": [
                {
                    "messageId": "false_777777@lid_msg2",
                    "timestamp": "2026-04-17T18:20:00+00:00",
                    "direction": "inbound",
                    "sender": "15551230000@c.us",
                    "text": "Would love to connect",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                },
                {
                    "messageId": "true_777777@lid_msg1",
                    "timestamp": "2026-04-17T18:18:20+00:00",
                    "direction": "outbound",
                    "sender": "Me",
                    "text": "Happy to chat",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                }
            ],
            "messages": [
                {
                    "messageId": "false_777777@lid_msg2",
                    "timestamp": "2026-04-17T18:20:00+00:00",
                    "direction": "inbound",
                    "sender": "15551230000@c.us",
                    "text": "Would love to connect",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                },
                {
                    "messageId": "true_777777@lid_msg1",
                    "timestamp": "2026-04-17T18:18:20+00:00",
                    "direction": "outbound",
                    "sender": "Me",
                    "text": "Happy to chat",
                    "textAvailable": True,
                    "messageType": "chat",
                    "subtype": None,
                }
            ],
        },
    ]


def test_collect_dashboard_export_opens_chat_and_uses_true_message_ids_when_indexeddb_has_no_text_for_unlabeled_row() -> None:
    class OpenedChatFallbackSession:
        def __init__(self) -> None:
            self.clicked = []

        def click_point(self, expression: str):
            self.clicked.append(expression)
            return {"x": 100, "y": 200}

        def run_json(self, js: str):
            if "PAGE_META" in js:
                return {"title": "WhatsApp Business", "url": "https://web.whatsapp.com/"}
            if "LABELS_BODY" in js:
                return {"body": "Labels"}
            if "CHAT_LIST_BODY" in js:
                return {
                    "rows": [
                        {
                            "chat_name": "+972 58-699-1569",
                            "timestamp_label": "Today",
                            "preview": "An expensive strawberry cheesecake has been delivered to your front door.",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                    ],
                    "body": "All",
                }
            if "OPENED_CHAT_RECENT_MESSAGES" in js:
                return {
                    "openedChatTitle": "+972 58-699-1569",
                    "messages": [
                        {
                            "id": "false_49294027550955@lid_3EB0REALMSG",
                            "t": 1776693900,
                            "type": "chat",
                            "subtype": None,
                            "from": "972586991569@c.us",
                            "body": "An expensive strawberry cheesecake has been delivered to your front door.",
                        }
                    ],
                }
            raise AssertionError(f"Unexpected js: {js}")

        def run_async_json(self, script: str, result_var: str = "__hermes_async_result"):
            if 'objectStoreNames' in script and 'model-storage' in script:
                return {
                    "stores": [
                        "chat",
                        "contact",
                        "group-metadata",
                        "label",
                        "label-association",
                        "message",
                    ]
                }
            if '"label"' in script or '"label-association"' in script or '"group-metadata"' in script:
                return []
            if '"contact"' in script:
                return [
                    {
                        "key": "972586991569@s.whatsapp.net",
                        "value": {
                            "id": "972586991569@s.whatsapp.net",
                            "phoneNumber": "972586991569@c.us",
                        },
                    }
                ]
            if '"chat"' in script:
                return [
                    {
                        "key": "49294027550955@lid",
                        "value": {
                            "id": "49294027550955@lid",
                            "historyChatId": "972586991569@c.us",
                            "t": 1776693263,
                            "unreadCount": 0,
                        },
                    }
                ]
            if '"message"' in script:
                return [
                    {
                        "key": "false_49294027550955@lid_3EB014F2B7F4065B433F",
                        "value": {"id": "false_49294027550955@lid_3EB014F2B7F4065B433F", "t": 1776693263, "type": "e2e_notification", "subtype": "encrypt", "from": "49294027550955@lid"},
                    }
                ]
            raise AssertionError(f"Unexpected async script: {script[:200]}")

    session = OpenedChatFallbackSession()
    collector = WhatsAppCollector(session=session)

    payload = collector.collect_dashboard_export(account_label="WhatsApp", allow_labels=[])

    unlabeled = [thread for thread in payload["threads"] if thread["labelsRaw"] == ["Unlabeled"]]
    assert len(unlabeled) == 1
    assert unlabeled[0]["chatTitle"] == "+972 58-699-1569"
    assert unlabeled[0]["threadKey"] == "49294027550955@lid"
    assert unlabeled[0]["recentMessages"][0]["messageId"] == "false_49294027550955@lid_3EB0REALMSG"
    assert unlabeled[0]["recentMessages"][0]["messageType"] == "chat"
    assert unlabeled[0]["recentMessages"][0]["text"].startswith("An expensive strawberry cheesecake")
    assert session.clicked


def test_collect_dashboard_export_resolves_unlabeled_threads_via_chat_aliases_and_phone_digits_without_preview_fallback() -> None:
    class AliasResolvingSession:
        def run_json(self, js: str):
            if "PAGE_META" in js:
                return {"title": "WhatsApp Business", "url": "https://web.whatsapp.com/"}
            if "LABELS_BODY" in js:
                return {"body": "Labels"}
            if "CHAT_LIST_BODY" in js:
                return {
                    "rows": [
                        {
                            "chat_name": "Example Lead",
                            "timestamp_label": "Today",
                            "preview": "Haha i hear you",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                        {
                            "chat_name": "+972 58-699-1569",
                            "timestamp_label": "Today",
                            "preview": "An expensive strawberry cheesecake has been delivered to your front door.",
                            "unread_count": 0,
                            "unread_flag": False,
                        },
                    ],
                    "body": "All",
                }
            raise AssertionError(f"Unexpected js: {js}")

        def run_async_json(self, script: str, result_var: str = "__hermes_async_result"):
            if 'objectStoreNames' in script and 'model-storage' in script:
                return {
                    "stores": [
                        "chat",
                        "contact",
                        "group-metadata",
                        "label",
                        "label-association",
                        "message",
                    ]
                }
            if '"label"' in script:
                return []
            if '"label-association"' in script:
                return []
            if '"contact"' in script:
                return [
                    {
                        "key": "16103895325@s.whatsapp.net",
                        "value": {
                            "id": "16103895325@s.whatsapp.net",
                            "name": "Example Lead",
                            "phoneNumber": "16103895325@c.us",
                        },
                    },
                    {
                        "key": "972586991569@s.whatsapp.net",
                        "value": {
                            "id": "972586991569@s.whatsapp.net",
                            "phoneNumber": "972586991569@c.us",
                        },
                    },
                ]
            if '"group-metadata"' in script:
                return []
            if '"chat"' in script:
                return [
                    {
                        "key": "245530529685647@lid",
                        "value": {
                            "id": "245530529685647@lid",
                            "historyChatId": "16103895325@c.us",
                            "t": 1776400000,
                            "unreadCount": 0,
                        },
                    },
                    {
                        "key": "555123@lid",
                        "value": {
                            "id": "555123@lid",
                            "historyChatId": "972586991569@c.us",
                            "t": 1776500000,
                            "unreadCount": 0,
                        },
                    },
                ]
            if '"message"' in script:
                return [
                    {
                        "key": "false_245530529685647@lid_msg2",
                        "value": {"id": "false_245530529685647@lid_msg2", "t": 1776400000, "type": "chat", "from": "16103895325@c.us", "body": "Haha i hear you"},
                    },
                    {
                        "key": "true_245530529685647@lid_msg1",
                        "value": {"id": "true_245530529685647@lid_msg1", "t": 1776399900, "type": "chat", "from": "12122037591@c.us", "body": "Sounds good"},
                    },
                    {
                        "key": "false_555123@lid_msg2",
                        "value": {"id": "false_555123@lid_msg2", "t": 1776500000, "type": "chat", "from": "972586991569@c.us", "body": "An expensive strawberry cheesecake has been delivered to your front door."},
                    },
                    {
                        "key": "true_555123@lid_msg1",
                        "value": {"id": "true_555123@lid_msg1", "t": 1776499900, "type": "chat", "from": "12122037591@c.us", "body": "Thanks!"},
                    },
                ]
            raise AssertionError(f"Unexpected async script: {script[:200]}")

    collector = WhatsAppCollector(session=AliasResolvingSession())

    payload = collector.collect_dashboard_export(account_label="WhatsApp", allow_labels=[])

    unlabeled = [thread for thread in payload["threads"] if thread["labelsRaw"] == ["Unlabeled"]]
    assert [thread["threadKey"] for thread in unlabeled] == ["245530529685647@lid", "555123@lid"]
    assert [thread["chatTitle"] for thread in unlabeled] == ["Example Lead", "+972 58-699-1569"]
    assert [len(thread["recentMessages"]) for thread in unlabeled] == [2, 2]
    assert all(thread["recentMessages"][0]["messageType"] == "chat" for thread in unlabeled)
    assert all(thread["recentMessages"][0]["messageType"] != "visible-preview" for thread in unlabeled)
    assert unlabeled[1]["lastMessageText"].startswith("An expensive strawberry cheesecake")


def test_recent_messages_for_thread_excludes_null_text_messages() -> None:
    collector = WhatsAppCollector(session=StubSession())

    recent_messages = collector._recent_messages_for_thread(
        jid="49294027550955@lid",
        display_name="+972 58-699-1569",
        phone_or_history_id="972586991569@c.us",
        messages=[
            {"id": "msg1", "t": 1776500000, "type": "e2e_notification", "subtype": "encrypt", "from": "972586991569@c.us", "body": "An expensive strawberry cheesecake has been delivered to your front door."},
            {"id": "msg2", "t": 1776499999, "type": "notification_template", "subtype": "disappearing_mode_update", "from": "972586991569@c.us"},
            {"id": "msg3", "t": 1776499998, "type": "notification_template", "subtype": "contact_info_card", "from": "972586991569@c.us"},
            {"id": "msg4", "t": 1776499997, "type": "chat", "from": "972586991569@c.us"},
        ],
        preview="An expensive strawberry cheesecake has been delivered to your front door.",
        max_messages=15,
    )

    assert [message.message_id for message in recent_messages] == ["msg1"]
    assert [message.text for message in recent_messages] == ["An expensive strawberry cheesecake has been delivered to your front door."]


def test_latest_thread_summary_prefers_latest_recent_message_over_preview_inference() -> None:
    thread = IndexedDBThread(
        jid="sam@lid",
        display_name="Sam Wasserman",
        phone_or_history_id="13106662345@c.us",
        labels=["Follow Up"],
        last_message_timestamp=1776440411,
        unread_count=0,
        preview="https://imports-emotional-dollar-grade.trycloudflare.com",
        timestamp_label="Friday",
        visible_in_chat_list=True,
        recent_messages=[
            RecentMessage(
                message_id="true_sam_latest",
                timestamp=1776440411,
                iso_timestamp="2026-04-17T15:40:11+00:00",
                direction="outbound",
                sender="Me",
                text="https://imports-emotional-dollar-grade.trycloudflare.com",
                text_available=True,
                message_type="chat",
                subtype=None,
            ),
            RecentMessage(
                message_id="false_sam_prev",
                timestamp=1776439157,
                iso_timestamp="2026-04-17T15:19:17+00:00",
                direction="inbound",
                sender="Sam Wasserman",
                text=None,
                text_available=False,
                message_type="image",
                subtype=None,
            ),
        ],
    )

    assert WhatsAppCollector._latest_thread_summary(thread) == (
        "2026-04-17T15:40:11+00:00",
        "outbound",
        "Me",
        "https://imports-emotional-dollar-grade.trycloudflare.com",
    )
