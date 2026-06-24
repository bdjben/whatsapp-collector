from __future__ import annotations

import base64
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from whatsapp_collector.chrome_session import ChromeWhatsAppSession
from whatsapp_collector.models import (
    ChatRow,
    IndexedDBThread,
    LabelStat,
    NormalizedEvent,
    RecentAttachment,
    RecentMessage,
    Snapshot,
)
from whatsapp_collector.parsing import parse_chat_list, parse_labels

PAGE_META_JS = 'JSON.stringify(Object.assign({PAGE_META:true},{title:document.title,url:location.href}))'
LABELS_BODY_JS = '''JSON.stringify({LABELS_BODY:true,body:(()=>{const close=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Close'); if(close){close.click();} const directLabels=document.getElementById("labels-filter")||[...document.querySelectorAll('button,[role="button"],a')].find(el=>((el.getAttribute('aria-label')||'')==='Labels')||((el.innerText||'').trim().startsWith('Labels'))); if(directLabels){directLabels.click(); return document.body.innerText;} const menu=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Menu'); if(menu){menu.click(); const menuLabels=[...document.querySelectorAll('button,[role="button"],a')].find(el=>((el.getAttribute('aria-label')||'')==='Labels')||((el.innerText||'').trim().startsWith('Labels'))); if(menuLabels){menuLabels.click(); return document.body.innerText;}} const tools=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Tools'); if(tools){tools.click(); const toolLabels=[...document.querySelectorAll('button,[role="button"],a')].find(el=>((el.getAttribute('aria-label')||'')==='Labels')||((el.innerText||'').trim().startsWith('Labels'))); if(toolLabels){toolLabels.click();}} return document.body.innerText;})()})'''
CHAT_LIST_RESET_JS = r'''(async()=>{const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms)); const clickFirst=(predicate)=>{const item=[...document.querySelectorAll('button,[role="button"],a')].find(predicate); if(item){item.click(); return true;} return false;}; const clickedChats=clickFirst(el=>(el.getAttribute('aria-label')||'')==='Chats'); if(clickedChats){await sleep(350);} for(let i=0;i<3;i++){const panelButton=[...document.querySelectorAll('button,[role="button"]')].find(el=>['Close','Back'].includes(el.getAttribute('aria-label')||'')); if(!panelButton){break;} panelButton.click(); await sleep(250);} const all=document.getElementById("all-filter")||[...document.querySelectorAll('button,[role="button"],a')].find(el=>(el.innerText||'').trim()==='All'); if(all){all.click(); await sleep(250);} const pane=document.querySelector('#pane-side'); if(pane){pane.scrollTop=0; pane.dispatchEvent(new Event('scroll',{bubbles:true}));} return JSON.stringify({CHAT_LIST_RESET:true,ok:true,clickedChats});})()'''
CHAT_LIST_BODY_JS = r'''JSON.stringify({CHAT_LIST_BODY:true,...(()=>{const close=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Close'); if(close){close.click();} const all=document.getElementById("all-filter")||[...document.querySelectorAll('button,[role="button"],a')].find(el=>(el.innerText||'').trim()==='All'); if(all){all.click();} const timestampPattern=/^(Today|Yesterday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{1,2}:\d{2}(?:\s?[AP]M)?|\d{1,2}\/\d{1,2}\/\d{2,4})$/i; const unreadPattern=/^(\d+) unread messages?$/i; const rows=[...document.querySelectorAll('#pane-side [role="row"][data-testid^="list-item-"]')].map((row)=>{let lines=(row.innerText||'').split('\n').map(line=>line.replace(/\u200e/g,'').replace(/\u00a0/g,' ').trim()).filter(Boolean); let unreadCount=0; let unreadFlag=false; if(lines.length){const unreadMatch=lines[0].match(unreadPattern); if(unreadMatch){unreadCount=parseInt(unreadMatch[1],10)||0; unreadFlag=unreadCount>0; lines=lines.slice(1);} else if(/^unread$/i.test(lines[0])){unreadCount=1; unreadFlag=true; lines=lines.slice(1);} } if(lines.length && /^\d+$/.test(lines[lines.length-1])){const badgeCount=parseInt(lines[lines.length-1],10)||0; if(badgeCount>0){unreadCount=Math.max(unreadCount,badgeCount); unreadFlag=true;} lines=lines.slice(0,-1);} const timestampIndex=lines.findIndex((line,idx)=>idx>0&&timestampPattern.test(line)); if(timestampIndex <= 0){return null;} const chatName=lines[0]; const timestampLabel=lines[timestampIndex]; const preview=lines.slice(timestampIndex+1).join(' ').replace(/\s+/g,' ').trim(); return {chat_name:chatName,timestamp_label:timestampLabel,preview:preview,unread_count:unreadCount,unread_flag:unreadFlag};}).filter(Boolean); return {body:document.body.innerText,rows:rows};})()})'''
MODEL_STORAGE_STORES_JS = '''window.__hermes_async_result = null;(function(){const req=indexedDB.open("model-storage");req.onerror=()=>{window.__hermes_async_result=JSON.stringify({error:String(req.error)});};req.onsuccess=()=>{const db=req.result;window.__hermes_async_result=JSON.stringify({stores:Array.from(db.objectStoreNames)});db.close();};})();"started"'''

DEFAULT_EXCLUDED_LABELS = ["Excluded Label", "Archive Label"]
MAX_MESSAGE_LOOKBACK_HARD_LIMIT = 15
DEFAULT_MAX_MESSAGES = MAX_MESSAGE_LOOKBACK_HARD_LIMIT
DEFAULT_ALL_VIEW_CHAT_LIMIT = 15
GROUP_INCLUDE_STANDARD = "standard"
GROUP_INCLUDE_LABELED_ALWAYS = "labeledAlways"
MAX_AUTOMATIC_VIDEO_ATTACHMENT_BYTES = 10 * 1024 * 1024
ATTACHMENT_MESSAGE_TYPES = {
    "image",
    "document",
    "video",
    "ptt",
    "audio",
    "sticker",
}


