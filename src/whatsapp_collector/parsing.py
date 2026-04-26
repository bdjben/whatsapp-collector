from __future__ import annotations

import re

WEEKDAY_OR_TIME = re.compile(
    r"^(Today|Yesterday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{1,2}:\d{2}|\d{1,2}/\d{1,2}/\d{2,4})$",
    re.IGNORECASE,
)
UI_NOISE = {
    "All",
    "Unread",
    "Favorites",
    "Locked chats",
    "Labels",
    "Business tools",
    "2",
}


def _clean_lines(body: str) -> list[str]:
    return [line.replace("\u200e", "").strip() for line in body.splitlines() if line.strip()]


def parse_labels(body: str) -> list[dict[str, int | str]]:
    lines = _clean_lines(body)
    results: list[dict[str, int | str]] = []
    seen: set[tuple[str, int]] = set()
    for idx in range(len(lines) - 1):
        name = lines[idx]
        count_line = lines[idx + 1]
        match = re.fullmatch(r"(\d+) chats?", count_line)
        if not match:
            continue
        if name in UI_NOISE:
            continue
        item = (name, int(match.group(1)))
        if item in seen:
            continue
        seen.add(item)
        results.append({"name": item[0], "chat_count": item[1]})
    return results


def parse_chat_list(body: str) -> list[dict[str, int | str | bool]]:
    lines = _clean_lines(body)
    rows: list[dict[str, int | str | bool]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line in UI_NOISE or line.endswith("chats") or line.endswith("chat"):
            idx += 1
            continue
        if idx + 2 >= len(lines) or not WEEKDAY_OR_TIME.match(lines[idx + 1]):
            idx += 1
            continue

        chat_name = line
        timestamp_label = lines[idx + 1]
        preview = lines[idx + 2]
        idx += 3

        unread_count = 0
        unread_flag = False
        if idx < len(lines):
            unread_line = lines[idx]
            unread_match = re.fullmatch(r"(\d+) unread messages?", unread_line, re.IGNORECASE)
            badge_match = re.fullmatch(r"\d+", unread_line)
            if unread_match:
                unread_count = int(unread_match.group(1))
                unread_flag = unread_count > 0
                idx += 1
            elif badge_match:
                unread_count = int(unread_line)
                unread_flag = unread_count > 0
                idx += 1
            elif unread_line.lower() == "unread":
                unread_count = 1
                unread_flag = True
                idx += 1

        rows.append(
            {
                "chat_name": chat_name,
                "timestamp_label": timestamp_label,
                "preview": preview,
                "unread_count": unread_count,
                "unread_flag": unread_flag,
            }
        )
    return rows
