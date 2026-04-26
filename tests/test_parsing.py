from whatsapp_collector.parsing import parse_chat_list, parse_labels


def test_parse_labels_extracts_names_and_counts_and_ignores_noise() -> None:
    body = """2
Labels
‎Important
3 chats
‎Follow Up
31 chats
Groups
0 chats
All
Unread
Favorites
Groups
Locked chats
"""

    assert parse_labels(body) == [
        {"name": "Important", "chat_count": 3},
        {"name": "Follow Up", "chat_count": 31},
        {"name": "Groups", "chat_count": 0},
    ]


def test_parse_labels_dedupes_repeated_rows() -> None:
    body = """Labels
Important
3 chats
Important
3 chats
Business
4 chats
Business
4 chats
"""

    assert parse_labels(body) == [
        {"name": "Important", "chat_count": 3},
        {"name": "Business", "chat_count": 4},
    ]


def test_parse_chat_list_extracts_preview_timestamp_and_unread_flags() -> None:
    body = """2
All
Unread
Favorites
Groups
Locked chats
Example Contact
Tuesday
I want to work with you - timing - need to push to beginning of may
Example Lead
Monday
Haha i hear you
7 unread messages
Tamar Simon
Friday
Photo
1
"""

    assert parse_chat_list(body) == [
        {
            "chat_name": "Example Contact",
            "timestamp_label": "Tuesday",
            "preview": "I want to work with you - timing - need to push to beginning of may",
            "unread_count": 0,
            "unread_flag": False,
        },
        {
            "chat_name": "Example Lead",
            "timestamp_label": "Monday",
            "preview": "Haha i hear you",
            "unread_count": 7,
            "unread_flag": True,
        },
        {
            "chat_name": "Tamar Simon",
            "timestamp_label": "Friday",
            "preview": "Photo",
            "unread_count": 1,
            "unread_flag": True,
        },
    ]