class WhatsAppCollector:
    def __init__(self, session: ChromeWhatsAppSession | None = None) -> None:
        self.session = session or ChromeWhatsAppSession()
        self._idb_read_cache: dict[str, list[dict[str, Any]]] | None = None

    def collect_labels(self) -> list[LabelStat]:
        payload = self.session.run_json(LABELS_BODY_JS)
        return [LabelStat(**item) for item in parse_labels(payload["body"])]

    def collect_label_names_from_indexeddb(self) -> list[str]:
        stores = set(self._model_storage_stores())
        if "label" not in stores:
            return []
        labels: list[str] = []
        seen: set[str] = set()
        for row in self._idb_read_all("label"):
            value = row.get("value")
            if not isinstance(value, dict):
                continue
            name = value.get("name")
            if not isinstance(name, str):
                continue
            clean = self._clean_label_name(name)
            if not clean:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            labels.append(clean)
        return sorted(labels, key=str.casefold)

    def collect_chat_list(self) -> list[ChatRow]:
        self._reset_chat_list_to_top()
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

    def _reset_chat_list_to_top(self) -> None:
        try:
            self.session.run_json(CHAT_LIST_RESET_JS)
        except Exception:
            return

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
        include_groups: str = GROUP_INCLUDE_STANDARD,
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
            if not self._include_group_for_policy(
                jid.endswith("@g.us"),
                normalized_thread_labels,
                allowed,
                include_groups,
            ):
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
        include_groups: str = GROUP_INCLUDE_STANDARD,
    ) -> list[NormalizedEvent]:
        events: list[NormalizedEvent] = []
        for thread in self.collect_labeled_threads(
            allow_labels=allow_labels,
            exclude_labels=exclude_labels,
            include_groups=include_groups,
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
        include_groups: str = GROUP_INCLUDE_STANDARD,
    ) -> dict[str, Any]:
        max_messages = self._bounded_max_messages(max_messages)
        excluded_labels = self._effective_excluded_labels(exclude_labels)
        snapshot = self.collect_snapshot()
        threads = self.collect_labeled_threads(
            allow_labels=allow_labels,
            exclude_labels=excluded_labels,
            include_groups=include_groups,
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
        include_groups: str = GROUP_INCLUDE_STANDARD,
        attachments_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        with self._cached_idb_reads():
            return self._collect_dashboard_export(
                account_label=account_label,
                allow_labels=allow_labels,
                exclude_labels=exclude_labels,
                max_messages=max_messages,
                max_all_chats=max_all_chats,
                include_groups=include_groups,
                attachments_dir=attachments_dir,
            )

    def _collect_dashboard_export(
        self,
        *,
        account_label: str = "WhatsApp",
        allow_labels: list[str] | None = None,
        exclude_labels: list[str] | None = None,
        max_messages: int = MAX_MESSAGE_LOOKBACK_HARD_LIMIT,
        max_all_chats: int = DEFAULT_ALL_VIEW_CHAT_LIMIT,
        include_groups: str = GROUP_INCLUDE_STANDARD,
        attachments_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        max_messages = self._bounded_max_messages(max_messages)
        max_all_chats = max(1, int(max_all_chats))
        include_groups = self._normalized_group_policy(include_groups)
        attachments_root = Path(attachments_dir).expanduser() if attachments_dir else None
        excluded_labels = self._effective_excluded_labels(exclude_labels)
        forced_labels = list(allow_labels or [])
        snapshot = self.collect_snapshot()
        export_warnings: list[str] = []
        message_capture_skipped_count = 0
        try:
            threads = (
                self.collect_labeled_threads(
                    allow_labels=forced_labels,
                    exclude_labels=excluded_labels,
                    include_groups=include_groups,
                    max_messages=max_messages,
                    snapshot=snapshot,
                )
                if forced_labels
                else []
            )
        except (RuntimeError, TimeoutError, ValueError) as exc:
            threads = []
            export_warnings.append(f"labeled-thread-export-skipped:{type(exc).__name__}:{exc}")
        exported_threads = []
        for thread in threads:
            clean_labels = [self._clean_label_name(label) for label in thread.labels]
            if not clean_labels:
                continue
            recent_messages = self._serialize_recent_messages(thread.recent_messages)
            source_diagnostics = None
            if thread.visible_in_chat_list:
                recent_messages, source_diagnostics = self._refresh_recent_messages_from_opened_chat(
                    thread.display_name,
                    recent_messages=recent_messages,
                    max_messages=max_messages,
                    attachments_dir=attachments_root,
                    thread_key=thread.jid,
                    expected_latest_timestamp=thread.last_message_timestamp,
                    preview=thread.preview,
                    force=True,
                    return_diagnostics=True,
                )
            last_message_at, last_direction, last_sender, last_text = self._latest_thread_summary(thread)
            if recent_messages:
                latest_message = recent_messages[0]
                last_message_at = latest_message.get("timestamp") or last_message_at
                last_direction = str(latest_message.get("direction") or last_direction)
                last_sender = latest_message.get("sender") or last_sender
                last_text = str(latest_message.get("text") or last_text)
            else:
                message_capture_skipped_count += 1
                continue
            export_thread = {
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
            self._attach_source_diagnostics(export_thread, source_diagnostics)
            exported_threads.append(export_thread)
        try:
            excluded_recent_chat_names = self._excluded_recent_chat_names(excluded_labels)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            excluded_recent_chat_names = set()
            export_warnings.append(f"excluded-label-filter-skipped:{type(exc).__name__}:{exc}")
        refreshed_chat_rows = self.collect_chat_list()
        planned_chat_rows = self._plan_all_view_rows(snapshot.chat_list, refreshed_chat_rows, all_view_chat_limit=max_all_chats)
        exported_threads.extend(
            self._recent_default_view_exports(
                chat_rows=planned_chat_rows,
                existing_titles=[thread["chatTitle"] for thread in exported_threads],
                excluded_titles=excluded_recent_chat_names,
                include_groups=include_groups,
                max_messages=max_messages,
                limit=max_all_chats,
                attachments_dir=attachments_root,
            )
        )
        exported_threads.extend(
            self._recent_indexeddb_chat_exports(
                existing_threads=exported_threads,
                excluded_labels=excluded_labels,
                allow_labels=forced_labels,
                include_groups=include_groups,
                max_messages=max_messages,
                limit=max_all_chats,
                attachments_dir=attachments_root,
            )
        )
        exported_threads.sort(key=self._export_thread_recency_key, reverse=True)
        payload = {
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
            "includeGroups": include_groups,
            "threads": exported_threads,
        }
        if attachments_root:
            payload["attachmentsRoot"] = str(attachments_root)
        if export_warnings:
            if message_capture_skipped_count:
                export_warnings.append(f"message-capture-skipped:{message_capture_skipped_count}")
            payload["exportWarnings"] = export_warnings
        elif message_capture_skipped_count:
            payload["exportWarnings"] = [f"message-capture-skipped:{message_capture_skipped_count}"]
        return payload

    def _model_storage_stores(self) -> list[str]:
        payload = self.session.run_async_json(MODEL_STORAGE_STORES_JS)
        return list(payload["stores"])

    @contextmanager
    def _cached_idb_reads(self):
        previous_cache = self._idb_read_cache
        self._idb_read_cache = {}
        try:
            yield
        finally:
            self._idb_read_cache = previous_cache

    def _cached_idb_rows_if_loaded(self, store_name: str) -> list[dict[str, Any]]:
        if self._idb_read_cache is None:
            return []
        return self._idb_read_cache.get(store_name, [])

    def _idb_read_all(self, store_name: str) -> list[dict[str, Any]]:
        if self._idb_read_cache is not None and store_name in self._idb_read_cache:
            return self._idb_read_cache[store_name]
        quoted_store = store_name.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''window.__hermes_async_result = null;(function(){{const req=indexedDB.open("model-storage");req.onerror=()=>{{window.__hermes_async_result=JSON.stringify({{error:String(req.error)}});}};req.onsuccess=()=>{{const db=req.result;const tx=db.transaction("{quoted_store}","readonly");const os=tx.objectStore("{quoted_store}");const allRecords=[];const cursorReq=os.openCursor();cursorReq.onerror=()=>{{window.__hermes_async_result=JSON.stringify({{error:String(cursorReq.error)}});db.close();}};cursorReq.onsuccess=(event)=>{{const cursor=event.target.result;if(cursor){{allRecords.push({{ key: cursor.key, value: cursor.value }});cursor.continue();}} else {{window.__hermes_async_result=JSON.stringify(allRecords);db.close();}}}};}};}})();"started"'''
        payload = self.session.run_async_json(script)
        if isinstance(payload, dict) and payload.get("error"):
            raise ValueError(f"IndexedDB read failed for {store_name}: {payload['error']}")
        rows = list(payload)
        if self._idb_read_cache is not None:
            self._idb_read_cache[store_name] = rows
        return rows

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

    def _labels_for_jid(
        self,
        association_rows: list[dict[str, Any]],
        labels_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, set[str]]:
        labels_for_jid: dict[str, set[str]] = defaultdict(set)
        for row in association_rows:
            value = row.get("value")
            if not isinstance(value, dict) or value.get("type") != "jid":
                continue
            association_id = value.get("associationId")
            label = labels_by_id.get(value.get("labelId"))
            if not isinstance(association_id, str) or not label:
                continue
            label_name = label.get("name")
            if isinstance(label_name, str) and label_name.strip():
                labels_for_jid[association_id].add(self._clean_label_name(label_name))
        return labels_for_jid

    def _labels_for_thread(self, jid: str, labels_for_jid: dict[str, set[str]]) -> list[str]:
        return sorted({self._clean_label_name(label) for label in labels_for_jid.get(jid, set()) if label})

    def _contact_for_chat(
        self,
        chat: dict[str, Any],
        *,
        contacts_by_id: dict[str, dict[str, Any]],
        contacts_by_alias: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for candidate in [chat.get("id"), chat.get("historyChatId"), chat.get("accountLid")]:
            if isinstance(candidate, str) and candidate in contacts_by_id:
                return contacts_by_id[candidate]
            for alias in self._alias_keys_for_value(candidate):
                match = contacts_by_alias.get(alias)
                if match:
                    return match
        return None

    def _group_for_chat(
        self,
        chat: dict[str, Any],
        *,
        groups_by_id: dict[str, dict[str, Any]],
        groups_by_alias: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for candidate in [chat.get("id"), chat.get("historyChatId"), chat.get("accountLid")]:
            if isinstance(candidate, str) and candidate in groups_by_id:
                return groups_by_id[candidate]
            for alias in self._alias_keys_for_value(candidate):
                match = groups_by_alias.get(alias)
                if match:
                    return match
        return None

    @staticmethod
    def _resolve_chat_display_name(
        jid: str,
        *,
        contact: dict[str, Any] | None,
        group: dict[str, Any] | None,
        chat: dict[str, Any],
    ) -> str:
        contact = contact or {}
        group = group or {}
        return (
            contact.get("name")
            or contact.get("shortName")
            or group.get("subject")
            or contact.get("displayNameLID")
            or contact.get("phoneNumber")
            or chat.get("formattedTitle")
            or chat.get("title")
            or chat.get("name")
            or chat.get("historyChatId")
            or chat.get("accountLid")
            or jid
        )

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
        include_groups: str,
        max_messages: int,
        limit: int = 15,
        attachments_dir: Path | None = None,
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
            is_group_chat = bool(group) or (isinstance(jid, str) and jid.endswith("@g.us"))
            if not self._include_group_for_policy(is_group_chat, set(), set(), include_groups):
                continue
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
            if jid and any(self._message_has_exportable_content(message) for message in candidate_messages):
                recent_messages = self._serialize_recent_messages(
                    self._recent_messages_for_thread(
                        jid=jid,
                        display_name=row.chat_name,
                        phone_or_history_id=phone_or_history_id,
                        messages=candidate_messages,
                        preview=row.preview,
                        max_messages=max_messages,
                        attachments_dir=attachments_dir,
                        thread_key=jid or row.chat_name,
                    )
                )
            recent_messages, source_diagnostics = self._refresh_recent_messages_from_opened_chat(
                row.chat_name,
                recent_messages=recent_messages,
                max_messages=max_messages,
                attachments_dir=attachments_dir,
                thread_key=jid or row.chat_name,
                expected_latest_timestamp=(chat or {}).get("t") if chat else None,
                preview=row.preview,
                force=True,
                return_diagnostics=True,
            )
            if not recent_messages:
                continue

            last_message = recent_messages[0]
            export_thread = {
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
            self._attach_source_diagnostics(export_thread, source_diagnostics)
            recent_exports.append(export_thread)
        return recent_exports

    def _recent_indexeddb_chat_exports(
        self,
        *,
        existing_threads: list[dict[str, Any]],
        excluded_labels: list[str],
        allow_labels: list[str],
        include_groups: str,
        max_messages: int,
        limit: int,
        attachments_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        try:
            label_rows = self._idb_read_all("label")
            association_rows = self._idb_read_all("label-association")
        except (RuntimeError, TimeoutError, ValueError):
            label_rows = []
            association_rows = []
        try:
            contact_rows = self._idb_read_all("contact")
            group_rows = self._idb_read_all("group-metadata")
            chat_rows = self._idb_read_all("chat")
        except (RuntimeError, TimeoutError, ValueError):
            return []

        labels_by_id = {
            row["value"]["id"]: row["value"]
            for row in label_rows
            if isinstance(row.get("value"), dict) and row["value"].get("id")
        }
        normalized_excluded_labels = self._normalized_label_set(excluded_labels)
        normalized_allowed_labels = self._normalized_label_set(allow_labels)
        labels_for_jid = self._labels_for_jid(association_rows, labels_by_id)
        contacts_by_id = {
            row["value"]["id"]: row["value"]
            for row in contact_rows
            if isinstance(row.get("value"), dict) and row["value"].get("id")
        }
        groups_by_id = {
            row["value"]["id"]: row["value"]
            for row in group_rows
            if isinstance(row.get("value"), dict) and row["value"].get("id")
        }
        contacts_by_alias = self._contacts_by_normalized_name(contact_rows)
        groups_by_alias = self._groups_by_normalized_name(group_rows)
        message_rows = self._cached_idb_rows_if_loaded("message")
        messages_by_jid = self._group_messages_by_jid(message_rows)

        seen_thread_keys = {
            str(thread.get("threadKey") or "").strip()
            for thread in existing_threads
            if str(thread.get("threadKey") or "").strip()
        }
        seen_titles = {
            self._normalized_chat_identity(str(thread.get("chatTitle") or ""))
            for thread in existing_threads
            if str(thread.get("chatTitle") or "").strip()
        }
        recent_exports: list[dict[str, Any]] = []
        recent_direct_count = 0
        recent_group_count = 0

        sorted_chat_rows = sorted(
            [
                row
                for row in chat_rows
                if isinstance(row.get("value"), dict) and row["value"].get("id")
            ],
            key=lambda row: ((row["value"].get("t") or 0), str(row["value"].get("id") or "")),
            reverse=True,
        )

        for row in sorted_chat_rows:
            chat = row["value"]
            jid = str(chat.get("id") or "").strip()
            if not jid or jid in seen_thread_keys:
                continue
            is_group_chat = jid.endswith("@g.us")
            if is_group_chat:
                if recent_group_count >= limit:
                    continue
            elif recent_direct_count >= limit:
                continue

            raw_labels = self._labels_for_thread(jid, labels_for_jid)
            normalized_labels = {self._normalize_label_slug(label) for label in raw_labels}
            if self._thread_has_only_excluded_labels(normalized_labels, normalized_excluded_labels):
                continue
            if not self._include_group_for_policy(
                is_group_chat,
                normalized_labels,
                normalized_allowed_labels,
                include_groups,
            ):
                continue

            contact = self._contact_for_chat(chat, contacts_by_id=contacts_by_id, contacts_by_alias=contacts_by_alias)
            group = self._group_for_chat(chat, groups_by_id=groups_by_id, groups_by_alias=groups_by_alias)
            display_name = self._resolve_chat_display_name(jid, contact=contact, group=group, chat=chat)
            normalized_display_name = self._normalized_chat_identity(display_name)
            if not normalized_display_name or normalized_display_name == "you" or normalized_display_name in seen_titles:
                continue

            phone_or_history_id = None
            if contact:
                phone_or_history_id = contact.get("phoneNumber")
            if not phone_or_history_id:
                phone_or_history_id = chat.get("historyChatId") or chat.get("accountLid")

            candidate_messages = self._collect_candidate_messages(
                self._candidate_message_keys_for_chat(chat, contact=contact, group=group),
                messages_by_jid,
            )
            recent_messages = self._serialize_recent_messages(
                self._recent_messages_for_thread(
                    jid=jid,
                    display_name=display_name,
                    phone_or_history_id=phone_or_history_id,
                    messages=candidate_messages,
                    preview="",
                    max_messages=max_messages,
                    attachments_dir=attachments_dir,
                    thread_key=jid,
                )
            )
            source_diagnostics = None
            if not recent_messages:
                try:
                    opened_messages = self._opened_chat_recent_messages_for_chat(
                        display_name,
                        max_messages=max_messages,
                        attachments_dir=attachments_dir,
                        thread_key=jid,
                    )
                    recent_messages = opened_messages
                    source_diagnostics = self._message_source_diagnostics(
                        indexeddb_messages=[],
                        opened_chat_messages=opened_messages,
                        merged_messages=recent_messages,
                        opened_chat_checked=True,
                        max_messages=max_messages,
                    )
                except RuntimeError:
                    recent_messages = []
            latest_raw = self._latest_raw_message(candidate_messages)
            latest_message = recent_messages[0] if recent_messages else None
            last_message_at = (
                (latest_message or {}).get("timestamp")
                or self._format_timestamp((latest_raw or {}).get("t"))
                or self._format_timestamp(chat.get("t"))
            )
            if not last_message_at:
                continue

            if latest_message:
                last_direction = str(latest_message.get("direction") or "")
                last_sender = latest_message.get("sender")
                last_text = str(latest_message.get("text") or "")
            else:
                if not latest_raw:
                    continue
                last_direction = self._message_direction(latest_raw or {}) if latest_raw else "unknown"
                last_sender = (
                    self._message_sender(latest_raw, display_name=display_name, direction=last_direction)
                    if latest_raw
                    else None
                )
                last_text = ""
                if not last_text:
                    continue

            labels_raw = raw_labels or ["Unlabeled"]
            labels_normalized = [self._normalize_label_slug(label) for label in labels_raw]
            unread = int(chat.get("unreadCount") or 0) > 0
            requires_response = unread or bool({"follow-up", "important"} & set(labels_normalized))
            export_thread = {
                "threadKey": jid,
                "chatTitle": display_name,
                "chatType": "group" if is_group_chat else "direct",
                "participants": [{"name": display_name, "phone": phone_or_history_id}],
                "labelsRaw": labels_raw,
                "labelsNormalized": labels_normalized,
                "unread": unread,
                "starred": False,
                "requiresResponse": requires_response,
                "lastMessageAt": last_message_at,
                "lastMessageDirection": last_direction,
                "lastMessageSender": last_sender,
                "lastMessageText": last_text,
                "sourceView": "indexeddb-recent",
                "recentMessages": recent_messages,
                "messages": [dict(message) for message in recent_messages],
            }
            self._attach_source_diagnostics(export_thread, source_diagnostics)
            recent_exports.append(export_thread)
            if is_group_chat:
                recent_group_count += 1
            else:
                recent_direct_count += 1
            seen_thread_keys.add(jid)
            seen_titles.add(normalized_display_name)
            if recent_direct_count >= limit and recent_group_count >= limit:
                break
        return recent_exports

    def _refresh_recent_messages_from_opened_chat(
        self,
        chat_name: str,
        *,
        recent_messages: list[dict[str, Any]],
        max_messages: int,
        attachments_dir: Path | None,
        thread_key: str,
        expected_latest_timestamp: Any = None,
        preview: str = "",
        force: bool = False,
        return_diagnostics: bool = False,
    ) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if not force and not self._should_refresh_opened_chat_messages(
            recent_messages,
            expected_latest_timestamp=expected_latest_timestamp,
            preview=preview,
        ):
            return (recent_messages, None) if return_diagnostics else recent_messages
        try:
            opened_messages = self._opened_chat_recent_messages_for_chat(
                chat_name,
                max_messages=max_messages,
                attachments_dir=attachments_dir,
                thread_key=thread_key,
            )
        except RuntimeError as exc:
            diagnostic = self._message_source_diagnostics(
                indexeddb_messages=recent_messages,
                opened_chat_messages=[],
                merged_messages=recent_messages,
                opened_chat_checked=True,
                opened_chat_error=str(exc),
                max_messages=max_messages,
            )
            return (recent_messages, diagnostic) if return_diagnostics else recent_messages
        if not opened_messages:
            diagnostic = self._message_source_diagnostics(
                indexeddb_messages=recent_messages,
                opened_chat_messages=[],
                merged_messages=recent_messages,
                opened_chat_checked=True,
                max_messages=max_messages,
            )
            return (recent_messages, diagnostic) if return_diagnostics else recent_messages
        merged_messages = self._merge_recent_message_exports(opened_messages, recent_messages, max_messages=max_messages)
        diagnostic = self._message_source_diagnostics(
            indexeddb_messages=recent_messages,
            opened_chat_messages=opened_messages,
            merged_messages=merged_messages,
            opened_chat_checked=True,
            max_messages=max_messages,
        )
        return (merged_messages, diagnostic) if return_diagnostics else merged_messages

    @classmethod
    def _attach_source_diagnostics(cls, export_thread: dict[str, Any], diagnostics: dict[str, Any] | None) -> None:
        if diagnostics and diagnostics.get("issues"):
            export_thread["sourceDiagnostics"] = diagnostics

    @classmethod
    def _message_source_diagnostics(
        cls,
        *,
        indexeddb_messages: list[dict[str, Any]],
        opened_chat_messages: list[dict[str, Any]],
        merged_messages: list[dict[str, Any]],
        opened_chat_checked: bool,
        max_messages: int,
        opened_chat_error: str | None = None,
    ) -> dict[str, Any] | None:
        issues: list[dict[str, Any]] = []
        latest_indexeddb = cls._latest_exported_message(indexeddb_messages)
        latest_opened = cls._latest_exported_message(opened_chat_messages)
        latest_indexeddb_epoch = cls._exported_message_epoch(latest_indexeddb or {})
        latest_opened_epoch = cls._exported_message_epoch(latest_opened or {})

        if opened_chat_error:
            if "requires a DevTools-backed Chrome session" not in opened_chat_error:
                issues.append({"code": "opened-chat-check-failed", "detail": opened_chat_error[:240]})
        elif opened_chat_checked and not opened_chat_messages and not indexeddb_messages:
            issues.append({"code": "no-message-source-produced-exportable-content"})

        if latest_opened_epoch is not None and (latest_indexeddb_epoch is None or latest_opened_epoch > latest_indexeddb_epoch + 1):
            issues.append(
                {
                    "code": "opened-chat-newer-than-indexeddb",
                    "openedChatLatestAt": latest_opened.get("timestamp") if latest_opened else None,
                    "indexedDbLatestAt": latest_indexeddb.get("timestamp") if latest_indexeddb else None,
                }
            )
        if latest_indexeddb_epoch is not None and latest_opened_epoch is not None and latest_indexeddb_epoch > latest_opened_epoch + 1:
            issues.append(
                {
                    "code": "indexeddb-newer-than-opened-chat",
                    "indexedDbLatestAt": latest_indexeddb.get("timestamp") if latest_indexeddb else None,
                    "openedChatLatestAt": latest_opened.get("timestamp") if latest_opened else None,
                }
            )

        issues.extend(cls._matching_message_conflicts(indexeddb_messages, opened_chat_messages))

        if not issues:
            return None
        sources: list[str] = []
        if indexeddb_messages:
            sources.append("indexeddb")
        if opened_chat_messages:
            sources.append("opened-chat")
        return {
            "openedChatChecked": opened_chat_checked,
            "sourcesUsed": sources,
            "indexedDbMessageCount": len(indexeddb_messages),
            "openedChatMessageCount": len(opened_chat_messages),
            "mergedMessageCount": len(merged_messages),
            "maxMessages": max_messages,
            "issues": issues,
        }

    @classmethod
    def _latest_exported_message(cls, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        ordered = sorted(messages, key=cls._exported_message_epoch_for_sort, reverse=True)
        return ordered[0] if ordered else None

    @classmethod
    def _matching_message_conflicts(
        cls,
        indexeddb_messages: list[dict[str, Any]],
        opened_chat_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        indexed_by_id = {str(message.get("messageId") or ""): message for message in indexeddb_messages if message.get("messageId")}
        opened_by_id = {str(message.get("messageId") or ""): message for message in opened_chat_messages if message.get("messageId")}
        for message_id in sorted(set(indexed_by_id) & set(opened_by_id)):
            indexed = indexed_by_id[message_id]
            opened = opened_by_id[message_id]
            indexed_epoch = cls._exported_message_epoch(indexed)
            opened_epoch = cls._exported_message_epoch(opened)
            if indexed_epoch is not None and opened_epoch is not None and abs(indexed_epoch - opened_epoch) > 1:
                issues.append(
                    {
                        "code": "matching-message-timestamp-conflict",
                        "messageId": message_id,
                        "indexedDbAt": indexed.get("timestamp"),
                        "openedChatAt": opened.get("timestamp"),
                    }
                )
            indexed_text_available = bool(str(indexed.get("text") or "").strip())
            opened_text_available = bool(str(opened.get("text") or "").strip())
            if indexed_text_available != opened_text_available:
                issues.append(
                    {
                        "code": "matching-message-text-availability-conflict",
                        "messageId": message_id,
                        "indexedDbTextAvailable": indexed_text_available,
                        "openedChatTextAvailable": opened_text_available,
                    }
                )
        return issues

    @classmethod
    def _should_refresh_opened_chat_messages(
        cls,
        recent_messages: list[dict[str, Any]],
        *,
        expected_latest_timestamp: Any = None,
        preview: str = "",
    ) -> bool:
        if not recent_messages:
            return True
        expected_epoch = cls._whatsapp_timestamp_epoch(expected_latest_timestamp)
        latest_epoch = cls._exported_message_epoch(recent_messages[0])
        if expected_epoch is not None and latest_epoch is not None and expected_epoch > latest_epoch + 1:
            return True
        preview_text = str(preview or "").strip()
        latest_text = str(recent_messages[0].get("text") or "").strip()
        if preview_text and not latest_text:
            return True
        return False

    @classmethod
    def _merge_recent_message_exports(
        cls,
        preferred_messages: list[dict[str, Any]],
        fallback_messages: list[dict[str, Any]],
        *,
        max_messages: int,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        anonymous_index = 0
        for message in [*fallback_messages, *preferred_messages]:
            key = str(message.get("messageId") or "").strip()
            if not key:
                anonymous_index += 1
                key = f"anonymous:{anonymous_index}:{message.get('timestamp') or ''}:{message.get('sender') or ''}"
            existing = merged.get(key, {})
            merged[key] = cls._merge_recent_message_dicts(existing, message)
        return sorted(merged.values(), key=cls._exported_message_epoch_for_sort, reverse=True)[:max_messages]

    @staticmethod
    def _merge_recent_message_dicts(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        if not existing:
            return dict(incoming)
        merged = dict(existing)
        for key, value in incoming.items():
            if value is None or value == "":
                continue
            if key == "attachments" and merged.get("attachments") and not value:
                continue
            merged[key] = value
        return merged

    @classmethod
    def _exported_message_epoch_for_sort(cls, message: dict[str, Any]) -> float:
        return cls._exported_message_epoch(message) or 0.0

    @classmethod
    def _exported_message_epoch(cls, message: dict[str, Any]) -> float | None:
        return cls._iso_timestamp_epoch(message.get("timestamp"))

    @classmethod
    def _whatsapp_timestamp_epoch(cls, value: Any) -> float | None:
        if value is None:
            return None
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return cls._iso_timestamp_epoch(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return timestamp

    @staticmethod
    def _iso_timestamp_epoch(value: Any) -> float | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    def _serialize_recent_messages(self, messages: list[RecentMessage]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for message in messages:
            item = {
                "messageId": message.message_id,
                "timestamp": message.iso_timestamp,
                "direction": message.direction,
                "sender": message.sender,
                "text": message.text,
                "textAvailable": message.text_available,
                "messageType": message.message_type,
                "subtype": message.subtype,
            }
            if message.attachments:
                item["attachments"] = [self._serialize_attachment(attachment) for attachment in message.attachments]
            serialized.append(item)
        return serialized

    @staticmethod
    def _serialize_attachment(attachment: RecentAttachment) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attachmentId": attachment.attachment_id,
            "kind": attachment.kind,
            "mimeType": attachment.mime_type,
            "fileName": attachment.file_name,
            "sizeBytes": attachment.size_bytes,
            "status": attachment.status,
        }
        if attachment.relative_path:
            payload["relativePath"] = attachment.relative_path
        if attachment.local_path:
            payload["localPath"] = attachment.local_path
        if attachment.skipped_reason:
            payload["skippedReason"] = attachment.skipped_reason
        if attachment.note:
            payload["note"] = attachment.note
        return payload

    def _opened_chat_recent_messages_for_chat(
        self,
        chat_name: str,
        *,
        max_messages: int,
        attachments_dir: Path | None = None,
        thread_key: str | None = None,
    ) -> list[dict[str, Any]]:
        if not hasattr(self.session, "click_point"):
            raise RuntimeError("Opened-chat message capture requires a DevTools-backed Chrome session")
        self._reset_chat_list_to_top()
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
            attachments = self._message_attachments(
                item,
                attachments_dir=attachments_dir,
                thread_key=thread_key or chat_name,
                message_id=message_id,
            )
            if not text and not attachments:
                continue
            direction = self._message_direction(item)
            sender = self._message_sender(item, display_name=chat_name, direction=direction)
            opened_message = {
                "messageId": message_id,
                "timestamp": self._format_timestamp(item.get("t")),
                "direction": direction,
                "sender": sender,
                "text": text,
                "textAvailable": bool(text),
                "messageType": item.get("type") or "unknown",
                "subtype": item.get("subtype"),
            }
            if attachments:
                opened_message["attachments"] = [self._serialize_attachment(attachment) for attachment in attachments]
            opened_messages.append(opened_message)
        return opened_messages

    @staticmethod
    def _chat_row_click_point_expression(chat_name: str) -> str:
        return f'''(()=>{{const targetTitle={json.dumps(chat_name)}; const titleEl=[...document.querySelectorAll('#pane-side [data-testid="cell-frame-title"] [title], #pane-side [data-testid^="list-item-"] [title]')].find(el => ((el.getAttribute('title')||'').trim()===targetTitle)); if(!titleEl) return null; const clickable=titleEl.closest('[data-testid^="list-item-"]')||titleEl.closest('[data-testid="cell-frame-container"]')||titleEl.closest('[role="row"]')||titleEl; clickable.scrollIntoView({{block:'center'}}); const rect=clickable.getBoundingClientRect(); return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2}};}})()'''

    @staticmethod
    def _opened_chat_recent_messages_js(*, max_messages: int) -> str:
        return f'''(async () => {{
            const maxMessages = {int(max_messages)};
            const maxVideoBytes = {MAX_AUTOMATIC_VIDEO_ATTACHMENT_BYTES};
            const maxPasses = Math.max(4, Math.min(40, Math.ceil(maxMessages / 8) + 6));
            const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            await wait(1200);
            const titleCandidates = [...document.querySelectorAll('header [title], #main header [title], #main header span[dir="auto"], #main header div[dir="auto"]')]
                .map(el => (el.getAttribute('title') || (el.textContent || '').trim()))
                .filter(Boolean);
            const openedChatTitle = titleCandidates.find(title => !['Profile details', 'click here for contact info'].includes(title)) || '';
            const normalizeJid = (value) => {{
                if (!value) return null;
                if (typeof value === 'string') return value;
                if (typeof value === 'object' && value._serialized) return value._serialized;
                return null;
            }};
            const bestFileName = (msg, kind, index) => {{
                const raw = msg && typeof msg === 'object' ? (msg.filename || msg.fileName || msg.documentTitle || msg.title) : null;
                if (typeof raw === 'string' && raw.trim()) return raw.trim();
                const ext = kind === 'image' ? '.jpg' : kind === 'video' ? '.mp4' : kind === 'document' ? '.bin' : '';
                return `attachment-${{index + 1}}${{ext}}`;
            }};
            const blobToDataURL = (blob) => new Promise((resolve, reject) => {{
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = () => reject(reader.error);
                reader.readAsDataURL(blob);
            }});
            const fetchAttachment = async (src, kind) => {{
                if (!src || (!src.startsWith('blob:') && !src.startsWith('data:'))) return {{status: 'notDownloaded', skippedReason: 'not-fetchable-from-dom'}};
                try {{
                    const blob = await (await fetch(src)).blob();
                    if (kind === 'video' && blob.size > maxVideoBytes) return {{status: 'notDownloaded', sizeBytes: blob.size, skippedReason: 'video-over-10mb'}};
                    return {{status: 'downloadable', sizeBytes: blob.size, mimeType: blob.type || null, dataUrl: await blobToDataURL(blob)}};
                }} catch (error) {{
                    return {{status: 'notDownloaded', skippedReason: 'browser-fetch-failed', note: String(error)}};
                }}
            }};
            const domAttachments = async (container, msg) => {{
                const nodes = [...container.querySelectorAll('img[src], video[src], a[href^="blob:"], a[href^="data:"]')];
                const items = [];
                for (let index = 0; index < nodes.length; index += 1) {{
                    const node = nodes[index];
                    const src = node.getAttribute('src') || node.getAttribute('href') || '';
                    const tag = node.tagName.toLowerCase();
                    const msgType = (msg && msg.type) || '';
                    const kind = tag === 'video' ? 'video' : (msgType === 'document' || tag === 'a' ? 'document' : 'image');
                    const fetched = await fetchAttachment(src, kind);
                    items.push({{
                        kind,
                        mimeType: fetched.mimeType || (node.getAttribute('type') || null),
                        fileName: node.getAttribute('download') || bestFileName(msg, kind, index),
                        sizeBytes: fetched.sizeBytes ?? null,
                        status: fetched.status,
                        skippedReason: fetched.skippedReason || null,
                        note: fetched.note || null,
                        dataUrl: fetched.dataUrl || null
                    }});
                }}
                return items;
            }};
            const summarizeMsg = async (msg, container) => {{
                if (!msg || typeof msg !== 'object') return null;
                const attachments = await domAttachments(container, msg);
                return {{
                    id: typeof msg.id === 'string' ? msg.id : (msg.id && msg.id._serialized) || null,
                    t: msg.t ?? null,
                    type: msg.type || null,
                    subtype: msg.subtype || null,
                    body: typeof msg.body === 'string' ? msg.body : null,
                    caption: typeof msg.caption === 'string' ? msg.caption : null,
                    text: typeof msg.text === 'string' ? msg.text : null,
                    matchedText: typeof msg.matchedText === 'string' ? msg.matchedText : null,
                    mimetype: typeof msg.mimetype === 'string' ? msg.mimetype : null,
                    fileName: typeof msg.fileName === 'string' ? msg.fileName : (typeof msg.filename === 'string' ? msg.filename : null),
                    size: Number.isFinite(Number(msg.size || msg.fileSize)) ? Number(msg.size || msg.fileSize) : null,
                    from: normalizeJid(msg.from),
                    to: normalizeJid(msg.to),
                    notifyName: typeof msg.notifyName === 'string' ? msg.notifyName : null,
                    attachments
                }};
            }};
            const visibleMessages = async () => {{
                const containers = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
                const messages = await Promise.all(containers.map(async (container) => {{
                    const fiberKey = Object.keys(container).find(k => k.startsWith('__reactFiber$'));
                    let fiber = fiberKey ? container[fiberKey] : null;
                    while (fiber) {{
                        const props = fiber.memoizedProps;
                        if (props && typeof props === 'object' && props.msg) {{
                            return await summarizeMsg(props.msg, container);
                        }}
                        fiber = fiber.return;
                    }}
                    return null;
                }}));
                return messages.filter(item => item && item.id);
            }};
            const scroller = () => document.querySelector('#main [data-testid="conversation-panel-messages"]')
                || [...document.querySelectorAll('#main *')].find(el => el.scrollHeight > el.clientHeight + 40)
                || null;
            const initialPanel = scroller();
            if (initialPanel) {{
                initialPanel.scrollTop = initialPanel.scrollHeight;
                await wait(700);
            }}
            const seen = new Map();
            for (let pass = 0; pass < maxPasses; pass += 1) {{
                for (const message of await visibleMessages()) {{
                    const key = String(message.id || '');
                    if (!key) continue;
                    seen.set(key, Object.assign(seen.get(key) || {{}}, message));
                }}
                if (seen.size >= maxMessages) break;
                const panel = scroller();
                if (!panel) break;
                const beforeTop = panel.scrollTop;
                const beforeHeight = panel.scrollHeight;
                panel.scrollTop = Math.max(0, beforeTop - Math.max(420, panel.clientHeight * 0.9));
                await wait(700);
                if (Math.abs(panel.scrollTop - beforeTop) < 2 && panel.scrollHeight === beforeHeight) break;
            }}
            const messages = [...seen.values()]
                .filter(item => item && item.id)
                .sort((a, b) => (Number(b.t) || 0) - (Number(a.t) || 0))
                .slice(0, maxMessages);
            return JSON.stringify({{OPENED_CHAT_RECENT_MESSAGES: true, openedChatTitle, messages}});
        }})()'''

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

    @classmethod
    def _candidate_message_keys_for_chat(
        cls,
        chat: dict[str, Any],
        *,
        contact: dict[str, Any] | None,
        group: dict[str, Any] | None,
    ) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()
        for value in [contact or {}, group or {}, chat]:
            for key in ["id", "phoneNumber", "historyChatId", "accountLid", "name", "shortName", "displayNameLID", "subject", "formattedTitle", "title"]:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip() and candidate not in seen:
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

    @staticmethod
    def _latest_raw_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        ordered = sorted(messages, key=lambda item: item.get("t") or 0, reverse=True)
        return ordered[0] if ordered else None

    def _default_view_lookup_maps(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        try:
            contact_rows = self._idb_read_all("contact")
            group_rows = self._idb_read_all("group-metadata")
            chat_rows = self._idb_read_all("chat")
            message_rows = self._idb_read_all("message")
        except (RuntimeError, TimeoutError, ValueError):
            return {}, {}, {}, {}, {}
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
    def _normalized_group_policy(include_groups: str | None) -> str:
        normalized = str(include_groups or "").strip()
        if normalized in {
            GROUP_INCLUDE_LABELED_ALWAYS,
            "labeled-always",
            "always-labeled",
            "alwaysIncludeOnly",
            "allow-labeled-only",
        }:
            return GROUP_INCLUDE_LABELED_ALWAYS
        return GROUP_INCLUDE_STANDARD

    @classmethod
    def _include_group_for_policy(
        cls,
        is_group_chat: bool,
        thread_labels: set[str],
        allowed_labels: set[str],
        include_groups: str | None,
    ) -> bool:
        if not is_group_chat:
            return True
        if cls._normalized_group_policy(include_groups) == GROUP_INCLUDE_STANDARD:
            return True
        return bool(allowed_labels) and cls._label_set_matches(thread_labels, allowed_labels)

    @staticmethod
    def _export_thread_recency_key(thread: dict[str, Any]) -> tuple[float, str]:
        raw_date = thread.get("lastMessageAt")
        timestamp = 0.0
        if isinstance(raw_date, str) and raw_date.strip():
            try:
                timestamp = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).timestamp()
            except ValueError:
                timestamp = 0.0
        return (timestamp, str(thread.get("chatTitle") or ""))

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
        attachments_dir: Path | None = None,
        thread_key: str | None = None,
    ) -> list[RecentMessage]:
        sorted_messages = sorted(messages, key=lambda item: item.get("t") or 0, reverse=True)
        recent_messages: list[RecentMessage] = []
        for message in sorted_messages:
            message_id = message.get("id") or f"{jid}:{message.get('t') or 0}"
            message_text = self._extract_message_text(message)
            attachments = self._message_attachments(
                message,
                attachments_dir=attachments_dir,
                thread_key=thread_key or jid,
                message_id=str(message_id),
            )
            if not message_text and not attachments:
                continue
            direction = self._message_direction(message)
            sender = self._message_sender(message, display_name=display_name, direction=direction)
            recent_messages.append(
                RecentMessage(
                    message_id=str(message_id),
                    timestamp=message.get("t"),
                    iso_timestamp=self._format_timestamp(message.get("t")),
                    direction=direction,
                    sender=sender,
                    text=message_text,
                    text_available=bool(message_text),
                    message_type=message.get("type") or "unknown",
                    subtype=message.get("subtype"),
                    attachments=attachments,
                )
            )
            if len(recent_messages) >= max_messages:
                break
        return recent_messages

    def _message_attachments(
        self,
        message: dict[str, Any],
        *,
        attachments_dir: Path | None,
        thread_key: str,
        message_id: str,
    ) -> list[RecentAttachment]:
        candidates = self._attachment_candidates(message)
        attachments: list[RecentAttachment] = []
        for index, candidate in enumerate(candidates):
            attachments.append(
                self._attachment_from_candidate(
                    candidate,
                    attachments_dir=attachments_dir,
                    thread_key=thread_key,
                    message_id=message_id,
                    index=index,
                )
            )
        return attachments

    def _message_has_exportable_content(self, message: dict[str, Any]) -> bool:
        return bool(self._extract_message_text(message) or self._attachment_candidates(message))

    def _attachment_candidates(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        explicit = message.get("attachments")
        if isinstance(explicit, list) and explicit:
            return [item for item in explicit if isinstance(item, dict)]

        message_type = str(message.get("type") or "").strip().lower()
        mimetype_value = self._first_string(message, ["mimetype", "mimeType", "mime"])
        file_name = self._first_string(
            message,
            ["fileName", "filename", "documentTitle", "title"],
        )
        size_bytes = self._first_int(message, ["size", "fileSize", "sizeBytes"])
        data_url = self._first_string(message, ["dataUrl", "dataURL"])
        if message_type not in ATTACHMENT_MESSAGE_TYPES and not mimetype_value and not file_name:
            return []
        if message_type in ATTACHMENT_MESSAGE_TYPES and not any([mimetype_value, file_name, size_bytes, data_url]):
            return []

        return [
            {
                "kind": self._attachment_kind(message_type, mimetype_value, file_name),
                "mimeType": mimetype_value,
                "fileName": file_name,
                "sizeBytes": size_bytes,
                "dataUrl": data_url,
            }
        ]

    def _attachment_from_candidate(
        self,
        candidate: dict[str, Any],
        *,
        attachments_dir: Path | None,
        thread_key: str,
        message_id: str,
        index: int,
    ) -> RecentAttachment:
        kind = self._attachment_kind(
            str(candidate.get("kind") or candidate.get("type") or "").strip().lower(),
            self._first_string(candidate, ["mimeType", "mimetype", "mime"]),
            self._first_string(candidate, ["fileName", "filename", "name"]),
        )
        mime_type = self._first_string(candidate, ["mimeType", "mimetype", "mime"])
        size_bytes = self._first_int(candidate, ["sizeBytes", "size", "fileSize"])
        file_name = self._safe_attachment_filename(
            self._first_string(candidate, ["fileName", "filename", "name"]),
            kind=kind,
            mime_type=mime_type,
            index=index,
        )
        attachment_id = self._stable_attachment_id(message_id=message_id, kind=kind, file_name=file_name, index=index)

        data_url = self._first_string(candidate, ["dataUrl", "dataURL"])
        if kind == "video" and size_bytes is not None and size_bytes > MAX_AUTOMATIC_VIDEO_ATTACHMENT_BYTES:
            return RecentAttachment(
                attachment_id=attachment_id,
                kind=kind,
                mime_type=mime_type,
                file_name=file_name,
                size_bytes=size_bytes,
                status="notDownloaded",
                skipped_reason="video-over-10mb",
                note="Video was not downloaded automatically because it is larger than 10 MB; the user can view it in WhatsApp.",
            )

        if attachments_dir and data_url:
            materialized = self._write_data_url_attachment(
                data_url,
                attachments_dir=attachments_dir,
                thread_key=thread_key,
                message_id=message_id,
                file_name=file_name,
            )
            if materialized:
                path, relative_path, actual_mime, actual_size = materialized
                return RecentAttachment(
                    attachment_id=attachment_id,
                    kind=kind,
                    mime_type=mime_type or actual_mime,
                    file_name=file_name,
                    size_bytes=size_bytes or actual_size,
                    status="downloaded",
                    relative_path=relative_path,
                    local_path=str(path),
                )

        skipped_reason = self._first_string(candidate, ["skippedReason"]) or "download-not-available"
        status = "notDownloaded"
        note = self._first_string(candidate, ["note"])
        if kind in {"image", "document"}:
            note = note or "Attachment metadata was found, but WhatsApp Web did not expose downloadable bytes during this export."
        elif kind == "video":
            note = note or "Video metadata was found, but the video was not downloadable during this export."
        return RecentAttachment(
            attachment_id=attachment_id,
            kind=kind,
            mime_type=mime_type,
            file_name=file_name,
            size_bytes=size_bytes,
            status=status,
            skipped_reason=skipped_reason,
            note=note,
        )

    @staticmethod
    def _attachment_kind(raw_kind: str, mime_type: str | None, file_name: str | None) -> str:
        kind = (raw_kind or "").lower()
        mime = (mime_type or "").lower()
        if kind in {"image", "document", "video", "audio", "sticker"}:
            return kind
        if kind == "ptt":
            return "audio"
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
        if file_name:
            guessed, _ = mimetypes.guess_type(file_name)
            return WhatsAppCollector._attachment_kind("", guessed, None)
        return "document"

    @staticmethod
    def _stable_attachment_id(*, message_id: str, kind: str, file_name: str, index: int) -> str:
        digest = hashlib.sha256(f"{message_id}:{kind}:{file_name}:{index}".encode("utf-8")).hexdigest()[:16]
        return f"att_{digest}"

    @staticmethod
    def _safe_path_component(value: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "").strip(".-")
        return clean[:120] or "unknown"

    @classmethod
    def _safe_attachment_filename(cls, value: str | None, *, kind: str, mime_type: str | None, index: int) -> str:
        raw = (value or "").strip()
        if not raw:
            extension = mimetypes.guess_extension(mime_type or "") or {
                "image": ".jpg",
                "video": ".mp4",
                "audio": ".m4a",
                "document": ".bin",
                "sticker": ".webp",
            }.get(kind, ".bin")
            raw = f"attachment-{index + 1}{extension}"
        name = cls._safe_path_component(raw)
        suffix = Path(name).suffix
        if not suffix:
            guessed = mimetypes.guess_extension(mime_type or "")
            if guessed:
                name = f"{name}{guessed}"
        return name

    @classmethod
    def _write_data_url_attachment(
        cls,
        data_url: str,
        *,
        attachments_dir: Path,
        thread_key: str,
        message_id: str,
        file_name: str,
    ) -> tuple[Path, str, str | None, int] | None:
        match = re.match(r"^data:(?P<mime>[^;,]+)?(?:;charset=[^;,]+)?;base64,(?P<data>.+)$", data_url, re.DOTALL)
        if not match:
            return None
        try:
            data = base64.b64decode(match.group("data"), validate=True)
        except ValueError:
            return None
        message_dir = attachments_dir / cls._safe_path_component(thread_key) / cls._safe_path_component(message_id)
        message_dir.mkdir(parents=True, exist_ok=True)
        output_path = message_dir / file_name
        output_path.write_bytes(data)
        relative_path = str(output_path.relative_to(attachments_dir.parent))
        return output_path, relative_path, match.group("mime"), len(data)

    @staticmethod
    def _first_string(source: dict[str, Any], keys: list[str]) -> str | None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_int(source: dict[str, Any], keys: list[str]) -> int | None:
        for key in keys:
            value = source.get(key)
            if value is None or value == "":
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _extract_message_text(message: dict[str, Any]) -> str | None:
        candidates = [
            message.get("body"),
            message.get("caption"),
            message.get("text"),
            message.get("matchedText"),
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip() and not WhatsAppCollector._looks_like_inline_media_payload(candidate):
                return candidate.strip()
        return None

    @staticmethod
    def _looks_like_inline_media_payload(value: str) -> bool:
        stripped = value.strip()
        if stripped.startswith("data:image/") or stripped.startswith("data:video/") or stripped.startswith("data:application/"):
            return True
        if len(stripped) < 160:
            return False
        if stripped.startswith(("/9j/", "iVBORw0KGgo", "R0lGOD", "UklGR", "AAAAIGZ0eXB", "JVBERi0")):
            return True
        compact = re.sub(r"\s+", "", stripped)
        if len(compact) < 160 or len(compact) % 4 != 0:
            return False
        if re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", compact) is None:
            return False
        try:
            decoded = base64.b64decode(compact[:512], validate=True)
        except Exception:
            return False
        return decoded.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"%PDF", b"\x00\x00\x00"))

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
