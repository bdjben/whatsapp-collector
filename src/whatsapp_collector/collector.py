from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import re
from typing import Any

from whatsapp_collector.chrome_session import ChromeWhatsAppSession
from whatsapp_collector.models import (
    ChatRow,
    IndexedDBThread,
    LabelStat,
    NormalizedEvent,
    RecentMessage,
    Snapshot,
)
from whatsapp_collector.parsing import parse_chat_list, parse_labels

PAGE_META_JS = 'JSON.stringify(Object.assign({PAGE_META:true},{title:document.title,url:location.href}))'
LABELS_BODY_JS = '''JSON.stringify({LABELS_BODY:true,body:(()=>{const close=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Close'); if(close){close.click();} const directLabels=document.getElementById("labels-filter")||[...document.querySelectorAll('button,[role="button"],a')].find(el=>((el.getAttribute('aria-label')||'')==='Labels')||((el.innerText||'').trim().startsWith('Labels'))); if(directLabels){directLabels.click(); return document.body.innerText;} const menu=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Menu'); if(menu){menu.click(); const menuLabels=[...document.querySelectorAll('button,[role="button"],a')].find(el=>((el.getAttribute('aria-label')||'')==='Labels')||((el.innerText||'').trim().startsWith('Labels'))); if(menuLabels){menuLabels.click(); return document.body.innerText;}} const tools=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Tools'); if(tools){tools.click(); const toolLabels=[...document.querySelectorAll('button,[role="button"],a')].find(el=>((el.getAttribute('aria-label')||'')==='Labels')||((el.innerText||'').trim().startsWith('Labels'))); if(toolLabels){toolLabels.click();}} return document.body.innerText;})()})'''
CHAT_LIST_BODY_JS = r'''JSON.stringify({CHAT_LIST_BODY:true,...(()=>{const close=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Close'); if(close){close.click();} const all=document.getElementById("all-filter")||[...document.querySelectorAll('button,[role="button"],a')].find(el=>(el.innerText||'').trim()==='All'); if(all){all.click();} const timestampPattern=/^(Today|Yesterday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{1,2}:\d{2}(?:\s?[AP]M)?|\d{1,2}\/\d{1,2}\/\d{2,4})$/i; const unreadPattern=/^(\d+) unread messages?$/i; const rows=[...document.querySelectorAll('#pane-side [role="row"][data-testid^="list-item-"]')].map((row)=>{let lines=(row.innerText||'').split('\n').map(line=>line.replace(/\u200e/g,'').replace(/\u00a0/g,' ').trim()).filter(Boolean); let unreadCount=0; let unreadFlag=false; if(lines.length){const unreadMatch=lines[0].match(unreadPattern); if(unreadMatch){unreadCount=parseInt(unreadMatch[1],10)||0; unreadFlag=unreadCount>0; lines=lines.slice(1);} else if(/^unread$/i.test(lines[0])){unreadCount=1; unreadFlag=true; lines=lines.slice(1);} } if(lines.length && /^\d+$/.test(lines[lines.length-1])){const badgeCount=parseInt(lines[lines.length-1],10)||0; if(badgeCount>0){unreadCount=Math.max(unreadCount,badgeCount); unreadFlag=true;} lines=lines.slice(0,-1);} const timestampIndex=lines.findIndex((line,idx)=>idx>0&&timestampPattern.test(line)); if(timestampIndex <= 0){return null;} const chatName=lines[0]; const timestampLabel=lines[timestampIndex]; const preview=lines.slice(timestampIndex+1).join(' ').replace(/\s+/g,' ').trim(); return {chat_name:chatName,timestamp_label:timestampLabel,preview:preview,unread_count:unreadCount,unread_flag:unreadFlag};}).filter(Boolean); return {body:document.body.innerText,rows:rows};})()})'''
MODEL_STORAGE_STORES_JS = '''window.__hermes_async_result = null;(function(){const req=indexedDB.open("model-storage");req.onerror=()=>{window.__hermes_async_result=JSON.stringify({error:String(req.error)});};req.onsuccess=()=>{const db=req.result;window.__hermes_async_result=JSON.stringify({stores:Array.from(db.objectStoreNames)});db.close();};})();"started"'''

DEFAULT_EXCLUDED_LABELS = ["Excluded Label", "Archive Label"]
MAX_MESSAGE_LOOKBACK_HARD_LIMIT = 15
DEFAULT_MAX_MESSAGES = MAX_MESSAGE_LOOKBACK_HARD_LIMIT
DEFAULT_ALL_VIEW_CHAT_LIMIT = 15


class WhatsAppCollector:
    def __init__(self, session: ChromeWhatsAppSession | None = None) -> None:
        self.session = session or ChromeWhatsAppSession()

    def collect_labels(self) -> list[LabelStat]:
        payload = self.session.run_json(LABELS_BODY_JS)
        return [LabelStat(**item) for item in parse_labels(payload["body"])]

    def collect_chat_list(self) -> list[ChatRow]:
        payload = self.session.run_json(CHAT_LIST_BODY_JS)
        structured_rows = payload.get("rows") if isinstance(payload, dict) else None
        if isinstance(structured_rows, list) and structured_rows:
            rows: list[ChatRow] = []
            for item in structured_rows:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    ChatRow(
                        chat_name=str(item.get("chat_name") or "").strip(),
                        timestamp_label=str(item.get("timestamp_label") or "").strip(),
                        preview=str(item.get("preview") or "").strip(),
                        unread_count=int(item.get("unread_count") or 0),
                        unread_flag=bool(item.get("unread_flag")),
                    )
                )
            rows = [row for row in rows if row.chat_name and row.timestamp_label]
            if rows:
                return rows
        return [ChatRow(**item) for item in parse_chat_list(payload["body"])]

    def collect_snapshot(self) -> Snapshot:
        page = self.session.run_json(PAGE_META_JS)
        return Snapshot(
            page_title=page["title"],
            page_url=page["url"],
            labels=self.collect_labels(),
            chat_list=self.collect_chat_list(),
        )

    @staticmethod
    def _effective_excluded_labels(exclude_labels: list[str] | None) -> list[str]:
        merged: list[str] = []
        for label in [*DEFAULT_EXCLUDED_LABELS, *(exclude_labels or [])]:
            clean = WhatsAppCollector._clean_label_name(label)
            if clean and clean not in merged:
                merged.append(clean)
        return merged

    def collect_labeled_threads(
        self,
        *,
        allow_labels: list[str] | None = None,
        exclude_labels: list[str] | None = None,
        max_messages: int = MAX_MESSAGE_LOOKBACK_HARD_LIMIT,
        snapshot: Snapshot | None = None,
    ) -> list[IndexedDBThread]:
        snapshot = snapshot or self.collect_snapshot()
        stores = self._model_storage_stores()
        required = {"label", "label-association", "contact", "group-metadata", "chat", "message"}
        missing = sorted(required - set(stores))
        if missing:
            raise ValueError(f"Missing required IndexedDB stores: {missing}")

        max_messages = self._bounded_max_messages(max_messages)
        allowed = self._normalized_label_set(allow_labels)
        excluded = self._normalized_label_set(self._effective_excluded_labels(exclude_labels))

        label_rows = self._idb_read_all("label")
        association_rows = self._idb_read_all("label-association")
        contact_rows = self._idb_read_all("contact")
        group_rows = self._idb_read_all("group-metadata")
        chat_rows = self._idb_read_all("chat")
        message_rows = self._idb_read_all("message")

        labels_by_id = {row["value"]["id"]: row["value"] for row in label_rows}
        contacts_by_id = {row["value"]["id"]: row["value"] for row in contact_rows}
        groups_by_id = {row["value"]["id"]: row["value"] for row in group_rows}
        chats_by_id = {row["value"]["id"]: row["value"] for row in chat_rows}
        visible_map = self._visible_chat_map(snapshot.chat_list)
        messages_by_jid = self._group_messages_by_jid(message_rows)

        labels_for_jid: dict[str, set[str]] = defaultdict(set)
        for row in association_rows:
            value = row["value"]
            if value.get("type") != "jid":
                continue
            label = labels_by_id.get(value["labelId"])
            if not label:
                continue
            labels_for_jid[value["associationId"]].add(self._clean_label_name(label["name"]))

        threads: list[IndexedDBThread] = []
        for jid, label_names in labels_for_jid.items():
            clean_labels = sorted({self._clean_label_name(label) for label in label_names if label})
            normalized_thread_labels = {self._normalize_label_slug(label) for label in clean_labels}
            if not clean_labels:
                continue
            if self._thread_has_only_excluded_labels(normalized_thread_labels, excluded):
                continue
            if allowed and not self._label_set_matches(normalized_thread_labels, allowed):
                continue


            contact = contacts_by_id.get(jid, {})
            group = groups_by_id.get(jid, {})
            chat = chats_by_id.get(jid)

            phone_or_history_id = None
            if contact:
                phone_or_history_id = contact.get("phoneNumber")
            if not phone_or_history_id and chat:
                phone_or_history_id = chat.get("historyChatId") or chat.get("accountLid")

            display_name = self._resolve_display_name(jid, contact, group)
            visible = visible_map.get(display_name)
            unread_count = int((chat or {}).get("unreadCount") or (visible.unread_count if visible else 0) or 0)
            recent_messages = self._recent_messages_for_thread(
                jid=jid,
                display_name=display_name,
                phone_or_history_id=phone_or_history_id,
                messages=messages_by_jid.get(jid, []),
                preview=(visible.preview if visible else ""),
                max_messages=max_messages,
            )
            threads.append(
                IndexedDBThread(
                    jid=jid,
                    display_name=display_name,
                    phone_or_history_id=phone_or_history_id,
                    labels=clean_labels,
                    last_message_timestamp=(chat or {}).get("t"),
                    unread_count=unread_count,
                    preview=visible.preview if visible else "",
                    timestamp_label=visible.timestamp_label if visible else None,
                    visible_in_chat_list=visible is not None,
                    recent_messages=recent_messages,
                )
            )

        threads.sort(key=lambda item: ((item.last_message_timestamp or 0), item.display_name), reverse=True)
        return threads

    def collect_events(
        self,
        allow_labels: list[str] | None = None,
        *,
        exclude_labels: list[str] | None = None,
        max_messages: int = MAX_MESSAGE_LOOKBACK_HARD_LIMIT,
    ) -> list[NormalizedEvent]:
        events: list[NormalizedEvent] = []
        for thread in self.collect_labeled_threads(
            allow_labels=allow_labels,
            exclude_labels=exclude_labels,
            max_messages=max_messages,
        ):
            events.append(
                self._event_from_thread(thread)
            )
        return events

    def collect_full_snapshot(
        self,
        allow_labels: list[str] | None = None,
        *,
        exclude_labels: list[str] | None = None,
        max_messages: int = MAX_MESSAGE_LOOKBACK_HARD_LIMIT,
    ) -> dict[str, Any]:
        max_messages = self._bounded_max_messages(max_messages)
        excluded_labels = self._effective_excluded_labels(exclude_labels)
        snapshot = self.collect_snapshot()
        threads = self.collect_labeled_threads(
            allow_labels=allow_labels,
            exclude_labels=excluded_labels,
            max_messages=max_messages,
            snapshot=snapshot,
        )
        events = [self._event_from_thread(thread) for thread in threads]
        return snapshot.to_dict(
            allowed_labels=allow_labels,
            excluded_labels=excluded_labels,
            max_recent_messages=max_messages,
            labeled_threads=threads,
            events=events,
        )

    def collect_dashboard_export(
        self,
        *,
        account_label: str = "WhatsApp",
        allow_labels: list[str] | None = None,
        exclude_labels: list[str] | None = None,
        max_messages: int = MAX_MESSAGE_LOOKBACK_HARD_LIMIT,
        max_all_chats: int = DEFAULT_ALL_VIEW_CHAT_LIMIT,
    ) -> dict[str, Any]:
        max_messages = self._bounded_max_messages(max_messages)
        max_all_chats = max(1, int(max_all_chats))
        excluded_labels = self._effective_excluded_labels(exclude_labels)
        snapshot = self.collect_snapshot()
        threads = self.collect_labeled_threads(
            allow_labels=allow_labels,
            exclude_labels=excluded_labels,
            max_messages=max_messages,
            snapshot=snapshot,
        )
        exported_threads = []
        for thread in threads:
            clean_labels = [self._clean_label_name(label) for label in thread.labels]
            if not clean_labels:
                continue
            last_message_at, last_direction, last_sender, last_text = self._latest_thread_summary(thread)

            recent_messages = [
                {
                    "messageId": message.message_id,
                    "timestamp": message.iso_timestamp,
                    "direction": message.direction,
                    "sender": message.sender,
                    "text": message.text,
                    "textAvailable": message.text_available,
                    "messageType": message.message_type,
                    "subtype": message.subtype,
                }
                for message in thread.recent_messages
            ]
            exported_threads.append(
                {
                    "threadKey": thread.jid,
                    "chatTitle": thread.display_name,
                    "chatType": "group" if thread.jid.endswith("@g.us") else "direct",
                    "participants": [
                        {
                            "name": thread.display_name,
                            "phone": thread.phone_or_history_id,
                        }
                    ],
                    "labelsRaw": clean_labels,
                    "labelsNormalized": [self._normalize_label_slug(label) for label in clean_labels],
                    "unread": thread.unread_count > 0,
                    "starred": False,
                    "requiresResponse": (thread.unread_count > 0) or ("Follow Up" in clean_labels) or ("Important" in clean_labels),
                    "lastMessageAt": last_message_at,
                    "lastMessageDirection": last_direction,
                    "lastMessageSender": last_sender,
                    "lastMessageText": last_text,
                    "recentMessages": recent_messages,
                    "messages": list(recent_messages),
                }
            )
        excluded_recent_chat_names = self._excluded_recent_chat_names(excluded_labels)
        refreshed_chat_rows = self.collect_chat_list()
        planned_chat_rows = self._plan_all_view_rows(snapshot.chat_list, refreshed_chat_rows, all_view_chat_limit=max_all_chats)
        exported_threads.extend(
            self._recent_default_view_exports(
                chat_rows=planned_chat_rows,
                existing_titles=[thread["chatTitle"] for thread in exported_threads],
                excluded_titles=excluded_recent_chat_names,
                max_messages=max_messages,
                limit=max_all_chats,
            )
        )
        return {
            "source": "whatsapp",
            "exportedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "account": {
                "platform": "whatsapp-web",
                "accountLabel": account_label,
            },
            "allowLabels": allow_labels or [],
            "excludeLabels": excluded_labels,
            "maxRecentMessages": max_messages,
            "maxAllViewChats": max_all_chats,
            "threads": exported_threads,
        }

    def _model_storage_stores(self) -> list[str]:
        payload = self.session.run_async_json(MODEL_STORAGE_STORES_JS)
        return list(payload["stores"])

    def _idb_read_all(self, store_name: str) -> list[dict[str, Any]]:
        quoted_store = store_name.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''window.__hermes_async_result = null;(function(){{const req=indexedDB.open("model-storage");req.onerror=()=>{{window.__hermes_async_result=JSON.stringify({{error:String(req.error)}});}};req.onsuccess=()=>{{const db=req.result;const tx=db.transaction("{quoted_store}","readonly");const os=tx.objectStore("{quoted_store}");const allRecords=[];const cursorReq=os.openCursor();cursorReq.onerror=()=>{{window.__hermes_async_result=JSON.stringify({{error:String(cursorReq.error)}});db.close();}};cursorReq.onsuccess=(event)=>{{const cursor=event.target.result;if(cursor){{allRecords.push({{ key: cursor.key, value: cursor.value }});cursor.continue();}} else {{window.__hermes_async_result=JSON.stringify(allRecords);db.close();}}}};}};}})();"started"'''
        payload = self.session.run_async_json(script)
        if isinstance(payload, dict) and payload.get("error"):
            raise ValueError(f"IndexedDB read failed for {store_name}: {payload['error']}")
        return list(payload)

    @staticmethod
    def _normalized_chat_identity(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    @staticmethod
    def _phone_digits(value: str | None) -> str:
        return re.sub(r"\D+", "", value or "")

    @classmethod
    def _alias_keys_for_value(cls, value: str | None) -> set[str]:
        aliases: set[str] = set()
        if not isinstance(value, str) or not value.strip():
            return aliases
        normalized = cls._normalized_chat_identity(value)
        if normalized:
            aliases.add(normalized)
        digits = cls._phone_digits(value)
        if digits:
            aliases.add(f"digits:{digits}")
        return aliases

    def _excluded_recent_chat_names(self, excluded_labels: list[str]) -> set[str]:
        normalized_excluded_labels = self._normalized_label_set(excluded_labels)
        if not normalized_excluded_labels:
            return set()

        label_rows = self._idb_read_all("label")
        association_rows = self._idb_read_all("label-association")
        contact_rows = self._idb_read_all("contact")
        group_rows = self._idb_read_all("group-metadata")

        labels_by_id = {row["value"]["id"]: row["value"] for row in label_rows}
        contacts_by_id = {row["value"]["id"]: row["value"] for row in contact_rows}
        groups_by_id = {row["value"]["id"]: row["value"] for row in group_rows}

        excluded_names: set[str] = set()
        labels_for_jid: dict[str, set[str]] = defaultdict(set)
        for row in association_rows:
            value = row["value"]
            if value.get("type") != "jid":
                continue
            label = labels_by_id.get(value["labelId"])
            if not label:
                continue
            labels_for_jid[value["associationId"]].add(self._normalize_label_slug(label["name"]))

        for jid, normalized_labels in labels_for_jid.items():
            if not self._thread_has_only_excluded_labels(normalized_labels, normalized_excluded_labels):
                continue
            display_name = self._resolve_display_name(jid, contacts_by_id.get(jid, {}), groups_by_id.get(jid, {}))
            excluded_names.add(self._normalized_chat_identity(display_name))
        return excluded_names

    @staticmethod
    def _is_trackable_all_view_row(row: ChatRow) -> bool:
        normalized = WhatsAppCollector._normalized_chat_identity(row.chat_name)
        return bool(normalized) and normalized != 'you'

    def _plan_all_view_rows(
        self,
        initial_rows: list[ChatRow],
        refreshed_rows: list[ChatRow],
        *,
        initial_limit: int = 15,
        hard_cap: int = 20,
        all_view_chat_limit: int | None = None,
    ) -> list[ChatRow]:
        if all_view_chat_limit is not None:
            initial_limit = max(1, int(all_view_chat_limit))
            hard_cap = initial_limit
        planned: list[ChatRow] = []
        seen: set[str] = set()

        def append_rows(rows: list[ChatRow], limit: int) -> None:
            for row in rows[:limit]:
                if not self._is_trackable_all_view_row(row):
                    continue
                normalized = self._normalized_chat_identity(row.chat_name)
                if normalized in seen:
                    continue
                if len(planned) >= hard_cap:
                    return
                seen.add(normalized)
                planned.append(row)

        append_rows(initial_rows, initial_limit)
        append_rows(refreshed_rows, initial_limit)
        return planned

    def _recent_default_view_exports(
        self,
        *,
        chat_rows: list[ChatRow],
        existing_titles: list[str],
        excluded_titles: set[str],
        max_messages: int,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        seen_titles = {self._normalized_chat_identity(title) for title in existing_titles}
        recent_exports: list[dict[str, Any]] = []
        contacts_by_name, groups_by_name, chats_by_id, chats_by_alias, messages_by_jid = self._default_view_lookup_maps()
        for row in chat_rows[:limit]:
            normalized_name = self._normalized_chat_identity(row.chat_name)
            if not normalized_name or normalized_name in seen_titles or normalized_name in excluded_titles:
                continue
            seen_titles.add(normalized_name)

            row_aliases = self._alias_keys_for_value(row.chat_name)
            contact = next((contacts_by_name[alias] for alias in row_aliases if alias in contacts_by_name), None)
            group = next((groups_by_name[alias] for alias in row_aliases if alias in groups_by_name), None)
            chat = self._resolve_default_view_chat(row, contact=contact, group=group, chats_by_alias=chats_by_alias)
            jid = (chat or contact or group or {}).get("id")
            chat = chats_by_id.get(jid, chat) if jid else chat
            phone_or_history_id = None
            if contact:
                phone_or_history_id = contact.get("phoneNumber")
            if not phone_or_history_id and chat:
                phone_or_history_id = chat.get("historyChatId") or chat.get("accountLid")

            direction = self._infer_direction(row.preview, row.unread_count)
            sender = row.chat_name if direction == "inbound" else "Me"
            recent_messages = []
            candidate_message_keys = self._candidate_message_keys_for_default_view_row(row, contact=contact, group=group, chat=chat)
            candidate_messages = self._collect_candidate_messages(candidate_message_keys, messages_by_jid)
            if jid and any(self._extract_message_text(message) for message in candidate_messages):
                recent_messages = [
                    {
                        "messageId": message.message_id,
                        "timestamp": message.iso_timestamp,
                        "direction": message.direction,
                        "sender": message.sender,
                        "text": message.text,
                        "textAvailable": message.text_available,
                        "messageType": message.message_type,
                        "subtype": message.subtype,
                    }
                    for message in self._recent_messages_for_thread(
                        jid=jid,
                        display_name=row.chat_name,
                        phone_or_history_id=phone_or_history_id,
                        messages=candidate_messages,
                        preview=row.preview,
                        max_messages=max_messages,
                    )
                ]
            if not recent_messages:
                try:
                    recent_messages = self._opened_chat_recent_messages_for_chat(row.chat_name, max_messages=max_messages)
                except RuntimeError:
                    recent_messages = []
            if not recent_messages:
                continue

            last_message = recent_messages[0]
            recent_exports.append(
                {
                    "threadKey": jid or f"visible:{self._normalize_label_slug(row.chat_name)}",
                    "chatTitle": row.chat_name,
                    "chatType": "group" if jid and jid.endswith("@g.us") else ("direct" if jid else "unknown"),
                    "participants": [{"name": row.chat_name, "phone": phone_or_history_id}],
                    "labelsRaw": ["Unlabeled"],
                    "labelsNormalized": ["unlabeled"],
                    "unread": row.unread_count > 0 or row.unread_flag,
                    "starred": False,
                    "requiresResponse": row.unread_count > 0 or row.unread_flag,
                    "lastMessageAt": last_message["timestamp"],
                    "lastMessageDirection": last_message["direction"],
                    "lastMessageSender": last_message["sender"],
                    "lastMessageText": last_message["text"] or row.preview,
                    "timestampLabel": row.timestamp_label,
                    "sourceView": "all",
                    "recentMessages": recent_messages,
                    "messages": [dict(message) for message in recent_messages],
                }
            )
        return recent_exports

    def _opened_chat_recent_messages_for_chat(self, chat_name: str, *, max_messages: int) -> list[dict[str, Any]]:
        click_expression = self._chat_row_click_point_expression(chat_name)
        self.session.click_point(click_expression)
        payload = self.session.run_json(self._opened_chat_recent_messages_js(max_messages=max_messages))
        opened_title = str(payload.get("openedChatTitle") or "").strip()
        if not opened_title:
            return []
        opened_normalized = self._normalized_chat_identity(opened_title)
        target_normalized = self._normalized_chat_identity(chat_name)
        if opened_normalized and target_normalized and opened_normalized != target_normalized:
            return []
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return []
        opened_messages: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("id") or "").strip()
            if not message_id:
                continue
            text = self._extract_message_text(item)
            if not text:
                continue
            direction = self._message_direction(item)
            sender = self._message_sender(item, display_name=chat_name, direction=direction)
            opened_messages.append(
                {
                    "messageId": message_id,
                    "timestamp": self._format_timestamp(item.get("t")),
                    "direction": direction,
                    "sender": sender,
                    "text": text,
                    "textAvailable": True,
                    "messageType": item.get("type") or "unknown",
                    "subtype": item.get("subtype"),
                }
            )
        return opened_messages

    @staticmethod
    def _chat_row_click_point_expression(chat_name: str) -> str:
        return f'''(()=>{{const targetTitle={json.dumps(chat_name)}; const titleEl=[...document.querySelectorAll('#pane-side [title]')].find(el => ((el.getAttribute('title')||'').trim()===targetTitle)); if(!titleEl) return null; const clickable=titleEl.closest('[role="gridcell"]')||titleEl.closest('[data-testid="cell-frame-container"]')||titleEl; clickable.scrollIntoView({{block:'center'}}); const rect=clickable.getBoundingClientRect(); return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2}};}})()'''

    @staticmethod
    def _opened_chat_recent_messages_js(*, max_messages: int) -> str:
        return f'''(async ()=>{{await new Promise(resolve => setTimeout(resolve, 1200)); const titleCandidates=[...document.querySelectorAll('header [title], #main header [title], #main header span[dir="auto"], #main header div[dir="auto"]')].map(el => (el.getAttribute('title') || (el.textContent||'').trim())).filter(Boolean); const openedChatTitle=titleCandidates.find(title => !['Profile details','click here for contact info'].includes(title)) || ''; const summarizeMsg=(msg)=>{{if(!msg||typeof msg!=='object') return null; const normalizeJid=(value)=>{{if(!value) return null; if(typeof value==='string') return value; if(typeof value==='object' && value._serialized) return value._serialized; return null;}}; return {{id: typeof msg.id === 'string' ? msg.id : (msg.id && msg.id._serialized) || null, t: msg.t ?? null, type: msg.type || null, subtype: msg.subtype || null, body: typeof msg.body === 'string' ? msg.body : null, caption: typeof msg.caption === 'string' ? msg.caption : null, text: typeof msg.text === 'string' ? msg.text : null, matchedText: typeof msg.matchedText === 'string' ? msg.matchedText : null, from: normalizeJid(msg.from), to: normalizeJid(msg.to), notifyName: typeof msg.notifyName === 'string' ? msg.notifyName : null}}; }}; const messages=[...document.querySelectorAll('#main [data-testid="msg-container"]')].slice(-{int(max_messages)}).map((container)=>{{ const fiberKey=Object.keys(container).find(k=>k.startsWith('__reactFiber$')); let fiber=fiberKey ? container[fiberKey] : null; while(fiber){{ const props=fiber.memoizedProps; if(props && typeof props==='object' && props.msg){{ return summarizeMsg(props.msg); }} fiber=fiber.return; }} return null; }}).filter(item => item && item.id).reverse(); return JSON.stringify({{OPENED_CHAT_RECENT_MESSAGES:true, openedChatTitle, messages}});}})()'''

    @staticmethod
    def _parse_visible_dom_iso_timestamp(pre: str) -> str | None:
        match = re.match(r'^\[(?P<stamp>[^\]]+)\]', pre or '')
        if not match:
            return None
        stamp = match.group('stamp').strip()
        for fmt in ['%I:%M %p, %m/%d/%Y', '%H:%M, %m/%d/%Y']:
            try:
                return datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_visible_dom_sender(pre: str, *, default_sender: str) -> str:
        match = re.match(r'^\[[^\]]+\]\s*(?P<sender>.*?):\s*$', pre or '')
        sender = (match.group('sender') if match else '') or default_sender
        return sender.strip() or default_sender

    @staticmethod
    def _infer_visible_dom_direction(sender: str) -> str:
        normalized_sender = (sender or '').strip().lower()
        if normalized_sender in {'you', 'ben badejo'}:
            return 'outbound'
        return 'inbound'

    @classmethod
    def _contacts_by_normalized_name(cls, contact_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        contacts: dict[str, dict[str, Any]] = {}
        for row in contact_rows:
            value = row["value"]
            candidates = [
                value.get("name"),
                value.get("shortName"),
                value.get("displayNameLID"),
                value.get("phoneNumber"),
                value.get("id"),
            ]
            for candidate in candidates:
                for alias in cls._alias_keys_for_value(candidate):
                    if alias not in contacts:
                        contacts[alias] = value
        return contacts

    @classmethod
    def _groups_by_normalized_name(cls, group_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in group_rows:
            value = row["value"]
            for alias in cls._alias_keys_for_value(value.get("subject") or value.get("id") or ""):
                if alias not in groups:
                    groups[alias] = value
        return groups

    @classmethod
    def _chat_aliases(cls, chat: dict[str, Any]) -> set[str]:
        aliases: set[str] = set()
        for candidate in [
            chat.get("id"),
            chat.get("historyChatId"),
            chat.get("accountLid"),
            chat.get("name"),
            chat.get("formattedTitle"),
            chat.get("title"),
        ]:
            aliases.update(cls._alias_keys_for_value(candidate))
        return aliases

    @classmethod
    def _chats_by_alias(cls, chat_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        chats: dict[str, dict[str, Any]] = {}
        for row in chat_rows:
            value = row["value"]
            for alias in cls._chat_aliases(value):
                if alias not in chats:
                    chats[alias] = value
        return chats

    @classmethod
    def _string_candidates_for_default_view_row(
        cls,
        row: ChatRow,
        *,
        contact: dict[str, Any] | None,
        group: dict[str, Any] | None,
        chat: dict[str, Any] | None,
    ) -> set[str]:
        candidates: set[str] = {row.chat_name}
        for value in [contact or {}, group or {}, chat or {}]:
            for key in ["id", "phoneNumber", "historyChatId", "accountLid", "name", "shortName", "displayNameLID", "subject", "formattedTitle", "title"]:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    candidates.add(candidate)
        return candidates

    @classmethod
    def _resolve_default_view_chat(
        cls,
        row: ChatRow,
        *,
        contact: dict[str, Any] | None,
        group: dict[str, Any] | None,
        chats_by_alias: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for candidate in cls._string_candidates_for_default_view_row(row, contact=contact, group=group, chat=None):
            for alias in cls._alias_keys_for_value(candidate):
                match = chats_by_alias.get(alias)
                if match:
                    return match
        return None

    @classmethod
    def _candidate_message_keys_for_default_view_row(
        cls,
        row: ChatRow,
        *,
        contact: dict[str, Any] | None,
        group: dict[str, Any] | None,
        chat: dict[str, Any] | None,
    ) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()
        for candidate in cls._string_candidates_for_default_view_row(row, contact=contact, group=group, chat=chat):
            if candidate not in seen:
                seen.add(candidate)
                keys.append(candidate)
        return keys

    @staticmethod
    def _collect_candidate_messages(candidate_keys: list[str], messages_by_jid: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for key in candidate_keys:
            for message in messages_by_jid.get(key, []):
                message_id = str(message.get("id") or "")
                if message_id and message_id in seen_ids:
                    continue
                if message_id:
                    seen_ids.add(message_id)
                collected.append(message)
        return collected

    def _default_view_lookup_maps(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        contact_rows = self._idb_read_all("contact")
        group_rows = self._idb_read_all("group-metadata")
        chat_rows = self._idb_read_all("chat")
        message_rows = self._idb_read_all("message")
        contacts_by_name = self._contacts_by_normalized_name(contact_rows)
        groups_by_name = self._groups_by_normalized_name(group_rows)
        chats_by_id = {row["value"]["id"]: row["value"] for row in chat_rows}
        chats_by_alias = self._chats_by_alias(chat_rows)
        messages_by_jid = self._group_messages_by_jid(message_rows)
        return contacts_by_name, groups_by_name, chats_by_id, chats_by_alias, messages_by_jid

    @staticmethod
    def _visible_chat_map(chat_rows: list[ChatRow]) -> dict[str, ChatRow]:
        return {row.chat_name: row for row in chat_rows}

    @staticmethod
    def _resolve_display_name(jid: str, contact: dict[str, Any], group: dict[str, Any]) -> str:
        return (
            contact.get("name")
            or contact.get("shortName")
            or group.get("subject")
            or contact.get("displayNameLID")
            or contact.get("phoneNumber")
            or jid
        )

    @staticmethod
    def _clean_label_name(label: str) -> str:
        return label.replace("\u200e", "").strip()

    @staticmethod
    def _normalize_label_slug(label: str) -> str:
        cleaned = WhatsAppCollector._clean_label_name(label).lower()
        cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
        return cleaned.strip("-")

    @staticmethod
    def _normalized_label_set(labels: list[str] | None) -> set[str]:
        return {
            WhatsAppCollector._normalize_label_slug(label)
            for label in (labels or [])
            if label and label.strip()
        }

    @staticmethod
    def _label_matches_target(label: str, target: str) -> bool:
        return label == target or label.startswith(target) or target.startswith(label)

    @classmethod
    def _matching_labels(cls, thread_labels: set[str], target_labels: set[str]) -> set[str]:
        matched: set[str] = set()
        for thread_label in thread_labels:
            for target in target_labels:
                if cls._label_matches_target(thread_label, target):
                    matched.add(thread_label)
                    break
        return matched

    @classmethod
    def _thread_has_only_excluded_labels(cls, thread_labels: set[str], excluded_labels: set[str]) -> bool:
        if not thread_labels or not excluded_labels:
            return False
        matched = cls._matching_labels(thread_labels, excluded_labels)
        return bool(matched) and matched == thread_labels

    @classmethod
    def _label_set_matches(cls, thread_labels: set[str], target_labels: set[str]) -> bool:
        return bool(cls._matching_labels(thread_labels, target_labels))

    @staticmethod
    def _bounded_max_messages(max_messages: int | None) -> int:
        if max_messages is None:
            return MAX_MESSAGE_LOOKBACK_HARD_LIMIT
        return max(1, int(max_messages))

    @staticmethod
    def _group_messages_by_jid(message_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in message_rows:
            key = row.get("key")
            if not isinstance(key, str):
                continue
            parts = key.split("_", 2)
            if len(parts) < 3:
                continue
            grouped[parts[1]].append(row["value"])
        return grouped

    def _recent_messages_for_thread(
        self,
        *,
        jid: str,
        display_name: str,
        phone_or_history_id: str | None,
        messages: list[dict[str, Any]],
        preview: str,
        max_messages: int,
    ) -> list[RecentMessage]:
        sorted_messages = sorted(messages, key=lambda item: item.get("t") or 0, reverse=True)
        recent_messages: list[RecentMessage] = []
        for message in sorted_messages:
            message_text = self._extract_message_text(message)
            if not message_text:
                continue
            direction = self._message_direction(message)
            sender = self._message_sender(message, display_name=display_name, direction=direction)
            recent_messages.append(
                RecentMessage(
                    message_id=message.get("id") or f"{jid}:{message.get('t') or 0}",
                    timestamp=message.get("t"),
                    iso_timestamp=self._format_timestamp(message.get("t")),
                    direction=direction,
                    sender=sender,
                    text=message_text,
                    text_available=True,
                    message_type=message.get("type") or "unknown",
                    subtype=message.get("subtype"),
                )
            )
            if len(recent_messages) >= max_messages:
                break
        return recent_messages

    @staticmethod
    def _extract_message_text(message: dict[str, Any]) -> str | None:
        candidates = [
            message.get("body"),
            message.get("caption"),
            message.get("text"),
            message.get("matchedText"),
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    @staticmethod
    def _message_direction(message: dict[str, Any]) -> str:
        message_id = message.get("id") or ""
        if isinstance(message_id, str) and message_id.startswith("true_"):
            return "outbound"
        return "inbound"

    @staticmethod
    def _message_sender(message: dict[str, Any], *, display_name: str, direction: str) -> str | None:
        if direction == "outbound":
            return "Me"
        sender = message.get("notifyName") or message.get("from")
        if isinstance(sender, str) and sender.strip():
            return sender
        return display_name

    @staticmethod
    def _event_from_thread(thread: IndexedDBThread) -> NormalizedEvent:
        return NormalizedEvent(
            source="whatsapp_business",
            external_thread_id=thread.jid,
            display_name=thread.display_name,
            labels=thread.labels,
            summary=thread.preview,
            importance=WhatsAppCollector._importance_for_labels(thread.labels),
            status_hint=WhatsAppCollector._status_for_labels(thread.labels),
            unread_count=thread.unread_count,
            last_message_timestamp=thread.last_message_timestamp,
            timestamp_label=thread.timestamp_label,
            visible_in_chat_list=thread.visible_in_chat_list,
            recent_message_count=len(thread.recent_messages),
            recent_message_text_available_count=sum(1 for message in thread.recent_messages if message.text_available),
        )

    @staticmethod
    def _latest_thread_summary(thread: IndexedDBThread) -> tuple[str | None, str, str | None, str]:
        if thread.recent_messages:
            latest = thread.recent_messages[0]
            return (
                latest.iso_timestamp,
                latest.direction,
                latest.sender,
                latest.text or "",
            )

        last_message_at = WhatsAppCollector._format_timestamp(thread.last_message_timestamp)
        last_direction = WhatsAppCollector._infer_direction(thread.preview, thread.unread_count)
        last_sender = thread.display_name if last_direction == "inbound" else "Me"
        return (last_message_at, last_direction, last_sender, thread.preview)

    @staticmethod
    def _format_timestamp(timestamp: int | None) -> str | None:
        if not timestamp:
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _infer_direction(preview: str, unread_count: int) -> str:
        lowered = (preview or "").strip().lower()
        if unread_count > 0:
            return "inbound"
        if lowered.startswith("you ") or lowered.startswith("you reacted") or lowered.startswith("reacted "):
            return "outbound"
        return "inbound"

    @staticmethod
    def _importance_for_labels(labels: list[str]) -> str:
        lowered = {WhatsAppCollector._clean_label_name(label).lower() for label in labels}
        if "important" in lowered:
            return "high"
        if "business" in lowered or "past client" in lowered:
            return "medium"
        return "medium"

    @staticmethod
    def _status_for_labels(labels: list[str]) -> str:
        lowered = {WhatsAppCollector._clean_label_name(label).lower() for label in labels}
        if "follow up" in lowered:
            return "follow_up"
        if "important" in lowered:
            return "active"
        if "past client" in lowered:
            return "archive_watch"
        return "active"
