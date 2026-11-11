from __future__ import annotations

import base64
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from whatsapp_collector.attachment_store import (
    AttachmentPolicy,
    AttachmentStore,
    DEFAULT_MAX_ATTACHMENT_FILE_BYTES,
    DEFAULT_MAX_ATTACHMENT_THREAD_BYTES,
    DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES,
    StoreResult,
)
from whatsapp_collector.attachment_retention import AttachmentStorageError
from whatsapp_collector.chrome_session import ChromeWhatsAppSession
from whatsapp_collector.devtools_bridge import CHAT_IDENTITY_HELPERS_JS, MEDIA_MESSAGE_HELPERS_JS
from whatsapp_collector.export_quality import attach_export_quality
from whatsapp_collector.message_expiry import (
    MESSAGE_EXPIRY_HELPERS_JS, complete_live_capture, confirmed_empty, epoch, expected_expired, expiry_metadata,
)
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
LABELS_BODY_JS = '''JSON.stringify({LABELS_BODY:true,body:(()=>{const isLabels=(el)=>{const aria=(el.getAttribute('aria-label')||'').trim();const text=(el.innerText||'').trim();return ['Labels','Lists'].includes(aria)||['Labels','Lists'].some(name=>text.startsWith(name));};const close=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Close');if(close){close.click();}const directLabels=document.getElementById("labels-filter")||document.querySelector('[data-testid="biz-tools-labels"]')||[...document.querySelectorAll('button,[role="button"],a')].find(isLabels);if(directLabels){directLabels.click();return document.body.innerText;}const menu=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Menu');if(menu){menu.click();const menuLabels=[...document.querySelectorAll('button,[role="button"],a')].find(isLabels);if(menuLabels){menuLabels.click();return document.body.innerText;}}const tools=[...document.querySelectorAll('button,[role="button"]')].find(el=>el.getAttribute('aria-label')==='Tools');if(tools){tools.click();const toolLabels=document.querySelector('[data-testid="biz-tools-labels"]')||[...document.querySelectorAll('button,[role="button"],a')].find(isLabels);if(toolLabels){toolLabels.click();}}return document.body.innerText;})()})'''
CHAT_LIST_RESET_JS = r'''(async () => {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const controls = () => [...document.querySelectorAll('button,[role="button"],a')];
    const chats = controls().find(el => el.getAttribute('aria-label') === 'Chats');
    const clickedChats = Boolean(chats && chats.getAttribute('aria-pressed') !== 'true');
    if (clickedChats) { chats.click(); await sleep(350); }
    for (let i = 0; i < 3; i++) {
        const close = [...document.querySelectorAll('button,[role="button"]')]
            .find(el => ['Close', 'Back'].includes(el.getAttribute('aria-label')));
        if (!close) break;
        close.click();
        await sleep(250);
    }
    const all = document.getElementById('all-filter') || controls().find(el => (el.innerText || '').trim() === 'All');
    if (all && all.getAttribute('aria-selected') !== 'true') { all.click(); await sleep(250); }
    const pane = document.querySelector('#pane-side');
    if (pane) { pane.scrollTop = 0; pane.dispatchEvent(new Event('scroll', {bubbles: true})); }
    return JSON.stringify({CHAT_LIST_RESET: true, ok: true, clickedChats});
})()'''
CHAT_LIST_BODY_JS = r'''(async () => {
    const requestedHorizon = 15;
    const deadlineMs = Math.min(120000, 8000 + Math.max(0, requestedHorizon - 15) * 200);
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const clean = value => String(value || '').replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '').replace(/\u00a0/g, ' ').trim();
    const timestampPattern = /^(Today|Yesterday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{1,2}:\d{2}(?:\s?[AP]M)?|\d{1,2}\/\d{1,2}\/\d{2,4})$/i;
    const all = document.getElementById("all-filter") || [...document.querySelectorAll('#side [role="tab"], #side button')].find(el => clean(el.innerText) === 'All');
    if (all && all.getAttribute('aria-selected') !== 'true') all.click();
    const found = new Map();
    const skippedIndices = new Set();
    const rowIndex = row => {
        const ariaIndex = Number(row.getAttribute('aria-rowindex'));
        return ariaIndex > 0 ? ariaIndex - 1 : null;
    };
    // list-item-N is a recycled slot, not a stable position in the All list.
    const rowPosition = row => {
        const match = (row.getAttribute('style') || '').match(/translateY\(([\d.]+)px\)/);
        return match ? Number(match[1]) : null;
    };
    let lastSignature = '', stableSince = performance.now(), lastPane = null, anchored = false;
    let lastState = {};
    const started = performance.now();
    for (let pass = 0; pass < Math.ceil(deadlineMs / 100) && performance.now() - started < deadlineMs; pass++) {
        const pane = document.querySelector('#pane-side') || document.querySelector('#side [data-testid="chat-list"]');
        if (!pane) { await wait(100); continue; }
        if (pane !== lastPane) {
            lastPane = pane; found.clear(); skippedIndices.clear(); anchored = false;
            pane.scrollTop = 0; pane.dispatchEvent(new Event('scroll', {bubbles:true}));
            lastSignature = ''; stableSince = performance.now();
        }
        const selected = all && all.getAttribute('aria-selected') === 'true';
        const loading = Boolean(pane.querySelector('[role="progressbar"], [aria-busy="true"], [data-testid*="loading"], [data-testid*="spinner"]'));
        const candidates = [...pane.querySelectorAll('[role="row"], [data-testid^="list-item-"], [data-testid="cell-frame-container"]')];
        const nodes = candidates.filter(node => !candidates.some(parent => parent !== node && parent.contains(node)));
        const parsed = nodes.map((row, ordinal) => {
            const lines = clean(row.innerText).split(/\r?\n/).map(clean).filter(Boolean);
            const title = row.querySelector('span[title]') || row.querySelector('[data-testid="cell-frame-title"]');
            const name = clean(title?.getAttribute('title') || title?.innerText || title?.textContent).replace(/^\d+ unread messages?\s*/i, '') || lines.find(line => !/^(\d+ unread messages?|unread)$/i.test(line));
            const timeNode = row.querySelector('[data-testid="chat-list-timestamp"], [data-testid="cell-frame-primary-detail"]');
            const timestamp = clean(timeNode?.innerText || timeNode?.textContent) || lines.find((line, i) => i > 0 && timestampPattern.test(line));
            if (!name || !timestamp || ['Locked chats', 'Archived'].includes(name)) return null;
            const previewNode = row.querySelector('[data-testid="last-msg-status"]') || row.querySelector('[data-testid="cell-frame-secondary"]');
            let preview = clean(previewNode?.innerText || previewNode?.getAttribute('title') || previewNode?.textContent) || lines.slice(lines.indexOf(timestamp) + 1).join(' ');
            let unread = 0;
            for (const line of [...lines, ...[...row.querySelectorAll('[aria-label]')].map(el => clean(el.getAttribute('aria-label')))]) {
                const match = line.match(/^(\d+) unread messages?$/i);
                if (match) unread = Math.max(unread, Number(match[1]));
                else if (/^unread$/i.test(line)) unread = Math.max(unread, 1);
            }
            if (!unread && /^\d+$/.test(lines.at(-1) || '')) unread = Number(lines.at(-1));
            const index = rowIndex(row);
            return {chat_name:name, timestamp_label:timestamp, preview:preview.replace(/\s+/g,' ').trim(),
                unread_count:unread, unread_flag:unread > 0, index, position:rowPosition(row), ordinal};
        }).filter(Boolean);
        const signature = JSON.stringify(parsed);
        if (signature !== lastSignature || loading || !selected) { lastSignature = signature; stableSince = performance.now(); }
        const indices = nodes.map(rowIndex).filter(index => index !== null);
        const positions = nodes.map(rowPosition).filter(position => position !== null);
        const topReady = anchored || (pane.scrollTop === 0 && (positions.length ? positions.includes(0) : !indices.length || indices.includes(0)));
        const grid = pane.querySelector('[aria-rowcount]');
        const rowCount = grid ? Number(grid.getAttribute('aria-rowcount')) : null;
        lastState = {source:'all-view-sidebar', allSelected:Boolean(selected), loading, rowCount,
            renderedRows:parsed.length, capturedRows:found.size, requestedHorizon, elapsedMs:performance.now()-started};
        if (selected && !loading && topReady && performance.now() - stableSince >= 250) {
            anchored = true;
            for (const node of nodes) {
                const index = rowIndex(node);
                if (index !== null && /^(Locked chats|Archived)(?:\s*\d+)?$/.test(clean(node.innerText || node.textContent))) skippedIndices.add(index);
            }
            for (const row of parsed) {
                const key = row.position !== null ? `position:${row.position}` : row.index !== null ? `index:${row.index}` : `name:${row.chat_name}:${row.timestamp_label}`;
                if (!found.has(key)) found.set(key, row);
            }
            const atEnd = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 2;
            const indexed = [...found.values()].every(row => row.index !== null && row.position === null);
            const ordered = [...found.values()].sort((a,b) => a.position !== null && b.position !== null ? a.position-b.position : a.index !== null && b.index !== null ? a.index-b.index : 0);
            const lastIndex = ordered[Math.min(requestedHorizon, ordered.length)-1]?.index;
            const contiguous = !indexed || (lastIndex !== undefined && Array.from({length:lastIndex+1},(_,i) => found.has(`index:${i}`) || skippedIndices.has(i)).every(Boolean));
            const horizonReady = found.size >= requestedHorizon && contiguous;
            const complete = horizonReady || (rowCount !== null && rowCount >= 0 && found.size + skippedIndices.size >= rowCount) || (rowCount === null && atEnd && found.size > 0 && contiguous);
            if (complete) {
                const rows = ordered.slice(0,requestedHorizon).map(({index,position,ordinal,...row}) => row);
                return JSON.stringify({CHAT_LIST_BODY:true, rows, bodyScope:'sidebar', captureState:{...lastState, capturedRows:rows.length, ready:true}});
            }
            if (parsed.length) {
                pane.scrollTop += Math.max(100, pane.clientHeight * 0.75);
                pane.dispatchEvent(new Event('scroll', {bubbles:true}));
                lastSignature = ''; stableSince = performance.now();
            }
        }
        await wait(100);
    }
    return JSON.stringify({CHAT_LIST_BODY:true, rows:[], bodyScope:'sidebar', captureState:{...lastState, ready:false}, error:'All-view sidebar did not settle before the capture deadline'});
})()'''
MODEL_STORAGE_STORES_JS = '''window.__hermes_async_result = null;(function(){const req=indexedDB.open("model-storage");req.onerror=()=>{window.__hermes_async_result=JSON.stringify({error:String(req.error)});};req.onsuccess=()=>{const db=req.result;window.__hermes_async_result=JSON.stringify({stores:Array.from(db.objectStoreNames)});db.close();};})();"started"'''

DEFAULT_EXCLUDED_LABELS = ["Excluded Label", "Archive Label"]
MAX_MESSAGE_LOOKBACK_HARD_LIMIT = 15
DEFAULT_MAX_MESSAGES = MAX_MESSAGE_LOOKBACK_HARD_LIMIT
DEFAULT_ALL_VIEW_CHAT_LIMIT = 15
GROUP_INCLUDE_STANDARD = "standard"
GROUP_INCLUDE_LABELED_ALWAYS = "labeledAlways"
# Observed group controls: creation stores the group name in body, not a user message.
NONCONTENT_CONTROL_MESSAGE_KINDS = frozenset({("gp2", "create"), ("gp2", "ephemeral")})
ATTACHMENT_MESSAGE_TYPES = {
    "image",
    "document",
    "video",
    "ptt",
    "audio",
    "sticker",
}
IDB_OPERATION_ATTEMPTS = 3
CHAT_CAPTURE_ATTEMPTS = 2
IDB_MESSAGE_ROWS_PER_DIRECTION_FLOOR = 64
IDB_MESSAGE_ROWS_PER_DIRECTION_CEILING = 256


class WhatsAppCollector:
    def __init__(self, session: ChromeWhatsAppSession | None = None) -> None:
        self.session = session or ChromeWhatsAppSession()
        self._idb_read_cache: dict[str, list[dict[str, Any]]] | None = None
        self._idb_key_cache: dict[str, dict[str, dict[str, Any] | None]] | None = None
        self._idb_message_rows_by_jid: dict[str, list[dict[str, Any]]] | None = None
        self._idb_queried_message_jids: set[str] | None = None
        self._attachment_store: AttachmentStore | None = None
        self._attachment_policy = AttachmentPolicy()
        self._previous_threads: dict[str, dict[str, Any]] = {}
        self._opened_capture_details: dict[str, dict[str, Any]] = {}
        self._skipped_recent_threads: list[dict[str, Any]] = []
        self._chat_expiry_settings: dict[str, dict[str, Any]] = {}
        self._excluded_thread_keys: set[str] = set()
        self._run_captures: dict[tuple[str, int], dict[str, Any]] | None = None
        self.capture_checkpoint: list[dict[str, Any]] = []
        self._run_timings: list[dict[str, Any]] | None = None
        self._run_started = 0.0

    def _export_progress(self, phase: str, completed: int, total: int | None, detail: str) -> None:
        if self._run_timings is not None:
            print("WAC_PROGRESS:" + json.dumps({
                "phase": phase, "completed": completed, "total": total, "detail": detail,
                "elapsedSeconds": round(time.monotonic() - self._run_started, 3),
            }), file=sys.stderr, flush=True)

    def _timed_export_call(self, phase: str, callback, *args, **kwargs):
        started = time.monotonic()
        self._export_progress(phase, 0, 1, "started")
        try:
            return callback(*args, **kwargs)
        finally:
            if self._run_timings is not None:
                self._run_timings.append({"phase": phase, "elapsedSeconds": round(time.monotonic() - started, 3)})
            self._export_progress(phase, 1, 1, "finished")

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

    def collect_chat_list(self, *, max_chats: int | None = None) -> list[ChatRow]:
        self._reset_chat_list_to_top()
        horizon = max(1, min(500, int(max_chats if max_chats is not None else getattr(self, "_all_view_capture_horizon", DEFAULT_ALL_VIEW_CHAT_LIMIT))))
        script = CHAT_LIST_BODY_JS.replace("const requestedHorizon = 15;", f"const requestedHorizon = {horizon};")
        payload = self.session.run_json(script)
        if not isinstance(payload, dict):
            raise RuntimeError("All-view sidebar capture returned an invalid payload")
        state = payload.get("captureState")
        self._chat_list_capture_details = dict(state) if isinstance(state, dict) else {}
        if payload.get("error") or isinstance(state, dict) and state.get("ready") is not True:
            raise RuntimeError(str(payload.get("error") or "All-view sidebar capture is incomplete"))
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
        if payload.get("bodyScope") == "sidebar":
            if isinstance(structured_rows, list):
                return []
            return [ChatRow(**item) for item in parse_chat_list(str(payload.get("body") or ""))]
        raise RuntimeError("Chat-list text has no sidebar provenance; refusing to parse conversation content")

    def _reset_chat_list_to_top(self) -> None:
        try:
            self.session.run_json(CHAT_LIST_RESET_JS)
        except Exception:
            pass
        if hasattr(self.session, "clear_sidebar_search"):
            try:
                self.session.clear_sidebar_search()
            except RuntimeError:
                pass

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
        labels_by_id = {row["value"]["id"]: row["value"] for row in label_rows}
        labels_for_jid: dict[str, set[str]] = defaultdict(set)
        for row in association_rows:
            value = row["value"]
            if value.get("type") != "jid":
                continue
            label = labels_by_id.get(value["labelId"])
            if not label:
                continue
            labels_for_jid[value["associationId"]].add(self._clean_label_name(label["name"]))

        selected_labels_by_jid: dict[str, list[str]] = {}
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
            selected_labels_by_jid[jid] = clean_labels

        selected_jids = list(selected_labels_by_jid)
        contact_rows = self._idb_get_many("contact", selected_jids)
        group_rows = self._idb_get_many("group-metadata", selected_jids)
        chat_rows = self._idb_get_many("chat", selected_jids)
        contacts_by_id = {row["value"]["id"]: row["value"] for row in contact_rows}
        groups_by_id = {row["value"]["id"]: row["value"] for row in group_rows}
        chats_by_id = {row["value"]["id"]: row["value"] for row in chat_rows}
        message_keys_by_jid = {
            jid: self._message_lookup_keys_for_thread(
                jid,
                contact=contacts_by_id.get(jid),
                group=groups_by_id.get(jid),
                chat=chats_by_id.get(jid),
            )
            for jid in selected_jids
        }
        message_rows = self._idb_read_recent_messages(
            [key for keys in message_keys_by_jid.values() for key in keys],
            max_messages=max_messages,
        )
        messages_by_jid = self._group_messages_by_jid(message_rows)
        visible_map = self._visible_chat_map(snapshot.chat_list)

        threads: list[IndexedDBThread] = []
        for jid, clean_labels in selected_labels_by_jid.items():
            contact = contacts_by_id.get(jid, {})
            group = groups_by_id.get(jid, {})
            chat = chats_by_id.get(jid)
            self._record_chat_expiry(jid, chat)

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
                messages=self._collect_candidate_messages(message_keys_by_jid.get(jid, [jid]), messages_by_jid),
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
        download_attachments: bool = True,
        max_file_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_FILE_BYTES,
        max_thread_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_THREAD_BYTES,
        max_total_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_TOTAL_BYTES,
        previous_export: dict[str, Any] | None = None,
        resume_captures: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        previous_store = self._attachment_store
        previous_horizon = getattr(self, "_all_view_capture_horizon", DEFAULT_ALL_VIEW_CHAT_LIMIT)
        self._all_view_capture_horizon = max(1, min(500, int(max_all_chats)))
        previous_policy = self._attachment_policy
        previous_threads = self._previous_threads
        previous_run = self._run_captures, self._run_timings, self._run_started
        baseline, _ = self._consolidate_threads((previous_export or {}).get("threads", []), max_messages=max_messages)
        self._previous_threads = {
            str(thread["threadKey"]): thread for thread in baseline
            if isinstance(thread, dict) and thread.get("threadKey")
        }
        self._opened_capture_details = {}
        self._skipped_recent_threads = []
        self._excluded_thread_keys = set()
        self._chat_expiry_settings = {}
        attachments_root = Path(attachments_dir).expanduser() if attachments_dir else None
        self._attachment_policy = AttachmentPolicy.from_settings(
            enabled=bool(download_attachments),
            max_file_bytes=max_file_attachment_bytes,
            max_thread_bytes=max_thread_attachment_bytes,
            max_total_bytes=max_total_attachment_bytes,
        )
        self._attachment_store = AttachmentStore(attachments_root, policy=self._attachment_policy) if attachments_root else None
        self._run_captures, self._run_timings, self._run_started = {}, [], time.monotonic()
        self.capture_checkpoint = []
        for capture in resume_captures or []:
            if capture.get("complete") and capture.get("fingerprint") and capture.get("threadKey"):
                self._run_captures[(capture["threadKey"], max_messages)] = {**deepcopy(capture), "needsRevalidation": True}
        try:
            if self._attachment_store:
                self._attachment_store.protect_export(previous_export)
                for capture in resume_captures or []:
                    self._attachment_store.retention.protect_messages(capture.get("messages", []))
                self._attachment_store.enforce_limits()
            with self._cached_idb_reads():
                payload = self._collect_dashboard_export(
                    account_label=account_label,
                    allow_labels=allow_labels,
                    exclude_labels=exclude_labels,
                    max_messages=max_messages,
                    max_all_chats=max_all_chats,
                    include_groups=include_groups,
                    attachments_dir=attachments_root,
                )
                if self._attachment_store:
                    self._attachment_store.validate_export(payload)
                    payload.setdefault("exportDiagnostics", {})["attachmentCleanup"] = self._attachment_store.retention.summary()
                self._timed_export_call("quality-validation", attach_export_quality, payload, previous_export=previous_export)
                payload.setdefault("exportDiagnostics", {})["phaseTimings"] = list(self._run_timings)
                payload["exportDiagnostics"]["chatCaptures"] = [
                    {key: value for key, value in capture.items() if key not in {"messages", "state", "error"}}
                    for capture in self._run_captures.values()
                ]
                self.capture_checkpoint = [deepcopy(capture) for capture in self._run_captures.values()
                                           if capture.get("complete") and capture.get("fingerprint")]
                self._export_progress("complete", 1, 1, "candidate-ready" if payload["exportQuality"]["ok"] else "candidate-rejected")
                return payload
        except Exception:
            if self._attachment_store and self._attachment_store.retention.failure is not None:
                failure = self._attachment_store.retention.failure
                key = failure.storage_diagnostics.get("threadKey")
                if key in self._previous_threads:
                    failure.storage_diagnostics["chatTitle"] = self._previous_threads[key].get("chatTitle")
                raise failure
            raise
        finally:
            self._all_view_capture_horizon = previous_horizon
            self._attachment_store = previous_store
            self._attachment_policy = previous_policy
            self._previous_threads = previous_threads
            self._run_captures, self._run_timings, self._run_started = previous_run

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
        snapshot = self._timed_export_call("discovery", self.collect_snapshot)
        export_warnings: list[str] = []
        message_capture_skipped_count = 0
        labeled_message_capture_failed_count = 0
        labeled_message_failures: list[dict[str, Any]] = []
        locked_labeled_chat_count = 0
        empty_labeled_chat_count = 0
        skipped_threads: list[dict[str, Any]] = []
        try:
            threads = (
                self._timed_export_call("labeled-discovery", self.collect_labeled_threads,
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
            locked_chat = self._is_locked_chat(thread.jid)
            if locked_chat:
                source_diagnostics = {
                    "issues": [
                        {
                            "code": "locked-chat-not-opened",
                            "detail": "WhatsApp hides this chat from sidebar search while it is locked.",
                        }
                    ]
                }
            else:
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
            elif not confirmed_empty(source_diagnostics or {}):
                message_capture_skipped_count += 1
                issue_codes = {
                    str(issue.get("code") or "")
                    for issue in (source_diagnostics or {}).get("issues", [])
                    if isinstance(issue, dict)
                }
                capture_loading = bool((source_diagnostics or {}).get("captureState", {}).get("isLoading"))
                if locked_chat:
                    locked_labeled_chat_count += 1
                    skip_reason = "locked-chat"
                elif {"opened-chat-check-failed", "empty-chat-not-confirmed"} & issue_codes or capture_loading:
                    labeled_message_capture_failed_count += 1
                    skip_reason = self._capture_skip_reason(source_diagnostics or {})
                    labeled_message_failures.append({
                        "chatTitle": thread.display_name,
                        "threadKey": thread.jid,
                        "labels": clean_labels,
                        "issues": (source_diagnostics or {}).get("issues", []),
                    })
                else:
                    empty_labeled_chat_count += 1
                    skip_reason = "no-exportable-content"
                skipped_threads.append({
                    "chatTitle": thread.display_name, "threadKey": thread.jid,
                    "labels": clean_labels, "reason": skip_reason,
                    "sourceDiagnostics": source_diagnostics or {},
                    "expectedLatestAt": self._format_timestamp(thread.last_message_timestamp),
                })
                continue
            if not recent_messages:
                last_message_at, last_direction, last_sender, last_text = None, "unknown", None, ""
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
        refreshed_chat_rows = self._timed_export_call("recent-discovery", self.collect_chat_list)
        planned_chat_rows = self._plan_all_view_rows(snapshot.chat_list, refreshed_chat_rows, all_view_chat_limit=max_all_chats)
        exported_threads.extend(
            self._recent_default_view_exports(
                chat_rows=planned_chat_rows,
                existing_titles=[thread["chatTitle"] for thread in exported_threads],
                existing_threads=exported_threads,
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
        exported_threads, identity_diagnostics = self._consolidate_threads(exported_threads, max_messages=max_messages)
        exported_threads = [thread for thread in exported_threads if thread.get("threadKey") not in self._excluded_thread_keys]
        exported_threads.sort(key=self._export_thread_recency_key, reverse=True)
        media_recovery = self._recover_missing_media(exported_threads, max_messages=max_messages, attachments_dir=attachments_root)
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
            "exportDiagnostics": {
                "allView": {
                    "requestedChatCount": max_all_chats,
                    "consideredRowCount": len(planned_chat_rows),
                    "captureState": dict(getattr(self, "_chat_list_capture_details", {})),
                    "exportedThreadCount": sum(
                        "all" in thread.get("sourceViews", [thread.get("sourceView")])
                        for thread in exported_threads
                    ),
                },
                "skippedThreads": skipped_threads + self._skipped_recent_threads,
                "mediaRecovery": media_recovery,
                "identityConsolidation": identity_diagnostics,
                "expiry": [
                    {"threadKey": thread["threadKey"], "chatTitle": thread.get("chatTitle"),
                     "setting": thread.get("expirySetting"),
                     "messageCount": len(thread.get("messages", [])),
                     "messagesWithExpiryEvidence": sum(bool(m.get("expiry")) for m in thread.get("messages", [])),
                     "keptMessageCount": sum((m.get("expiry") or {}).get("kept") is True for m in thread.get("messages", [])),
                     "unknownKeepStateCount": sum((m.get("expiry") or {}).get("kept") is None for m in thread.get("messages", [])),
                     "confirmedEmpty": confirmed_empty(thread.get("sourceDiagnostics") or {})}
                    for thread in exported_threads
                ],
            },
        }
        if attachments_root:
            payload["attachmentsRoot"] = str(attachments_root)
            payload["attachmentPolicy"] = self._attachment_policy.to_dict()
            payload["attachmentSummary"] = self._attachment_summary(payload)
            if payload["attachmentSummary"]["notDownloaded"]:
                export_warnings.append(f"attachments-not-downloaded:{payload['attachmentSummary']['notDownloaded']}")
        if message_capture_skipped_count:
            export_warnings.append(f"message-capture-skipped:{message_capture_skipped_count}")
        if labeled_message_capture_failed_count:
            export_warnings.append(f"labeled-message-capture-failed:{labeled_message_capture_failed_count}")
        if locked_labeled_chat_count:
            export_warnings.append(f"labeled-chat-unavailable-while-locked:{locked_labeled_chat_count}")
        if empty_labeled_chat_count:
            export_warnings.append(f"labeled-chat-without-exportable-messages:{empty_labeled_chat_count}")
        if export_warnings:
            payload["exportWarnings"] = export_warnings
        attach_export_quality(payload)
        if labeled_message_failures:
            for issue in payload["exportQuality"]["issues"]:
                if issue.get("code") == "configured-labeled-chat-message-capture-incomplete":
                    issue["threads"] = labeled_message_failures
        return payload

    def _model_storage_stores(self) -> list[str]:
        payload = self._timed_export_call("indexeddb", self.session.run_async_json, MODEL_STORAGE_STORES_JS)
        return list(payload["stores"])

    @contextmanager
    def _cached_idb_reads(self):
        previous_cache = self._idb_read_cache
        previous_key_cache = self._idb_key_cache
        previous_message_cache = self._idb_message_rows_by_jid
        previous_queried_message_jids = self._idb_queried_message_jids
        self._idb_read_cache = {}
        self._idb_key_cache = {}
        self._idb_message_rows_by_jid = {}
        self._idb_queried_message_jids = set()
        try:
            yield
        finally:
            self._idb_read_cache = previous_cache
            self._idb_key_cache = previous_key_cache
            self._idb_message_rows_by_jid = previous_message_cache
            self._idb_queried_message_jids = previous_queried_message_jids

    def _run_idb_operation(self, script: str, *, operation: str) -> Any:
        return self._timed_export_call("indexeddb", self._run_idb_operation_with_retries, script, operation=operation)

    def _run_idb_operation_with_retries(self, script: str, *, operation: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, IDB_OPERATION_ATTEMPTS + 1):
            try:
                payload = self.session.run_async_json(script)
                if isinstance(payload, dict) and payload.get("error"):
                    raise ValueError(str(payload["error"]))
                return payload
            except (RuntimeError, TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt < IDB_OPERATION_ATTEMPTS:
                    time.sleep(0.2 * attempt)
        raise RuntimeError(
            f"{operation} failed after {IDB_OPERATION_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _idb_payload_rows(payload: Any, *, operation: str) -> list[dict[str, Any]]:
        rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError(f"{operation} returned an invalid rows payload")
        return [row for row in rows if isinstance(row, dict)]

    def _idb_read_all(self, store_name: str) -> list[dict[str, Any]]:
        if self._idb_read_cache is not None and store_name in self._idb_read_cache:
            return self._idb_read_cache[store_name]
        quoted_store = store_name.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''window.__hermes_async_result = null;(function(){{const req=indexedDB.open("model-storage");req.onerror=()=>{{window.__hermes_async_result=JSON.stringify({{error:String(req.error)}});}};req.onsuccess=()=>{{const db=req.result;const tx=db.transaction("{quoted_store}","readonly");const os=tx.objectStore("{quoted_store}");const allRecords=[];const cursorReq=os.openCursor();cursorReq.onerror=()=>{{window.__hermes_async_result=JSON.stringify({{error:String(cursorReq.error)}});db.close();}};cursorReq.onsuccess=(event)=>{{const cursor=event.target.result;if(cursor){{allRecords.push({{ key: cursor.key, value: cursor.value }});cursor.continue();}} else {{window.__hermes_async_result=JSON.stringify(allRecords);db.close();}}}};}};}})();"started"'''
        operation = f'IndexedDB full read for store "{store_name}"'
        payload = self._run_idb_operation(script, operation=operation)
        rows = self._idb_payload_rows(payload, operation=operation)
        if self._idb_read_cache is not None:
            self._idb_read_cache[store_name] = rows
        return rows

    def _idb_get_many(self, store_name: str, keys: list[str]) -> list[dict[str, Any]]:
        unique_keys = list(dict.fromkeys(key.strip() for key in keys if isinstance(key, str) and key.strip()))
        if not unique_keys:
            return []
        if self._idb_read_cache is not None and store_name in self._idb_read_cache:
            rows_by_key = {
                str(row.get("key")): row
                for row in self._idb_read_cache[store_name]
                if isinstance(row.get("key"), str)
            }
            return [rows_by_key[key] for key in unique_keys if key in rows_by_key]

        store_cache = self._idb_key_cache.setdefault(store_name, {}) if self._idb_key_cache is not None else None
        missing_keys = [key for key in unique_keys if store_cache is None or key not in store_cache]
        if missing_keys:
            store_json = json.dumps(store_name)
            keys_json = json.dumps(missing_keys, ensure_ascii=False)
            script = f'''window.__hermes_async_result = null;(function(){{
                const keys = {keys_json};
                const startedAt = performance.now();
                const openRequest = indexedDB.open("model-storage");
                openRequest.onerror = () => {{
                    window.__hermes_async_result = JSON.stringify({{error: `open:${{String(openRequest.error)}}`}});
                }};
                openRequest.onsuccess = () => {{
                    const db = openRequest.result;
                    let settled = false;
                    const fail = (error) => {{
                        if (settled) return;
                        settled = true;
                        window.__hermes_async_result = JSON.stringify({{error: String(error)}});
                        db.close();
                    }};
                    const tx = db.transaction({store_json}, "readonly");
                    tx.onabort = () => fail(`transaction-aborted:${{String(tx.error)}}`);
                    const objectStore = tx.objectStore({store_json});
                    const allRecords = [];
                    let pending = keys.length;
                    const finishOne = () => {{
                        pending -= 1;
                        if (pending > 0 || settled) return;
                        settled = true;
                        window.__hermes_async_result = JSON.stringify({{
                            rows: allRecords,
                            diagnostics: {{store: {store_json}, requestedKeyCount: keys.length, returnedRowCount: allRecords.length, durationMs: performance.now() - startedAt}}
                        }});
                        db.close();
                    }};
                    for (const key of keys) {{
                        const request = objectStore.get(key);
                        request.onerror = () => fail(`get:${{String(request.error)}}`);
                        request.onsuccess = () => {{
                            if (request.result !== undefined) allRecords.push({{key, value: request.result}});
                            finishOne();
                        }};
                    }}
                }};
            }})();"started"'''
            operation = f'IndexedDB keyed read for store "{store_name}"'
            payload = self._run_idb_operation(script, operation=operation)
            fetched_rows = self._idb_payload_rows(payload, operation=operation)
            fetched_by_key: dict[str, dict[str, Any]] = {}
            requested = set(missing_keys)
            for row in fetched_rows:
                row_key = row.get("key")
                value = row.get("value")
                value_id = value.get("id") if isinstance(value, dict) else None
                matching_key = row_key if isinstance(row_key, str) and row_key in requested else value_id
                if isinstance(matching_key, str) and matching_key in requested:
                    fetched_by_key[matching_key] = row
            if store_cache is not None:
                for key in missing_keys:
                    store_cache[key] = fetched_by_key.get(key)
            elif len(missing_keys) == len(unique_keys):
                return [fetched_by_key[key] for key in unique_keys if key in fetched_by_key]

        if store_cache is None:
            return []
        return [row for key in unique_keys if (row := store_cache.get(key)) is not None]

    def _idb_read_recent_messages(self, jids: list[str], *, max_messages: int) -> list[dict[str, Any]]:
        unique_jids = list(dict.fromkeys(jid.strip() for jid in jids if isinstance(jid, str) and "@" in jid))
        if not unique_jids:
            return []
        queried = self._idb_queried_message_jids
        rows_by_jid = self._idb_message_rows_by_jid
        missing_jids = [jid for jid in unique_jids if queried is None or jid not in queried]
        if missing_jids:
            per_direction_limit = max(
                IDB_MESSAGE_ROWS_PER_DIRECTION_FLOOR,
                min(IDB_MESSAGE_ROWS_PER_DIRECTION_CEILING, max_messages * 8),
            )
            jids_json = json.dumps(missing_jids, ensure_ascii=False)
            script = f'''window.__hermes_async_result = null;(function(){{
                const jids = {jids_json};
                const perDirectionLimit = {per_direction_limit};
                const startedAt = performance.now();
                const mediaTypes = new Set(["image", "document", "video", "ptt", "audio", "sticker"]);
                const isPotentiallyExportable = (value) => {{
                    if (!value || typeof value !== "object") return false;
                    const type = String(value.type || "").toLowerCase();
                    const hasText = [value.body, value.caption, value.text, value.matchedText]
                        .some(item => typeof item === "string" && item.trim());
                    const hasMediaMetadata = mediaTypes.has(type)
                        || type === "album"
                        || (Array.isArray(value.attachments) && value.attachments.length > 0)
                        || (typeof value.mimetype === "string" && value.mimetype)
                        || (typeof value.fileName === "string" && value.fileName)
                        || (typeof value.filename === "string" && value.filename)
                        || value.parentMsgKey
                        || value.parentMsgId;
                    return Boolean(hasText || hasMediaMetadata);
                }};
                const openRequest = indexedDB.open("model-storage");
                openRequest.onerror = () => {{
                    window.__hermes_async_result = JSON.stringify({{error: `open:${{String(openRequest.error)}}`}});
                }};
                openRequest.onsuccess = () => {{
                    const db = openRequest.result;
                    let settled = false;
                    const fail = (error) => {{
                        if (settled) return;
                        settled = true;
                        window.__hermes_async_result = JSON.stringify({{error: String(error)}});
                        db.close();
                    }};
                    const allRecords = [];
                    let scannedRowCount = 0;
                    let eligibleRowCount = 0;
                    let fallbackJidCount = 0;
                    const finish = (queryMode) => {{
                        if (settled) return;
                        settled = true;
                        allRecords.sort((left, right) => (Number(right.value && right.value.t) || 0) - (Number(left.value && left.value.t) || 0));
                        window.__hermes_async_result = JSON.stringify({{
                            rows: allRecords,
                            diagnostics: {{
                                store: "message",
                                queriedJidCount: jids.length,
                                scannedRowCount,
                                eligibleRowCount,
                                returnedRowCount: allRecords.length,
                                perDirectionLimit,
                                queryMode,
                                fallbackJidCount,
                                durationMs: performance.now() - startedAt
                            }}
                        }});
                        db.close();
                    }};
                    const runBatch = (mode, batchJids, complete) => {{
                        const tx = db.transaction("message", "readonly");
                        tx.onabort = () => fail(`transaction-aborted:${{String(tx.error)}}`);
                        const objectStore = tx.objectStore("message");
                        const source = mode === "messageRangeIndex"
                            ? objectStore.index("messageRangeIndex")
                            : objectStore;
                        let pending = batchJids.length * 2;
                        const scannedByJid = new Map(batchJids.map(jid => [jid, 0]));
                        const finishOne = (jid, rows, scanned) => {{
                            allRecords.push(...rows);
                            scannedByJid.set(jid, (scannedByJid.get(jid) || 0) + scanned);
                            pending -= 1;
                            if (pending <= 0 && !settled) complete(scannedByJid);
                        }};
                        const scan = (jid, partition) => {{
                            const rows = [];
                            let scanned = 0;
                            const lower = mode === "messageRangeIndex"
                                ? `${{jid}}_${{partition}}_`
                                : `${{partition}}_${{jid}}_`;
                            const upper = `${{lower}}\uffff`;
                            const direction = mode === "messageRangeIndex" ? "prev" : "next";
                            const request = source.openCursor(IDBKeyRange.bound(lower, upper), direction);
                            request.onerror = () => fail(`cursor:${{mode}}:${{jid}}:${{String(request.error)}}`);
                            request.onsuccess = (event) => {{
                                const cursor = event.target.result;
                                if (!cursor) {{
                                    finishOne(jid, rows, scanned);
                                    return;
                                }}
                                scanned += 1;
                                scannedRowCount += 1;
                                const value = cursor.value;
                                if (isPotentiallyExportable(value)) {{
                                    eligibleRowCount += 1;
                                    const candidate = {{key: cursor.primaryKey, value}};
                                    if (mode === "messageRangeIndex") {{
                                        rows.push(candidate);
                                        if (rows.length >= perDirectionLimit) {{
                                            finishOne(jid, rows, scanned);
                                            return;
                                        }}
                                    }} else if (rows.length < perDirectionLimit) {{
                                        rows.push(candidate);
                                    }} else {{
                                        let oldestIndex = 0;
                                        for (let index = 1; index < rows.length; index += 1) {{
                                            if ((Number(rows[index].value && rows[index].value.t) || 0) < (Number(rows[oldestIndex].value && rows[oldestIndex].value.t) || 0)) oldestIndex = index;
                                        }}
                                        if ((Number(value.t) || 0) > (Number(rows[oldestIndex].value && rows[oldestIndex].value.t) || 0)) rows[oldestIndex] = candidate;
                                    }}
                                }}
                                cursor.continue();
                            }};
                        }};
                        for (const jid of batchJids) {{
                            if (mode === "messageRangeIndex") {{
                                scan(jid, "1");
                                scan(jid, "2");
                            }} else {{
                                scan(jid, "false");
                                scan(jid, "true");
                            }}
                        }}
                    }};
                    const probeTx = db.transaction("message", "readonly");
                    const hasChronologicalIndex = probeTx.objectStore("message").indexNames.contains("messageRangeIndex");
                    if (!hasChronologicalIndex) {{
                        runBatch("primaryKey", jids, () => finish("primaryKey"));
                        return;
                    }}
                    runBatch("messageRangeIndex", jids, (scannedByJid) => {{
                        const fallbackJids = jids.filter(jid => (scannedByJid.get(jid) || 0) <= 0);
                        fallbackJidCount = fallbackJids.length;
                        if (!fallbackJids.length) {{
                            finish("messageRangeIndex");
                            return;
                        }}
                        runBatch("primaryKey", fallbackJids, () => finish("messageRangeIndex+primaryKeyFallback"));
                    }});
                }};
            }})();"started"'''
            operation = "IndexedDB bounded recent-message range query"
            payload = self._run_idb_operation(script, operation=operation)
            fetched_rows = self._idb_payload_rows(payload, operation=operation)
            fetched_by_jid: dict[str, list[dict[str, Any]]] = defaultdict(list)
            requested = set(missing_jids)
            for row in fetched_rows:
                storage_jid = self._message_jid_from_storage_key(row.get("key"))
                if storage_jid in requested:
                    fetched_by_jid[storage_jid].append(row)
            if queried is not None and rows_by_jid is not None:
                for jid in missing_jids:
                    queried.add(jid)
                    rows_by_jid[jid] = fetched_by_jid.get(jid, [])
            else:
                rows_by_jid = fetched_by_jid

        if rows_by_jid is None:
            return []
        deduplicated: dict[str, dict[str, Any]] = {}
        for jid in unique_jids:
            for row in rows_by_jid.get(jid, []):
                key = row.get("key")
                if isinstance(key, str):
                    deduplicated[key] = row
        return list(deduplicated.values())

    @staticmethod
    def _normalized_chat_identity(value: str) -> str:
        return "".join(character for character in unicodedata.normalize("NFKC", value or "").casefold() if character.isalnum())

    @staticmethod
    def _phone_digits(value: str | None) -> str:
        return re.sub(r"\D+", "", value or "")

    @staticmethod
    def _message_jid_from_storage_key(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        parts = value.split("_", 2)
        if len(parts) < 3 or "@" not in parts[1]:
            return None
        return parts[1]

    def _is_locked_chat(self, jid: str) -> bool:
        try:
            rows = self._idb_get_many("chat", [jid])
        except (RuntimeError, TimeoutError, ValueError):
            return False
        return any(
            isinstance(row.get("value"), dict) and row["value"].get("isLocked") is True
            for row in rows
        )

    @classmethod
    def _message_jid_aliases(cls, value: Any) -> list[str]:
        if not isinstance(value, str):
            return []
        jid = value.strip()
        if not jid or "@" not in jid:
            return []
        aliases = [jid]
        if jid.endswith("@c.us"):
            aliases.append(f"{jid[:-5]}@s.whatsapp.net")
        elif jid.endswith("@s.whatsapp.net"):
            aliases.append(f"{jid[:-15]}@c.us")
        return aliases

    @classmethod
    def _message_lookup_keys_for_thread(
        cls,
        jid: str,
        *,
        contact: dict[str, Any] | None,
        group: dict[str, Any] | None,
        chat: dict[str, Any] | None,
    ) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()
        values: list[Any] = [jid]
        for source in (contact or {}, group or {}, chat or {}):
            values.extend(source.get(key) for key in ("id", "phoneNumber", "historyChatId", "accountLid"))
        for value in values:
            for alias in cls._message_jid_aliases(value):
                if alias in seen:
                    continue
                seen.add(alias)
                keys.append(alias)
        return keys

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
        labels_by_id = {row["value"]["id"]: row["value"] for row in label_rows}
        labels_for_jid: dict[str, set[str]] = defaultdict(set)
        for row in association_rows:
            value = row["value"]
            if value.get("type") != "jid":
                continue
            label = labels_by_id.get(value["labelId"])
            if not label:
                continue
            labels_for_jid[value["associationId"]].add(self._normalize_label_slug(label["name"]))

        excluded_jids = [
            jid
            for jid, normalized_labels in labels_for_jid.items()
            if self._thread_has_only_excluded_labels(normalized_labels, normalized_excluded_labels)
        ]
        self._excluded_thread_keys.update(excluded_jids)
        contact_rows = self._idb_get_many("contact", excluded_jids)
        group_rows = self._idb_get_many("group-metadata", excluded_jids)
        contacts_by_id = {row["value"]["id"]: row["value"] for row in contact_rows}
        groups_by_id = {row["value"]["id"]: row["value"] for row in group_rows}

        excluded_names: set[str] = set()
        for jid in excluded_jids:
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
        existing_threads: list[dict[str, Any]] | None = None,
        excluded_titles: set[str],
        include_groups: str,
        max_messages: int,
        limit: int = 15,
        attachments_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        seen_titles = {self._normalized_chat_identity(title) for title in existing_titles}
        seen_thread_keys = {str(thread.get("threadKey")) for thread in (existing_threads or []) if thread.get("threadKey")}
        recent_exports: list[dict[str, Any]] = []
        contacts_by_name, groups_by_name, chats_by_id, chats_by_alias, _ = self._default_view_lookup_maps()
        for row in chat_rows[:limit]:
            normalized_name = self._normalized_chat_identity(row.chat_name)
            if not normalized_name:
                continue

            row_aliases = self._alias_keys_for_value(row.chat_name)
            contact = next((contacts_by_name[alias] for alias in row_aliases if alias in contacts_by_name), None)
            group = next((groups_by_name[alias] for alias in row_aliases if alias in groups_by_name), None)
            chat = self._resolve_default_view_chat(row, contact=contact, group=group, chats_by_alias=chats_by_alias)
            jid = (chat or contact or group or {}).get("id")
            chat = chats_by_id.get(jid, chat) if jid else chat
            if jid:
                if jid in seen_thread_keys or jid in self._excluded_thread_keys:
                    for existing in existing_threads or []:
                        if existing.get("threadKey") == jid:
                            existing["sourceViews"] = sorted(set(existing.get("sourceViews", [existing.get("sourceView") or "labeled/forced"])) | {"all"})
                    continue
            elif normalized_name in seen_titles or normalized_name in excluded_titles:
                continue
            # Names are only a fallback. Distinct resolved IDs can share a display name.
            seen_titles.add(normalized_name)
            if jid:
                seen_thread_keys.add(jid)
                self._record_chat_expiry(jid, chat)
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
            candidate_message_keys = (
                self._message_lookup_keys_for_thread(str(jid), contact=contact, group=group, chat=chat)
                if isinstance(jid, str) and jid
                else []
            )
            message_rows = self._idb_read_recent_messages(candidate_message_keys, max_messages=max_messages)
            messages_by_jid = self._group_messages_by_jid(message_rows)
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
            if not jid:
                resolved = (source_diagnostics or {}).get("captureState", {}).get("resolvedThreadKey")
                if resolved:
                    jid = resolved
                    seen_thread_keys.add(jid)
            if not recent_messages and not confirmed_empty(source_diagnostics or {}):
                self._skipped_recent_threads.append({
                    "chatTitle": row.chat_name, "threadKey": jid, "sourceView": "all",
                    "reason": self._capture_skip_reason(source_diagnostics or {}), "sourceDiagnostics": source_diagnostics or {},
                })
                continue

            last_message = recent_messages[0] if recent_messages else {}
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
                "lastMessageAt": last_message.get("timestamp"),
                "lastMessageDirection": last_message.get("direction", "unknown"),
                "lastMessageSender": last_message.get("sender"),
                "lastMessageText": (last_message.get("text") or row.preview) if recent_messages else "",
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
            group_rows = self._idb_read_all("group-metadata")
            chat_rows = self._idb_read_all("chat")
        except (RuntimeError, TimeoutError, ValueError):
            return []

        sorted_chat_rows = sorted(
            [
                row
                for row in chat_rows
                if isinstance(row.get("value"), dict) and row["value"].get("id")
            ],
            key=lambda row: ((row["value"].get("t") or 0), str(row["value"].get("id") or "")),
            reverse=True,
        )
        candidate_chat_rows = sorted_chat_rows[:limit]
        contact_keys: list[str] = []
        for row in candidate_chat_rows:
            chat = row["value"]
            for field in ("id", "historyChatId", "accountLid"):
                contact_keys.extend(self._message_jid_aliases(chat.get(field)))
        try:
            contact_rows = self._idb_get_many("contact", contact_keys)
        except (RuntimeError, TimeoutError, ValueError):
            contact_rows = []

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
        message_keys_by_jid: dict[str, list[str]] = {}
        for row in candidate_chat_rows:
            chat = row["value"]
            jid = str(chat.get("id") or "").strip()
            if not jid:
                continue
            contact = self._contact_for_chat(chat, contacts_by_id=contacts_by_id, contacts_by_alias=contacts_by_alias)
            group = self._group_for_chat(chat, groups_by_id=groups_by_id, groups_by_alias=groups_by_alias)
            message_keys_by_jid[jid] = self._message_lookup_keys_for_thread(
                jid,
                contact=contact,
                group=group,
                chat=chat,
            )
        message_rows = self._idb_read_recent_messages(
            [key for keys in message_keys_by_jid.values() for key in keys],
            max_messages=max_messages,
        )
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

        for row in candidate_chat_rows:
            chat = row["value"]
            jid = str(chat.get("id") or "").strip()
            if not jid or jid in seen_thread_keys:
                continue
            is_group_chat = jid.endswith("@g.us")

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
            if not normalized_display_name or normalized_display_name == "you":
                continue

            phone_or_history_id = None
            if contact:
                phone_or_history_id = contact.get("phoneNumber")
            if not phone_or_history_id:
                phone_or_history_id = chat.get("historyChatId") or chat.get("accountLid")

            candidate_messages = self._collect_candidate_messages(
                message_keys_by_jid.get(jid, [jid]),
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
            self._record_chat_expiry(jid, chat)
            if not self._is_locked_chat(jid):
                previous = self._previous_threads.get(jid, {})
                recent_messages, source_diagnostics = self._refresh_recent_messages_from_opened_chat(
                    display_name, recent_messages=recent_messages, max_messages=max_messages,
                    attachments_dir=attachments_dir, thread_key=jid,
                    expected_latest_timestamp=chat.get("t"),
                    force=len(recent_messages) < max_messages or len(recent_messages) < len(previous.get("messages", [])),
                    return_diagnostics=True,
                )
                previous_ids = {m.get("messageId") for m in previous.get("messages", [])}
                current_ids = {m.get("messageId") for m in recent_messages}
                if previous_ids - current_ids and not (source_diagnostics or {}).get("openedChatChecked"):
                    recent_messages, source_diagnostics = self._refresh_recent_messages_from_opened_chat(
                        display_name, recent_messages=recent_messages, max_messages=max_messages,
                        attachments_dir=attachments_dir, thread_key=jid, expected_latest_timestamp=chat.get("t"),
                        force=True, return_diagnostics=True,
                    )
            else:
                source_diagnostics = {"issues": [{"code": "locked-chat-not-opened"}]}
            latest_raw = self._latest_raw_message(candidate_messages)
            latest_message = recent_messages[0] if recent_messages else None
            last_message_at = (
                (latest_message or {}).get("timestamp")
                or self._format_timestamp((latest_raw or {}).get("t"))
                or self._format_timestamp(chat.get("t"))
            )
            empty = confirmed_empty(source_diagnostics or {})
            if not last_message_at and not empty:
                self._skipped_recent_threads.append({
                    "chatTitle": display_name, "threadKey": jid, "sourceView": "indexeddb-recent",
                    "reason": self._capture_skip_reason(source_diagnostics or {}), "sourceDiagnostics": source_diagnostics or {},
                })
                continue

            if latest_message:
                last_direction = str(latest_message.get("direction") or "")
                last_sender = latest_message.get("sender")
                last_text = str(latest_message.get("text") or "")
            elif empty:
                last_message_at, last_direction, last_sender, last_text = None, "unknown", None, ""
            else:
                if not latest_raw:
                    self._skipped_recent_threads.append({
                        "chatTitle": display_name, "threadKey": jid, "sourceView": "indexeddb-recent",
                        "reason": self._capture_skip_reason(source_diagnostics or {}), "sourceDiagnostics": source_diagnostics or {},
                    })
                    continue
                last_direction = self._message_direction(latest_raw or {}) if latest_raw else "unknown"
                last_sender = (
                    self._message_sender(latest_raw, display_name=display_name, direction=last_direction)
                    if latest_raw
                    else None
                )
                last_text = ""
                if not last_text:
                    self._skipped_recent_threads.append({
                        "chatTitle": display_name, "threadKey": jid, "sourceView": "indexeddb-recent",
                        "reason": self._capture_skip_reason(source_diagnostics or {}), "sourceDiagnostics": source_diagnostics or {},
                    })
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
            seen_thread_keys.add(jid)
            seen_titles.add(normalized_display_name)
        return recent_exports

    @staticmethod
    def _capture_skip_reason(diagnostics: dict[str, Any]) -> str:
        codes = {issue.get("code") for issue in diagnostics.get("issues", [])}
        if "locked-chat-not-opened" in codes:
            return "locked-chat"
        if "opened-chat-check-failed" in codes:
            return "chat-open-failed"
        if any(diagnostics.get("captureState", {}).get(flag) for flag in ("isLoading", "waitTimedOut", "timedOut", "timeout")):
            return "chat-content-not-ready"
        return "empty-unconfirmed"

    def _capture_chat_with_recovery(
        self, chat_name: str, *, thread_key: str, max_messages: int, attachments_dir: Path | None,
        expected_exportable_timestamp: Any = None,
    ) -> dict[str, Any]:
        key = (thread_key, max_messages)
        cached_key = key
        cached = (self._run_captures or {}).get(key)
        if cached is None:
            match = next(((capture_key, capture) for capture_key, capture in (self._run_captures or {}).items()
                          if capture_key[1] == max_messages and capture["state"].get("resolvedThreadKey") == thread_key), None)
            if match:
                cached_key, cached = match
        if cached is not None:
            if cached.pop("needsRevalidation", False):
                jid = cached.get("state", {}).get("resolvedThreadKey")
                now = datetime.now(timezone.utc).isoformat()
                fingerprint = self._live_capture_fingerprint(jid)
                if (not fingerprint or fingerprint != cached.get("fingerprint")
                        or any(expected_expired(message, now, grace_seconds=0) for message in cached.get("messages", []))
                        or not self._resumed_media_verified(cached.get("messages", []))):
                    self._run_captures.pop(cached_key, None)
                    cached = None
                else:
                    cached["resumed"] = True
        if cached is not None:
            cached["reuseCount"] += 1
            self._opened_capture_details[thread_key] = deepcopy(cached["state"])
            return deepcopy(cached)
        started = time.monotonic()
        completed = len(self._run_captures or {})
        result: dict[str, Any] = {"threadKey": thread_key, "messages": [], "state": {}, "error": None,
                                  "captureAttempts": 0, "reuseCount": 0, "attempts": []}
        for attempt in range(1, CHAT_CAPTURE_ATTEMPTS + 1):
            self._export_progress("chat-capture" if attempt == 1 else "chat-retry", completed, None,
                                  f"attempt-{attempt}")
            attempt_started = time.monotonic()
            self._opened_capture_details.pop(thread_key, None)
            result.update(messages=[], state={}, error=None, captureAttempts=attempt)
            try:
                result["messages"] = self._opened_chat_recent_messages_for_chat(
                    chat_name, thread_key=thread_key, max_messages=max_messages, attachments_dir=attachments_dir,
                )
                result["state"] = deepcopy(self._opened_capture_details.get(thread_key, {}))
            except (RuntimeError, TimeoutError, ValueError) as exc:
                result["error"] = str(exc)
            diagnostics = {"openedChatChecked": not result["error"], "captureState": result["state"],
                           "openedChatMessageCount": len(result["messages"]), "sourcesUsed": ["opened-chat"]}
            complete = not result["error"] and complete_live_capture(diagnostics, max_messages)
            state = result["state"]
            settled = not any(state.get(flag) for flag in ("isLoading", "waitTimedOut", "timedOut", "timeout"))
            complete = complete and settled
            latest = self._exported_message_epoch(self._latest_exported_message(result["messages"]) or {})
            expected = self._whatsapp_timestamp_epoch(expected_exportable_timestamp)
            stale = not complete and expected is not None and (latest is None or latest + 1 < expected)
            successful = not result["error"] and settled and not stale and (bool(result["messages"]) or confirmed_empty(diagnostics))
            result["attempts"].append({"attempt": attempt, "elapsedSeconds": round(time.monotonic() - attempt_started, 3),
                                       "outcome": "captured" if successful else "open-failed" if result["error"]
                                       else "coverage-incomplete" if state.get("captureIncomplete") and state.get("activeLoading") is False
                                       else "loading" if not settled else "stale" if stale else "unconfirmed"})
            if successful:
                break
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        result["recovered"] = successful and result["captureAttempts"] > 1
        result["complete"] = complete
        resumable = complete and result["state"].get("identityVerified") and not result["state"].get("captureIncomplete")
        result["fingerprint"] = self._live_capture_fingerprint(result["state"].get("resolvedThreadKey")) if resumable else None
        result["usable"] = successful
        result["incomplete"] = not successful and any(a["outcome"] in {"loading", "stale", "coverage-incomplete"} for a in result["attempts"])
        if self._run_captures is not None:
            self._run_captures[key] = deepcopy(result)
        self._export_progress("chat-capture", completed + 1, None, "captured" if successful else "exhausted")
        return result

    @staticmethod
    def _resumed_media_verified(messages: list[dict[str, Any]]) -> bool:
        for message in messages:
            for attachment in message.get("attachments", []):
                if attachment.get("status") != "downloaded":
                    return False
                try:
                    path = Path(attachment["localPath"])
                    if (path.stat().st_size != attachment.get("sizeBytes")
                            or hashlib.sha256(path.read_bytes()).hexdigest() != attachment.get("sha256")):
                        return False
                except (OSError, KeyError, TypeError):
                    return False
        return True

    def _live_capture_fingerprint(self, jid: str | None) -> str | None:
        if not jid or not hasattr(self.session, "run_json"):
            return None
        try:
            snapshot = self.session.run_json(f'''(() => {{
                if (!navigator.onLine) return JSON.stringify(null);
                {MESSAGE_EXPIRY_HELPERS_JS}
                const module = window.require('WAWebChatCollection');
                const chat = (module.ChatCollection || module.Chat).get({json.dumps(jid)});
                const load = chat?.msgs?.msgLoadState;
                if (!chat || !load?.contextLoaded || chat.pendingInitialLoading || load.isLoadingEarlierMsgs
                    || load.isLoadingRecentMsgs || load.isLoadingAroundMsgs || load.isRepairingMsgHistory)
                    return JSON.stringify(null);
                const messages = chat.msgs.getModelsArray?.() || chat.msgs.models || chat.msgs._models;
                if (!Array.isArray(messages)) return JSON.stringify(null);
                return JSON.stringify({{t:chat.t, expiry:chat.ephemeralDuration,
                    messages:messages.map(m => [m.id?._serialized || m.id, m.t, m.type, m.subtype,
                        m.body, m.caption, m.text, m.filehash, m.size, m.lat, m.lng, waCollectorExpiry(m)])}});
            }})()''')
            return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() if snapshot else None
        except (RuntimeError, ValueError, TypeError):
            return None

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
        previous_messages = self._previous_threads.get(thread_key, {}).get("messages", [])
        previous_latest = self._exported_message_epoch(self._latest_exported_message(previous_messages) or {})
        current_latest = self._exported_message_epoch(self._latest_exported_message(recent_messages) or {})
        if previous_latest is not None and (current_latest is None or previous_latest > current_latest + 1):
            force = True
        if not force and not self._should_refresh_opened_chat_messages(
            recent_messages,
            expected_latest_timestamp=expected_latest_timestamp,
            preview=preview,
        ):
            return (recent_messages, None) if return_diagnostics else recent_messages
        # Chat activity may be a call/system event. Only message evidence can prove stale content.
        expected_exportable_timestamp = self._known_exportable_timestamp(thread_key, recent_messages)
        capture = self._capture_chat_with_recovery(
            chat_name, thread_key=thread_key, max_messages=max_messages, attachments_dir=attachments_dir,
            expected_exportable_timestamp=expected_exportable_timestamp,
        )
        opened_messages = capture["messages"]
        timing = {key: capture[key] for key in ("captureAttempts", "elapsedSeconds", "recovered", "reuseCount", "attempts")}
        timing["captureIncomplete"] = capture["incomplete"]
        if capture["error"]:
            diagnostic = self._message_source_diagnostics(
                indexeddb_messages=recent_messages,
                opened_chat_messages=[],
                merged_messages=recent_messages,
                opened_chat_checked=True,
                opened_chat_error=capture["error"],
                max_messages=max_messages,
            )
            diagnostic.update(timing)
            if not recent_messages:
                diagnostic["issues"].append({"code": "empty-chat-not-confirmed"})
            self._annotate_stale_cached_history(diagnostic, recent_messages, expected_exportable_timestamp, thread_key)
            return (recent_messages, diagnostic) if return_diagnostics else recent_messages
        if not opened_messages:
            state = self._opened_capture_details.get(thread_key, {})
            empty = confirmed_empty({"openedChatChecked": True, "captureState": state})
            result_messages = [] if empty else recent_messages
            diagnostic = self._message_source_diagnostics(
                indexeddb_messages=recent_messages,
                opened_chat_messages=[],
                merged_messages=result_messages,
                opened_chat_checked=True,
                max_messages=max_messages,
            )
            diagnostic.update(timing)
            diagnostic["captureState"] = self._opened_capture_details.get(thread_key, {})
            if empty:
                diagnostic["sourcesUsed"] = ["opened-chat"]
                diagnostic["issues"] = [{"code": "confirmed-empty-live-chat"}]
            else:
                diagnostic["issues"].append({"code": "empty-chat-not-confirmed"})
                self._annotate_stale_cached_history(diagnostic, recent_messages, expected_exportable_timestamp, thread_key)
            return (result_messages, diagnostic) if return_diagnostics else result_messages
        state = self._opened_capture_details.get(thread_key, {})
        complete = bool(state) and complete_live_capture({"openedChatMessageCount": len(opened_messages),
                                          "sourcesUsed": ["opened-chat"], "captureState": state}, max_messages)
        fallback = [m for m in recent_messages if m.get("messageId") in {item.get("messageId") for item in opened_messages}] if complete else recent_messages
        merged_messages = self._merge_recent_message_exports(opened_messages, fallback, max_messages=max_messages)
        diagnostic = self._message_source_diagnostics(
            indexeddb_messages=recent_messages,
            opened_chat_messages=opened_messages,
            merged_messages=merged_messages,
            opened_chat_checked=True,
            max_messages=max_messages,
        )
        diagnostic.update(timing)
        diagnostic["captureState"] = self._opened_capture_details.get(thread_key, {})
        if not complete:
            self._annotate_stale_cached_history(diagnostic, opened_messages, expected_exportable_timestamp, thread_key)
        return (merged_messages, diagnostic) if return_diagnostics else merged_messages

    def _known_exportable_timestamp(self, thread_key: str, messages: list[dict[str, Any]]) -> float | None:
        now = datetime.now(timezone.utc).isoformat()
        evidence = [*messages, *self._previous_threads.get(thread_key, {}).get("messages", [])]
        return max((timestamp for message in evidence
                    if (message.get("text") or message.get("attachments")) and not expected_expired(message, now)
                    if (timestamp := self._exported_message_epoch(message)) is not None), default=None)

    def _annotate_stale_cached_history(self, diagnostics: dict[str, Any], messages: list[dict[str, Any]], expected_timestamp: Any, thread_key: str) -> None:
        latest = self._exported_message_epoch(self._latest_exported_message(messages) or {})
        expected = self._whatsapp_timestamp_epoch(expected_timestamp)
        previous_latest = self._known_exportable_timestamp(thread_key, [])
        known = max((value for value in (expected, previous_latest) if value is not None), default=None)
        if messages and known is not None and (latest is None or known > latest + 1):
            diagnostics["issues"].append({
                "code": "cached-history-still-stale", "expectedLatestAt": self._format_timestamp(known),
                "capturedLatestAt": (self._latest_exported_message(messages) or {}).get("timestamp"),
            })

    def _attach_source_diagnostics(self, export_thread: dict[str, Any], diagnostics: dict[str, Any] | None) -> None:
        if diagnostics and (diagnostics.get("openedChatChecked") or diagnostics.get("issues")):
            export_thread["sourceDiagnostics"] = diagnostics
        setting = (diagnostics or {}).get("captureState", {}).get("expirySetting") or self._chat_expiry_settings.get(export_thread.get("threadKey"))
        if setting:
            export_thread["expirySetting"] = setting

    def _record_chat_expiry(self, jid: str, chat: dict[str, Any] | None) -> None:
        if chat and "ephemeralDuration" in chat:
            self._chat_expiry_settings[jid] = {
                "durationSeconds": chat.get("ephemeralDuration"),
                "settingChangedAt": self._format_timestamp(chat.get("ephemeralSettingTimestamp")),
                "observedAt": datetime.now(timezone.utc).isoformat(), "source": "indexeddb-chat",
                "appliesRetroactively": False,
            }

    @classmethod
    def _consolidate_threads(cls, threads: list[dict[str, Any]], *, max_messages: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        by_id: dict[str, dict[str, Any]] = {}
        unresolved: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            if not thread.get("threadKey"):
                unresolved.append(dict(thread))
                continue
            key = str(thread["threadKey"]).strip()
            incoming = dict(thread)
            incoming["threadKey"] = key
            if key not in by_id:
                by_id[key] = incoming
                continue
            existing = by_id[key]
            old_messages = existing.get("messages") or existing.get("recentMessages") or []
            new_messages = incoming.get("messages") or incoming.get("recentMessages") or []
            old_diag, new_diag = existing.get("sourceDiagnostics") or {}, incoming.get("sourceDiagnostics") or {}
            old_complete = bool(old_diag.get("captureState")) and complete_live_capture(old_diag, max_messages)
            new_complete = bool(new_diag.get("captureState")) and complete_live_capture(new_diag, max_messages)
            old_at = cls._iso_timestamp_epoch(old_diag.get("captureState", {}).get("capturedAt")) or 0
            new_at = cls._iso_timestamp_epoch(new_diag.get("captureState", {}).get("capturedAt")) or 0
            use_new = (new_complete and (not old_complete or new_at >= old_at)) or (not old_complete and not new_complete and new_at >= old_at)
            preferred, fallback = (incoming, existing) if use_new else (existing, incoming)
            preferred_messages = preferred.get("messages") or preferred.get("recentMessages") or []
            fallback_messages = fallback.get("messages") or fallback.get("recentMessages") or []
            if old_complete or new_complete:
                authoritative_ids = {m.get("messageId") for m in preferred_messages}
                fallback_messages = [m for m in fallback_messages if m.get("messageId") in authoritative_ids]
            messages = cls._merge_recent_message_exports(
                preferred_messages, fallback_messages, max_messages=max_messages,
            )
            conflicts = cls._matching_message_conflicts(old_messages, new_messages)
            if old_complete or new_complete:
                preferred_latest = cls._exported_message_epoch(cls._latest_exported_message(messages) or {}) or 0
                for message in fallback.get("messages") or fallback.get("recentMessages") or []:
                    if message.get("messageId") not in authoritative_ids and (cls._exported_message_epoch(message) or 0) > preferred_latest:
                        # Evidence observed before the complete capture is allowed to expire.
                        fallback_at = cls._iso_timestamp_epoch((fallback.get("sourceDiagnostics") or {}).get("captureState", {}).get("capturedAt")) or 0
                        preferred_at = cls._iso_timestamp_epoch((preferred.get("sourceDiagnostics") or {}).get("captureState", {}).get("capturedAt")) or 0
                        if fallback_at > preferred_at:
                            conflicts.append({"code": "newer-evidence-outside-complete-capture", "messageId": message.get("messageId")})
            merged = {**fallback, **preferred}
            # Prefer a meaningful contact name over a formatted telephone alias.
            titles = [str(t.get("chatTitle") or "") for t in (preferred, fallback)]
            merged["chatTitle"] = next((title for title in titles if any(c.isalpha() for c in title) and '@' not in title), titles[0])
            for field in ("labelsRaw", "labelsNormalized"):
                values = sorted(set(existing.get(field, [])) | set(incoming.get(field, [])))
                merged[field] = [value for value in values if str(value).casefold() != "unlabeled"] or values
            participants = existing.get("participants", []) + incoming.get("participants", [])
            participant_map: dict[str, dict[str, Any]] = {}
            for participant in participants:
                identity = str(participant.get("phone") or participant.get("name") or "")
                current = participant_map.get(identity)
                if not current or not any(c.isalpha() for c in str(current.get("name") or "")):
                    participant_map[identity] = participant
            merged["participants"] = list(participant_map.values())
            for field in ("unread", "starred", "requiresResponse"):
                merged[field] = bool(existing.get(field) or incoming.get(field))
            sources = set(existing.get("sourceViews", [existing.get("sourceView") or "labeled/forced"]))
            sources.update(incoming.get("sourceViews", [incoming.get("sourceView") or "labeled/forced"]))
            merged["sourceViews"] = sorted(sources)
            if "all" in sources:
                merged["sourceView"] = "all"
            merged["messages"], merged["recentMessages"] = messages, list(messages)
            latest = messages[0] if messages else {}
            merged.update(lastMessageAt=latest.get("timestamp"), lastMessageText=latest.get("text") or "",
                          lastMessageDirection=latest.get("direction", "unknown"), lastMessageSender=latest.get("sender"))
            findings.append({"threadKey": key, "aliases": list(dict.fromkeys(titles)), "policy": "newest-complete-live-capture" if old_complete or new_complete else "message-id-union",
                             "previousMessageCount": len(old_messages), "incomingMessageCount": len(new_messages),
                             "mergedMessageCount": len(messages), "conflicts": conflicts})
            merged.setdefault("sourceDiagnostics", {})
            merged["sourceDiagnostics"] = {**merged["sourceDiagnostics"], "identityAliases": list(dict.fromkeys(titles)),
                                           "identityMergeConflicts": conflicts}
            by_id[key] = merged
        return list(by_id.values()) + unresolved, findings

    def _recover_missing_media(self, threads: list[dict[str, Any]], *, max_messages: int, attachments_dir: Path | None) -> list[dict[str, Any]]:
        if not self._attachment_policy.enabled or attachments_dir is None:
            return []
        results: list[dict[str, Any]] = []
        for thread in threads:
            messages = thread.get("messages", [])
            retryable = [attachment for message in messages for attachment in message.get("attachments", [])
                         if attachment.get("status") != "downloaded"
                         and attachment.get("skippedReason") == "download-retry-exhausted"]
            if not retryable or self._is_locked_chat(str(thread["threadKey"])):
                continue
            result: dict[str, Any] = {"chatTitle": thread["chatTitle"], "threadKey": thread["threadKey"],
                                      "missingBefore": len(retryable), "attempts": 1}
            started = time.monotonic()
            diagnostics = deepcopy(thread.get("sourceDiagnostics") or {})
            diagnostics["mediaRecoveryAttempted"] = True
            recovery_diagnostics: dict[str, Any] = {}
            self._opened_capture_details.pop(thread["threadKey"], None)
            try:
                opened = self._opened_chat_recent_messages_for_chat(
                    thread["chatTitle"], max_messages=max_messages,
                    attachments_dir=attachments_dir, thread_key=thread["threadKey"],
                )
                state = deepcopy(self._opened_capture_details.get(thread["threadKey"], {}))
                recovery_diagnostics = {"openedChatChecked": True, "captureState": state,
                                        "openedChatMessageCount": len(opened), "sourcesUsed": ["opened-chat"]}
                settled = state.get("isLoading") is False and not any(
                    state.get(flag) for flag in ("waitTimedOut", "timedOut", "timeout")
                )
                verified = state.get("identityVerified") is True
                unresolved = (state.get("domModelsResolved") is False
                              or (state.get("emptyEvidence") or {}).get("domModelsResolved") is False)
                recovery_incomplete = bool(state.get("captureIncomplete")) or not settled or not verified or unresolved or not opened
                diagnostics["captureIncomplete"] = bool(diagnostics.get("captureIncomplete") or recovery_incomplete)
                if recovery_incomplete:
                    diagnostics.setdefault("issues", []).append({"code": "media-recovery-capture-incomplete"})
                # This pass repairs bytes only; it cannot establish new history membership or clear content failures.
                opened_by_id = {message.get("messageId"): message for message in opened if message.get("messageId")}
                repaired = []
                for message in messages:
                    incoming = opened_by_id.get(message.get("messageId"), {})
                    repaired_message = dict(message)
                    if message.get("attachments"):
                        expected_ids = {attachment.get("attachmentId") for attachment in message["attachments"]}
                        repaired_message["attachments"] = self._merge_attachment_exports(
                            message["attachments"],
                            [attachment for attachment in incoming.get("attachments", [])
                             if attachment.get("attachmentId") in expected_ids],
                        )
                    repaired.append(repaired_message)
                thread["messages"] = repaired
                thread["recentMessages"] = list(repaired)
                if not opened:
                    result["error"] = "No exportable messages were loaded during media recovery."
            except (RuntimeError, TimeoutError, ValueError) as exc:
                result["error"] = str(exc)[:2000]
                state = deepcopy(self._opened_capture_details.get(thread["threadKey"], {}))
                recovery_diagnostics = {"openedChatChecked": False, "captureState": state,
                                        "errorType": type(exc).__name__}
                if isinstance(exc, (TimeoutError, ValueError)) or any(
                    state.get(flag) for flag in ("isLoading", "waitTimedOut", "timedOut", "timeout")
                ):
                    diagnostics["captureIncomplete"] = True
                diagnostics.setdefault("issues", []).append({"code": "media-recovery-open-failed"})
            result["elapsedSeconds"] = round(time.monotonic() - started, 3)
            recovery_diagnostics["elapsedSeconds"] = result["elapsedSeconds"]
            diagnostics["mediaRecoveryCapture"] = recovery_diagnostics
            thread["sourceDiagnostics"] = diagnostics
            recovered_ids = {attachment.get("attachmentId")
                             for message in thread["messages"] for attachment in message.get("attachments", [])
                             if attachment.get("status") == "downloaded"}
            result["recovered"] = sum(attachment.get("attachmentId") in recovered_ids for attachment in retryable)
            result["remaining"] = len(retryable) - result["recovered"]
            results.append(result)
        return results

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
                issues.append({"code": "opened-chat-check-failed", "detail": opened_chat_error[:2000]})
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

        sources: list[str] = []
        if indexeddb_messages:
            sources.append("indexeddb")
        if opened_chat_messages:
            sources.append("opened-chat")
        return {
            "openedChatChecked": opened_chat_checked and "requires a DevTools-backed Chrome session" not in (opened_chat_error or ""),
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
            if indexed_text_available and opened_text_available and indexed.get("text") != opened.get("text"):
                issues.append({"code": "matching-message-text-conflict", "messageId": message_id,
                               "indexedDbTextSHA256": hashlib.sha256(str(indexed["text"]).encode()).hexdigest(),
                               "openedChatTextSHA256": hashlib.sha256(str(opened["text"]).encode()).hexdigest()})
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
            if key == "attachments":
                merged[key] = WhatsAppCollector._merge_attachment_exports(merged.get(key), value)
                continue
            merged[key] = value
        if "textAvailable" in merged:
            merged["textAvailable"] = bool(str(merged.get("text") or "").strip())
        if "contentAvailable" in merged:
            merged["contentAvailable"] = bool(str(merged.get("text") or "").strip() or merged.get("attachments"))
        return merged

    @staticmethod
    def _merge_attachment_exports(existing: Any, incoming: Any) -> list[dict[str, Any]]:
        combined: dict[str, dict[str, Any]] = {}
        anonymous = 0
        for attachment in [*(existing or []), *(incoming or [])]:
            if not isinstance(attachment, dict):
                continue
            key = str(attachment.get("attachmentId") or "").strip()
            if not key:
                anonymous += 1
                key = f"anonymous:{anonymous}:{attachment.get('fileName') or ''}:{attachment.get('kind') or ''}"
            current = combined.get(key)
            if current is None:
                combined[key] = dict(attachment)
                continue
            current_score = (
                int(current.get("status") == "downloaded"),
                int(current.get("verified") is True),
                int(bool(current.get("localPath") or current.get("relativePath"))),
            )
            incoming_score = (
                int(attachment.get("status") == "downloaded"),
                int(attachment.get("verified") is True),
                int(bool(attachment.get("localPath") or attachment.get("relativePath"))),
            )
            if incoming_score >= current_score:
                preferred, fallback = attachment, current
            else:
                preferred, fallback = current, attachment
            combined[key] = {**fallback, **{field: value for field, value in preferred.items() if value not in (None, "")}}
        for attachment in combined.values():
            if attachment.get("status") == "downloaded":
                attachment.pop("skippedReason", None)
                attachment.pop("note", None)
        return list(combined.values())

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
                "contentAvailable": bool(message.text_available or message.attachments),
                "messageType": message.message_type,
                "subtype": message.subtype,
            }
            if message.attachments:
                item["attachments"] = [self._serialize_attachment(attachment) for attachment in message.attachments]
            if message.expiry:
                item["expiry"] = message.expiry
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
        if attachment.sha256:
            payload["sha256"] = attachment.sha256
        if attachment.download_method:
            payload["downloadMethod"] = attachment.download_method
        payload["verified"] = attachment.verified
        payload["downloadAttempts"] = attachment.download_attempts
        if attachment.detection_source:
            payload["detectionSource"] = attachment.detection_source
        if attachment.source_message_id:
            payload["sourceMessageId"] = attachment.source_message_id
        if attachment.skipped_reason:
            payload["skippedReason"] = attachment.skipped_reason
        if attachment.note:
            payload["note"] = attachment.note
        return payload

    @staticmethod
    def _attachment_summary(payload: dict[str, Any]) -> dict[str, int]:
        detected = 0
        downloaded = 0
        not_downloaded = 0
        downloaded_bytes = 0
        retried = 0
        for thread in payload.get("threads", []):
            if not isinstance(thread, dict):
                continue
            messages = thread.get("recentMessages") or thread.get("messages") or []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                for attachment in message.get("attachments") or []:
                    if not isinstance(attachment, dict):
                        continue
                    detected += 1
                    if attachment.get("status") == "downloaded":
                        downloaded += 1
                        downloaded_bytes += int(attachment.get("sizeBytes") or 0)
                    else:
                        not_downloaded += 1
                    if int(attachment.get("downloadAttempts") or 0) > 1:
                        retried += 1
        return {
            "detected": detected,
            "downloaded": downloaded,
            "notDownloaded": not_downloaded,
            "downloadedBytes": downloaded_bytes,
            "retried": retried,
        }

    def _chat_open_identity(self, chat_name: str, thread_key: str | None) -> tuple[list[str], list[str]]:
        candidates = [chat_name]
        jids = self._message_jid_aliases(thread_key)
        if jids and hasattr(self.session, "run_async_json"):
            try:
                contact_rows = self._idb_get_many("contact", [thread_key])
                chat_rows = self._idb_get_many("chat", [thread_key])
            except (RuntimeError, TimeoutError, ValueError):
                contact_rows, chat_rows = [], []
            for row in [*contact_rows, *chat_rows]:
                value = row.get("value") or {}
                candidates.extend(value.get(key) for key in ("name", "shortName", "formattedTitle", "phoneNumber"))
                for key in ("id", "phoneNumber", "historyChatId", "accountLid"):
                    jids.extend(self._message_jid_aliases(value.get(key)))
        # Only phone-address JIDs are phone numbers; @lid identifiers must never become search numbers.
        candidates.extend(jid for jid in jids if jid.endswith(("@c.us", "@s.whatsapp.net")))
        searchable: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            candidate = candidate.strip()
            if candidate.endswith(("@c.us", "@s.whatsapp.net")):
                candidate = "+" + candidate.split("@", 1)[0]
            if candidate.endswith(("@lid", "@g.us", "@broadcast")) or re.search(r"[\u2219\u2022\u00b7*]{2,}", candidate):
                continue
            normalized = self._chat_lookup_name(candidate)
            if normalized and normalized not in seen:
                searchable.append(candidate)
                seen.add(normalized)
        return searchable[:4], list(dict.fromkeys(jids))

    @staticmethod
    def _chat_lookup_name(name: str) -> str:
        normalized = unicodedata.normalize("NFKC", name).casefold()
        normalized = re.sub(r"[\u200e\u200f]", "", normalized).strip()
        if re.fullmatch(r"[+\d\s().-]+", normalized) and len(re.sub(r"\D", "", normalized)) >= 7:
            return re.sub(r"\D", "", normalized)
        return re.sub(r"\s+", " ", normalized)

    def _opened_chat_matches_identity(
        self, payload: dict[str, Any], *, candidates: list[str], expected_jids: list[str]
    ) -> bool:
        opened_jid = payload.get("openedChatJid")
        if opened_jid and expected_jids and opened_jid not in expected_jids:
            return False
        message_jids = {
            jid for item in payload.get("messages", []) if isinstance(item, dict)
            if (jid := self._message_jid_from_storage_key(item.get("id")))
        }
        if message_jids and expected_jids:
            return message_jids.issubset(set(expected_jids))
        if message_jids and opened_jid and not message_jids.issubset(set(self._message_jid_aliases(opened_jid))):
            return False
        if opened_jid and expected_jids:
            return True
        opened_title = self._chat_lookup_name(str(payload.get("openedChatTitle") or ""))
        return bool(opened_title) and any(opened_title == self._chat_lookup_name(name) for name in candidates)

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
        candidates, expected_jids = self._chat_open_identity(chat_name, thread_key)
        errors: list[str] = []
        payload: dict[str, Any] | None = None
        for candidate in candidates:
            for use_search in (False, True):
                try:
                    if use_search:
                        if not hasattr(self.session, "open_chat_via_search"):
                            break
                        if expected_jids:
                            self.session.open_chat_via_search(candidate, expected_jids=expected_jids)
                        else:
                            self.session.open_chat_via_search(candidate)
                    else:
                        self.session.click_point(self._chat_row_click_point_expression(candidate))
                    store = self._attachment_store
                    if store is None and attachments_dir is not None:
                        store = AttachmentStore(attachments_dir, policy=self._attachment_policy)
                    hint_ids = getattr(self, "_attachment_hint_message_ids", {}).get(thread_key or chat_name, [])
                    verified_media = store.verified_media(
                        thread_key=thread_key or chat_name, message_ids=hint_ids,
                    ) if store else []
                    captured = self.session.run_json(self._opened_chat_recent_messages_js(
                        max_messages=max_messages, verified_media=verified_media, expected_jids=expected_jids,
                        expected_names=candidates,
                    ))
                    if not self._opened_chat_matches_identity(captured, candidates=candidates, expected_jids=expected_jids):
                        raise RuntimeError(f"Opened chat identity did not match {chat_name!r} (opened title: {captured.get('openedChatTitle')!r})")
                    payload = captured
                    break
                except RuntimeError as exc:
                    route = "sidebar-search" if use_search else "visible-list"
                    errors.append(f"{candidate!r} ({route}): {exc}")
            if payload is not None:
                break
        if payload is None:
            detail = "; ".join(errors) or "No usable contact name or full phone number was available."
            raise RuntimeError(f"Could not capture WhatsApp chat {chat_name!r}. {detail}")
        state = dict(payload.get("captureState") or {})
        self._normalize_capture_timing(state)
        resolved_jid = payload.get("openedChatJid")
        message_jids = {self._message_jid_from_storage_key(item.get("id"))
                        for item in payload.get("messages", []) if isinstance(item, dict)}
        observed_identity = bool(resolved_jid and message_jids and None not in message_jids
                                 and message_jids.issubset(set(self._message_jid_aliases(resolved_jid)))
                                 and state.get("emptyEvidence", {}).get("identityStable")
                                 and state.get("emptyEvidence", {}).get("modelAvailable"))
        state["identityVerified"] = bool(resolved_jid and (expected_jids or observed_identity))
        state["resolvedThreadKey"] = payload.get("openedChatJid")
        self._opened_capture_details[thread_key or chat_name] = state
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
                allow_ui_download=True,
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
                "contentAvailable": bool(text or attachments),
                "messageType": item.get("type") or "unknown",
                "subtype": item.get("subtype"),
            }
            if attachments:
                opened_message["attachments"] = [self._serialize_attachment(attachment) for attachment in attachments]
            expiry = expiry_metadata(item.get("expiryEvidence") or item, source="live-message-model")
            if expiry:
                opened_message["expiry"] = expiry
            opened_messages.append(opened_message)
        return opened_messages

    @staticmethod
    def _normalize_capture_timing(state: dict[str, Any]) -> None:
        # JavaScript JSON drops the decimal for whole durations; keep the Python export contract stable.
        if type(state.get("identityWaitMs")) in (int, float):
            state["identityWaitMs"] = float(state["identityWaitMs"])
        for wait in state.get("stateWaits") or []:
            if isinstance(wait, dict) and type(wait.get("elapsedMs")) in (int, float):
                wait["elapsedMs"] = float(wait["elapsedMs"])

    @staticmethod
    def _chat_row_click_point_expression(chat_name: str) -> str:
        return f'''(() => {{
            const normalize = text => String(text || '').normalize('NFKC').replace(/[\\u200e\\u200f]/g, '').replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
            const wanted = normalize({json.dumps(chat_name)});
            const rows = [...document.querySelectorAll('#pane-side [role="row"], #pane-side [data-testid^="list-item-"]')]
                .filter(row => row.offsetParent !== null);
            const matches = rows.filter(row => {{
                const title = row.querySelector('[data-testid="cell-frame-title"] [title], [title]');
                return normalize(title?.getAttribute('title') || String(row.innerText || '').split('\\n').find(text => text.trim())) === wanted;
            }});
            if (matches.length !== 1) return null;
            const row = matches[0];
            row.scrollIntoView({{block:'center'}});
            const rect = row.getBoundingClientRect();
            return {{x:rect.left + rect.width / 2, y:rect.top + rect.height / 2}};
        }})()'''

    @staticmethod
    def _opened_chat_recent_messages_js(
        *, max_messages: int, verified_media: list[dict[str, Any]] | None = None,
        expected_jids: list[str] | None = None,
        expected_names: list[str] | None = None,
    ) -> str:
        return f'''(async () => {{
            {CHAT_IDENTITY_HELPERS_JS}
            {MESSAGE_EXPIRY_HELPERS_JS}
            {MEDIA_MESSAGE_HELPERS_JS}
            const maxMessages = {int(max_messages)};
            const maxInlineBytes = 8000000;
            const verifiedMedia = {json.dumps(verified_media or [])};
            const maxPasses = Math.max(12, Math.min(160, maxMessages * 4 + 8));
            const captureDeadline = performance.now() + 45000;
            const mediaTypes = new Set(['image', 'document', 'video', 'ptt', 'audio', 'sticker']);
            const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const expectedJids = new Set({json.dumps(list(expected_jids or []))});
            const normalizeTitle = value => {{
                const text = String(value || '').normalize('NFKC').replace(/[\\u200e\\u200f]/g, '').replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                return /^[+\\d\\s().-]+$/.test(text) && text.replace(/\\D/g, '').length >= 7 ? text.replace(/\\D/g, '') : text;
            }};
            const expectedNames = new Set({json.dumps(list(expected_names or []))}.map(normalizeTitle));
            let identityWaitMs = 0;
            if (expectedJids.size || expectedNames.size) {{
                const started = performance.now();
                const deadline = started + 3000;
                let matched = false;
                for (let poll = 0; poll <= 30; poll += 1) {{
                    if (performance.now() >= deadline) break;
                    const jid = waCollectorChatId(document.querySelector('#main header'));
                    const ready = expectedJids.size ? expectedJids.has(jid) : Boolean(jid && expectedNames.has(normalizeTitle(waCollectorChatTitle())));
                    if (ready) {{ matched = true; break; }}
                    if (poll < 30) await wait(Math.min(100, Math.max(0, deadline - performance.now())));
                }}
                identityWaitMs = performance.now() - started;
                if (!matched) {{
                    throw new Error(`The expected opened chat did not become ready before capture (${{identityWaitMs}} ms).`);
                }}
            }}
            const openedChatTitle = waCollectorChatTitle();
            const openedChatJid = waCollectorChatId(document.querySelector('#main header'));
            if (expectedJids.size && !expectedJids.has(openedChatJid)) {{
                throw new Error('The opened chat changed during message capture.');
            }}
            let chatModel = null;
            const refreshChatModel = () => {{
                const previousModel = chatModel;
                chatModel = null;
                const header = document.querySelector('#main header');
                if (waCollectorChatId(header) !== openedChatJid) {{
                    throw new Error('The opened chat changed during message capture.');
                }}
                const headerFiber = header && Object.keys(header).find(key => key.startsWith('__reactFiber$'));
                for (let fiber = headerFiber ? header[headerFiber] : null, depth = 0; fiber && depth < 32; fiber = fiber.return, depth++) {{
                    const candidate = fiber.memoizedProps?.chat || fiber.memoizedProps?.chatModel;
                    const id = candidate?.id?._serialized || candidate?.id;
                    if (candidate?.msgs && id === openedChatJid) {{ chatModel = candidate; break; }}
                }}
                if (!chatModel && openedChatJid) {{
                    try {{
                        const module = window.require('WAWebChatCollection');
                        const candidate = (module.ChatCollection || module.Chat)?.get(openedChatJid) || null;
                        if ((candidate?.id?._serialized || candidate?.id) === openedChatJid) chatModel = candidate;
                    }} catch (_) {{}}
                }}
                return previousModel === chatModel;
            }};
            const normalizeJid = (value) => {{
                if (!value) return null;
                if (typeof value === 'string') return value;
                if (typeof value === 'object' && value._serialized) return value._serialized;
                return null;
            }};
            const messageId = (msg) => {{
                const value = msg && msg.id;
                if (!value) return null;
                if (typeof value === 'string') return value;
                return value._serialized || value.$1 || String(value);
            }};
            const readableText = value => {{
                if (typeof value !== 'string' || !value.trim()) return false;
                const text = value.trim();
                if (/^data:(image|video|application)\\//.test(text)) return false;
                if (text.length < 160) return true;
                if (/^(\\/9j\\/|iVBORw0KGgo|R0lGOD|UklGR|AAAAIGZ0eXB|JVBERi0)/.test(text)) return false;
                const compact = text.replace(/\\s/g, '');
                if (compact.length >= 160 && compact.length % 4 === 0 && /^[A-Za-z0-9+/]+={{0,2}}$/.test(compact)) {{
                    try {{
                        const decoded = atob(compact.slice(0, 512));
                        if (/^(\\xFF\\xD8\\xFF|\\x89PNG|GIF8|RIFF|%PDF|\\x00\\x00\\x00)/.test(decoded)) return false;
                    }} catch (_) {{}}
                }}
                return true;
            }};
            const locationText = msg => msg?.type === 'location'
                && typeof msg.lat === 'number' && Number.isFinite(msg.lat) && Math.abs(msg.lat) <= 90
                && typeof msg.lng === 'number' && Number.isFinite(msg.lng) && Math.abs(msg.lng) <= 180
                ? `Location: ${{msg.lat}}, ${{msg.lng}} (https://maps.google.com/?q=${{msg.lat}},${{msg.lng}})` : null;
            const hasExportableContent = (msg) => {{
                if (!msg || typeof msg !== 'object') return false;
                const type = String(msg.type || '').trim().toLowerCase();
                const subtype = String(msg.subtype || '').trim().toLowerCase();
                if ({json.dumps(sorted([list(kind) for kind in NONCONTENT_CONTROL_MESSAGE_KINDS]))}
                    .some(([controlType, controlSubtype]) => type === controlType && subtype === controlSubtype)) return false;
                if (mediaTypes.has(type) || type === 'album') return true;
                return Boolean(locationText(msg)) || [msg.body, msg.caption, msg.text, msg.matchedText].some(readableText);
            }};
            const bestFileName = (msg, kind, index) => {{
                const raw = msg && typeof msg === 'object' ? (msg.filename || msg.fileName || msg.documentTitle || msg.title) : null;
                if (typeof raw === 'string' && raw.trim()) return raw.trim();
                const ext = kind === 'image' ? '.jpg' : kind === 'video' ? '.mp4' : kind === 'audio' ? '.ogg' : kind === 'sticker' ? '.webp' : '.bin';
                return `attachment-${{index + 1}}${{ext}}`;
            }};
            const blobToDataURL = (blob) => new Promise((resolve, reject) => {{
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = () => reject(reader.error);
                reader.readAsDataURL(blob);
            }});
            const fetchAttachment = async (src) => {{
                if (!src || (!src.startsWith('blob:') && !src.startsWith('data:'))) return {{status: 'notDownloaded', skippedReason: 'not-fetchable-from-dom'}};
                try {{
                    const blob = await (await fetch(src)).blob();
                    if (blob.size > maxInlineBytes) return {{status: 'notDownloaded', sizeBytes: blob.size, skippedReason: 'dom-transfer-too-large'}};
                    return {{status: 'downloadable', sizeBytes: blob.size, mimeType: blob.type || null, dataUrl: await blobToDataURL(blob)}};
                }} catch (error) {{
                    return {{status: 'notDownloaded', skippedReason: 'browser-fetch-failed', note: String(error)}};
                }}
            }};
            const fetchMessageAttachment = (src, msg) => {{
                const size = Number(msg.size || msg.fileSize);
                const verified = Number.isSafeInteger(size) && size > 0 && typeof msg.filehash === 'string'
                    && /^[A-Za-z0-9+/]{{43}}=$/.test(msg.filehash) && verifiedMedia.some(item =>
                        item.sourceMessageId === messageId(msg) && item.filehash === msg.filehash && item.sizeBytes === size);
                if (verified) return {{status: 'notDownloaded', skippedReason: 'verified-local-media'}};
                return src ? fetchAttachment(src) : {{status: 'notDownloaded', skippedReason: 'no-full-media-dom-source'}};
            }};
            const mediaChildren = msg => waCollectorMediaChildren(msg,
                chatModel?.msgs?.getModelsArray?.() || chatModel?.msgs?.models || chatModel?.msgs?._models || []);
            const domAttachments = async (container, msg) => {{
                const msgType = String(msg && msg.type || '').toLowerCase();
                const kind = msgType === 'ptt' ? 'audio' : msgType;
                if (mediaTypes.has(msgType)) {{
                    let nodes = [];
                    if (['image', 'sticker'].includes(msgType)) nodes = [...(container?.querySelectorAll('img[src^="blob:"], img[src^="data:"]') || [])];
                    if (msgType === 'video') nodes = [...(container?.querySelectorAll('video[src^="blob:"], video[src^="data:"]') || [])];
                    const node = nodes[0] || null;
                    const src = node ? (node.getAttribute('src') || '') : '';
                    const fetched = await fetchMessageAttachment(src, msg);
                    return [{{
                        kind,
                        mimeType: (typeof msg.mimetype === 'string' && msg.mimetype) || fetched.mimeType || null,
                        fileName: bestFileName(msg, kind, 0),
                        sizeBytes: Number.isFinite(Number(msg.size || msg.fileSize)) ? Number(msg.size || msg.fileSize) : (fetched.sizeBytes ?? null),
                        status: fetched.status,
                        skippedReason: fetched.skippedReason || null,
                        note: fetched.note || null,
                        dataUrl: fetched.dataUrl || null,
                        filehash: typeof msg.filehash === 'string' ? msg.filehash : null,
                        encFilehash: typeof msg.encFilehash === 'string' ? msg.encFilehash : null,
                        directPath: typeof msg.directPath === 'string' ? msg.directPath : null,
                        detectionSource: 'live-message-model'
                    }}];
                }}
                if (msgType !== 'album') return [];
                const nodes = [...container.querySelectorAll('img[src^="blob:"], img[src^="data:"], video[src^="blob:"], video[src^="data:"]')];
                const items = [];
                const children = mediaChildren(msg);
                if (children.length) {{
                    for (let index = 0; index < children.length; index += 1) {{
                        const child = children[index];
                        const childType = String(child.type || '').toLowerCase();
                        const albumKind = childType === 'ptt' ? 'audio' : childType;
                        const node = nodes[index] || null;
                        const src = node ? (node.getAttribute('src') || '') : '';
                        const fetched = await fetchMessageAttachment(src, child);
                        items.push({{
                            kind: albumKind,
                            mimeType: (typeof child.mimetype === 'string' && child.mimetype) || fetched.mimeType || null,
                            fileName: bestFileName(child, albumKind, index),
                            sizeBytes: Number.isFinite(Number(child.size || child.fileSize)) ? Number(child.size || child.fileSize) : (fetched.sizeBytes ?? null),
                            status: fetched.status,
                            skippedReason: fetched.skippedReason || null,
                            note: fetched.note || null,
                            dataUrl: fetched.dataUrl || null,
                            filehash: typeof child.filehash === 'string' ? child.filehash : null,
                            encFilehash: typeof child.encFilehash === 'string' ? child.encFilehash : null,
                            directPath: typeof child.directPath === 'string' ? child.directPath : null,
                            sourceMessageId: messageId(child),
                            detectionSource: 'live-album-child-model'
                        }});
                    }}
                    return items;
                }}
                for (let index = 0; index < nodes.length; index += 1) {{
                    const node = nodes[index];
                    const src = node.getAttribute('src') || '';
                    const albumKind = node.tagName.toLowerCase() === 'video' ? 'video' : 'image';
                    const fetched = await fetchAttachment(src);
                    items.push({{
                        kind: albumKind,
                        mimeType: fetched.mimeType || (node.getAttribute('type') || null),
                        fileName: bestFileName(msg, albumKind, index),
                        sizeBytes: fetched.sizeBytes ?? null,
                        status: fetched.status,
                        skippedReason: fetched.skippedReason || null,
                        note: fetched.note || null,
                        dataUrl: fetched.dataUrl || null,
                        sourceMessageId: messageId(msg),
                        detectionSource: 'album-dom-fallback'
                    }});
                }}
                return items;
            }};
            const summarizeMsg = async (msg, container) => {{
                if (!msg || typeof msg !== 'object') return null;
                const attachments = await domAttachments(container, msg);
                return {{
                    id: messageId(msg),
                    t: msg.t ?? null,
                    type: msg.type || null,
                    subtype: msg.subtype || null,
                    body: typeof msg.body === 'string' ? msg.body : null,
                    caption: typeof msg.caption === 'string' ? msg.caption : null,
                    text: locationText(msg) || (typeof msg.text === 'string' ? msg.text : null),
                    lat: msg.type === 'location' ? msg.lat : null,
                    lng: msg.type === 'location' ? msg.lng : null,
                    matchedText: typeof msg.matchedText === 'string' ? msg.matchedText : null,
                    mimetype: typeof msg.mimetype === 'string' ? msg.mimetype : null,
                    fileName: typeof msg.fileName === 'string' ? msg.fileName : (typeof msg.filename === 'string' ? msg.filename : null),
                    size: Number.isFinite(Number(msg.size || msg.fileSize)) ? Number(msg.size || msg.fileSize) : null,
                    filehash: typeof msg.filehash === 'string' ? msg.filehash : null,
                    encFilehash: typeof msg.encFilehash === 'string' ? msg.encFilehash : null,
                    directPath: typeof msg.directPath === 'string' ? msg.directPath : null,
                    from: normalizeJid(msg.from),
                    to: normalizeJid(msg.to),
                    notifyName: typeof msg.notifyName === 'string' ? msg.notifyName : null,
                    expiryEvidence: {{...waCollectorExpiry(msg), t: msg.t}},
                    attachments
                }};
            }};
            const visibleMessages = () => {{
                const containers = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
                const messages = containers.map((container) => {{
                    const fiberKey = Object.keys(container).find(k => k.startsWith('__reactFiber$'));
                    let fiber = fiberKey ? container[fiberKey] : null;
                    while (fiber) {{
                        const props = fiber.memoizedProps;
                        if (props && typeof props === 'object' && props.msg) {{
                            return {{msg: props.msg, container}};
                        }}
                        fiber = fiber.return;
                    }}
                    return null;
                }});
                return messages.filter(item => item && item.msg && item.msg.id);
            }};
            const captureMessages = () => {{
                const visible = visibleMessages();
                const items = new Map(visible.map(item => [messageId(item.msg), item]));
                for (const item of visible) {{
                    if (String(item.msg.type || '').toLowerCase() === 'album') continue;
                    for (const child of mediaChildren(item.msg)) {{
                        // Keep real media messages separate, without borrowing the parent's image bytes.
                        if (!items.has(messageId(child))) items.set(messageId(child), {{msg: child, container: null}});
                    }}
                }}
                return [...items.values()];
            }};
            const scroller = () => document.querySelector('#main [data-testid="conversation-panel-messages"]')
                || [...document.querySelectorAll('#main *')].find(el => el.scrollHeight > el.clientHeight + 40)
                || null;
            const pollMs = 100;
            const stableEmptyMs = 2500;
            let emptySince = null;
            let emptySamples = 0;
            let emptyResets = 0;
            let observedContent = false;
            let waitTimedOut = false;
            let stableEmptyProof = false;
            let previousCollection = null;
            let previousLoadState = null;
            let disconnectedObserved = false;
            const unresolvedContainers = new Set();
            const connectionState = () => {{
                const signals = [];
                const online = typeof navigator !== 'undefined' && navigator.onLine === true;
                if (!online) signals.push('browser-offline-or-unknown');
                const alerts = document.querySelectorAll('[role="alert"], [data-testid="alert-computer"], [data-testid="alert-phone"], [data-testid="connection-status"]');
                for (const alert of alerts) {{
                    if (/offline|disconnected|connecting|reconnecting|not connected|no internet|waiting for network|trying to reach/i.test(alert.textContent || ''))
                        signals.push('connection-alert');
                }}
                for (const [name, key, ready] of [['WAWebSocketModel', 'Socket', 'CONNECTED'], ['WAWebStreamModel', 'Stream', 'CONNECTED']]) {{
                    try {{
                        const module = window.require(name);
                        const state = module?.[key]?.state;
                        if (typeof state === 'string' && state.toUpperCase() !== ready) signals.push(`${{key}}:${{state}}`);
                    }} catch (_) {{}}
                }}
                if (signals.length) disconnectedObserved = true;
                return {{online, connected: signals.length === 0, signals}};
            }};
            const sampleState = () => {{
                const sameModel = refreshChatModel();
                const connection = connectionState();
                const loadState = chatModel?.msgs?.msgLoadState;
                const sameContext = previousCollection === chatModel?.msgs && previousLoadState === loadState;
                previousCollection = chatModel?.msgs;
                previousLoadState = loadState;
                const models = chatModel?.msgs?.getModelsArray?.() || chatModel?.msgs?.models || chatModel?.msgs?._models;
                const modelExportableCount = Array.isArray(models) ? models.filter(hasExportableContent).length : null;
                const visible = visibleMessages();
                const containers = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
                const resolvedContainers = new Set(visible.map(item => item.container));
                for (const container of containers) {{
                    if (resolvedContainers.has(container)) unresolvedContainers.delete(container);
                    else unresolvedContainers.add(container);
                }}
                const domCount = containers.length;
                const domExportableCount = visible.filter(item => hasExportableContent(item.msg)).length;
                const isLoading = Boolean(document.querySelector('#main [role="progressbar"], #main [aria-busy="true"]'))
                    || Boolean(chatModel?.pendingInitialLoading || loadState?.isLoadingEarlierMsgs
                        || loadState?.isLoadingRecentMsgs || loadState?.isLoadingAroundMsgs || loadState?.isRepairingMsgHistory);
                const contextLoaded = loadState?.contextLoaded === true;
                const explicitHistoryStart = loadState?.noEarlierMsgs === true || chatModel?.noEarlierMsgs === true;
                observedContent ||= domExportableCount > 0 || modelExportableCount > 0;
                const eligible = Boolean(openedChatJid && chatModel && scroller() && contextLoaded && !isLoading && connection.connected
                    && modelExportableCount === 0 && domExportableCount === 0 && domCount === visible.length && !observedContent);
                if (!eligible || !sameModel || !sameContext) {{
                    if (emptySince !== null) emptyResets += 1;
                    emptySince = null;
                    emptySamples = 0;
                }}
                if (eligible) {{
                    emptySince ??= performance.now();
                    emptySamples += 1;
                }}
                stableEmptyProof = eligible && emptySamples >= 3 && performance.now() - emptySince >= stableEmptyMs;
                return {{isLoading, connection, contextLoaded, explicitHistoryStart, modelExportableCount, domExportableCount,
                    domContainerCount: domCount, domResolvedCount: visible.length, domUnresolvedCount: domCount - visible.length,
                    modelExportableIds: Array.isArray(models) ? models.filter(hasExportableContent).map(messageId) : null,
                    modelExportableMessages: Array.isArray(models) ? models.filter(hasExportableContent) : null,
                    domModelsResolved: domCount === visible.length,
                    signature: JSON.stringify([visible.map(item => messageId(item.msg)), Array.isArray(models) ? models.map(messageId) : null,
                        scroller()?.scrollHeight, contextLoaded, isLoading, connection.connected])}};
            }};
            // Poll for settled state, but never let a quiet DOM override an active loader.
            const stateWaits = [];
            const waitForState = async (budgetMs, allowEmptyProof = false) => {{
                const started = performance.now();
                const deadline = started + budgetMs;
                let signature = null;
                let quietSince = started;
                // The iteration limit is only a fail-closed guard for a frozen clock.
                for (let poll = 0; poll <= Math.ceil(budgetMs / pollMs); poll += 1) {{
                    if (performance.now() >= deadline) break;
                    const state = sampleState();
                    if (performance.now() >= deadline) break;
                    if (state.signature !== signature || state.isLoading) {{
                        signature = state.signature;
                        quietSince = performance.now();
                    }}
                    const settled = !state.isLoading && state.connection.connected && performance.now() - quietSince >= 300;
                    if (settled && ((!allowEmptyProof && state.contextLoaded)
                        || state.domExportableCount > 0 || (state.contextLoaded && state.explicitHistoryStart)
                        || stableEmptyProof)) {{
                        stateWaits.push({{budgetMs, elapsedMs: performance.now() - started, timedOut: false}});
                        return true;
                    }}
                    if (poll < Math.ceil(budgetMs / pollMs)) await wait(Math.min(pollMs, Math.max(0, deadline - performance.now())));
                }}
                stateWaits.push({{budgetMs, elapsedMs: performance.now() - started, timedOut: true}});
                waitTimedOut = true;
                return false;
            }};
            await waitForState(6000, true);
            const initialPanel = scroller();
            if (initialPanel) {{
                const beforeBottom = initialPanel.scrollTop;
                initialPanel.scrollTop = initialPanel.scrollHeight;
                if (initialPanel.scrollTop !== beforeBottom) await waitForState(3000);
            }}
            const seen = new Map();
            let stableTopPasses = 0;
            let historyStartReached = false;
            for (let pass = 0; pass < maxPasses; pass += 1) {{
                if (performance.now() >= captureDeadline) {{ waitTimedOut = true; break; }}
                for (const item of captureMessages()) {{
                    const key = messageId(item.msg);
                    if (!key) continue;
                    seen.set(key, item);
                }}
                const exportableSeen = [...seen.values()].filter(item => hasExportableContent(item.msg)).length;
                if (exportableSeen >= maxMessages) break;
                if (stableEmptyProof && !waitTimedOut) break;
                const state = sampleState();
                // Exhausted model history is sufficient only when every exportable ID was captured.
                // Matching counts cannot establish coverage of a virtualized conversation.
                if (openedChatJid && state.explicitHistoryStart && state.contextLoaded && !state.isLoading
                    && state.connection.connected && !waitTimedOut && state.domModelsResolved
                    && state.modelExportableIds !== null
                    && state.modelExportableIds.every(id => id && seen.has(id) && hasExportableContent(seen.get(id).msg))
                    && (state.modelExportableIds.length > 0 || (!observedContent && exportableSeen === 0 && state.domExportableCount === 0))) {{
                    historyStartReached = true;
                    break;
                }}
                const panel = scroller();
                if (!panel) break;
                const beforeTop = panel.scrollTop;
                const beforeHeight = panel.scrollHeight;
                panel.scrollTop = Math.max(0, beforeTop - Math.max(1, panel.clientHeight * 0.8));
                const settled = await waitForState(3000);
                if (!settled) break;
                if (Math.abs(panel.scrollTop - beforeTop) < 2 && panel.scrollHeight === beforeHeight) {{
                    stableTopPasses += 1;
                    if (stableTopPasses >= 3) {{ historyStartReached = panel.scrollTop < 2; break; }}
                }} else stableTopPasses = 0;
            }}
            // Include content delivered by the final scroll, including a timed-out wait.
            for (const item of captureMessages()) seen.set(messageId(item.msg), item);
            const selected = [...seen.values()]
                .filter(item => item && item.msg && messageId(item.msg) && hasExportableContent(item.msg))
                .sort((a, b) => (Number(b.msg.t) || 0) - (Number(a.msg.t) || 0))
                .slice(0, maxMessages);
            const selectedMediaMessages = new Map();
            for (const item of selected) {{
                selectedMediaMessages.set(messageId(item.msg), item.msg);
                for (const child of mediaChildren(item.msg)) selectedMediaMessages.set(messageId(child), child);
            }}
            window.__waCollectorSelectedMediaMessages = selectedMediaMessages;
            window.__waCollectorSelectedMediaChatJid = openedChatJid;
            const messages = (await Promise.all(selected.map(item => summarizeMsg(item.msg, item.container)))).filter(Boolean);
            if (openedChatJid && waCollectorChatId(document.querySelector('#main header')) !== openedChatJid) {{
                throw new Error('The opened chat changed during message capture.');
            }}
            const finalState = sampleState();
            const {{contextLoaded, explicitHistoryStart, modelExportableCount, connection}} = finalState;
            const capturedIds = new Set(messages.map(msg => msg.id));
            // Album children are represented by their attachments, not additional chat bubbles.
            for (const message of messages) {{
                if (message.type !== 'album') continue;
                const parent = selected.find(item => messageId(item.msg) === message.id)?.msg;
                const children = new Set(mediaChildren(parent).map(messageId));
                for (const attachment of message.attachments || []) {{
                    if (children.has(attachment.sourceMessageId)) capturedIds.add(attachment.sourceMessageId);
                }}
            }}
            const modelExportableMessages = finalState.modelExportableMessages;
            const missingModelMessages = modelExportableMessages?.filter(msg => !messageId(msg) || !capturedIds.has(messageId(msg))) || [];
            const modelCoverageComplete = modelExportableMessages !== null && missingModelMessages.length === 0;
            const capped = maxMessages > 0 && messages.length >= maxMessages;
            const oldestCapturedTime = Math.min(...messages.map(msg => Number(msg.t) || 0));
            const missingRequiredMessages = missingModelMessages.filter(msg => !capped || !messageId(msg)
                || !Number.isFinite(Number(msg.t)) || Number(msg.t) <= 0 || oldestCapturedTime <= 0
                || Number(msg.t) > oldestCapturedTime);
            const incompleteReasons = [];
            if (waitTimedOut) incompleteReasons.push('state-wait-timeout');
            if (finalState.isLoading) incompleteReasons.push('active-loading');
            if (!connection.connected) incompleteReasons.push('disconnected');
            if (!openedChatJid) incompleteReasons.push('missing-chat-identity');
            if (!contextLoaded) incompleteReasons.push('context-not-loaded');
            if (unresolvedContainers.size) incompleteReasons.push('unresolved-dom-message-models');
            if (modelExportableMessages === null) incompleteReasons.push('unknown-model-coverage');
            if (missingRequiredMessages.length) incompleteReasons.push('missing-required-model-messages');
            const captureIncomplete = incompleteReasons.length > 0;
            // Existing consumers use isLoading as the fail-closed incomplete-capture gate.
            const isLoading = captureIncomplete;
            const stableEmpty = stableEmptyProof && !waitTimedOut && messages.length === 0 && selected.length === 0;
            historyStartReached = !captureIncomplete && (stableEmpty || (explicitHistoryStart && modelCoverageComplete));
            const confirmedEmpty = messages.length === 0 && selected.length === 0 && modelExportableCount === 0
                && Boolean(openedChatJid) && (explicitHistoryStart || stableEmpty) && contextLoaded && !isLoading
                && !observedContent && finalState.domModelsResolved;
            const expirySetting = chatModel && typeof chatModel.ephemeralDuration === 'number' ? {{
                durationSeconds: chatModel.ephemeralDuration,
                settingChangedAt: chatModel.ephemeralSettingTimestamp ? new Date(chatModel.ephemeralSettingTimestamp * 1000).toISOString() : null,
                observedAt: new Date().toISOString(), source: 'live-chat-model', appliesRetroactively: false
            }} : null;
            const captureState = {{
                state: messages.length ? 'content-captured' : confirmedEmpty ? 'confirmed-empty' : isLoading ? 'still-loading'
                    : seen.size ? 'only-nonexportable-message-models' : 'no-message-models',
                observedMessageCount: seen.size, exportableMessageCount: selected.length,
                isLoading, historyStartReached, confirmedEmpty, modelExportableCount,
                captureIncomplete, incompleteReasons, modelCoverageComplete,
                missingModelMessageCount: missingModelMessages.length, missingRequiredMessageCount: missingRequiredMessages.length,
                missingRequiredMessages: missingRequiredMessages.map(msg => ({{
                    messageId: messageId(msg), messageType: msg.type || null,
                    timestamp: Number(msg.t) > 0 && Number.isFinite(Number(msg.t))
                        && Number(msg.t) < 8640000000000 ? new Date(Number(msg.t) * 1000).toISOString() : null,
                    parentMessageId: waCollectorMessageKey(msg.parentMsgKey) || waCollectorMessageKey(msg.parentMsgId) || null,
                    fileName: typeof msg.fileName === 'string' ? msg.fileName : typeof msg.filename === 'string' ? msg.filename : null,
                    sizeBytes: Number.isSafeInteger(Number(msg.size || msg.fileSize)) && Number(msg.size || msg.fileSize) > 0
                        ? Number(msg.size || msg.fileSize) : null
                }})),
                unresolvedObservedContainerCount: unresolvedContainers.size,
                domContainerCount: finalState.domContainerCount, domResolvedCount: finalState.domResolvedCount,
                domUnresolvedCount: finalState.domUnresolvedCount, domExportableCount: finalState.domExportableCount,
                contextLoaded, explicitHistoryStart, waitTimedOut, connection, disconnectedObserved, identityWaitMs, stateWaits,
                activeLoading: finalState.isLoading,
                emptyEvidence: {{proof: confirmedEmpty ? (explicitHistoryStart ? 'explicit-history-exhausted' : 'observed-stable-empty') : null,
                    stableEmpty, requiredStableMs: stableEmptyMs, observedStableMs: emptySince === null ? 0 : performance.now() - emptySince,
                    samples: emptySamples, resets: emptyResets, observedContent, identityJid: openedChatJid,
                    identityStable: Boolean(openedChatJid), modelAvailable: Boolean(chatModel), domModelsResolved: finalState.domModelsResolved}},
                capturedAt: new Date().toISOString(), expirySetting
            }};
            return JSON.stringify({{OPENED_CHAT_RECENT_MESSAGES: true, openedChatTitle, openedChatJid, messages, captureState}});
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
            group_rows = self._idb_read_all("group-metadata")
            chat_rows = self._idb_read_all("chat")
            contact_keys: list[str] = []
            for row in chat_rows:
                chat = row.get("value")
                if not isinstance(chat, dict):
                    continue
                for field in ("id", "historyChatId", "accountLid"):
                    contact_keys.extend(self._message_jid_aliases(chat.get(field)))
            contact_rows = self._idb_get_many("contact", contact_keys)
        except (RuntimeError, TimeoutError, ValueError):
            return {}, {}, {}, {}, {}
        contacts_by_name = self._contacts_by_normalized_name(contact_rows)
        groups_by_name = self._groups_by_normalized_name(group_rows)
        chats_by_id = {row["value"]["id"]: row["value"] for row in chat_rows}
        chats_by_alias = self._chats_by_alias(chat_rows)
        return contacts_by_name, groups_by_name, chats_by_id, chats_by_alias, {}

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
            jid = WhatsAppCollector._message_jid_from_storage_key(row.get("key"))
            value = row.get("value")
            if not jid or not isinstance(value, dict):
                continue
            grouped[jid].append(value)
        return grouped

    def _coalesce_album_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        album_parents = {
            message_id: message
            for message in messages
            if str(message.get("type") or "").lower() == "album"
            and (message_id := self._message_id_string(message.get("id")))
        }
        if not album_parents:
            return list(messages)

        children_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for message in messages:
            parent_id = self._message_id_string(message.get("parentMsgKey")) or self._message_id_string(message.get("parentMsgId"))
            if parent_id in album_parents:
                children_by_parent[parent_id].append(message)

        coalesced: list[dict[str, Any]] = []
        for message in messages:
            parent_id = self._message_id_string(message.get("parentMsgKey")) or self._message_id_string(message.get("parentMsgId"))
            if parent_id in album_parents:
                continue

            message_id = self._message_id_string(message.get("id"))
            children = children_by_parent.get(message_id or "", [])
            if not children:
                coalesced.append(message)
                continue

            merged = dict(message)
            existing = [dict(item) for item in merged.get("attachments") or [] if isinstance(item, dict)]
            child_attachments: list[dict[str, Any]] = []
            for child in sorted(children, key=lambda item: item.get("t") or 0):
                child_id = self._message_id_string(child.get("id"))
                child_type = str(child.get("type") or "").lower()
                if not child_id or child_type not in ATTACHMENT_MESSAGE_TYPES:
                    continue
                child_attachments.append(
                    {
                        "kind": "audio" if child_type == "ptt" else child_type,
                        "mimeType": self._first_string(child, ["mimetype", "mimeType", "mime"]),
                        "fileName": self._first_string(child, ["fileName", "filename", "documentTitle", "title"]),
                        "sizeBytes": self._first_int(child, ["size", "fileSize", "sizeBytes"]),
                        "filehash": self._first_string(child, ["filehash", "fileHash"]),
                        "encFilehash": self._first_string(child, ["encFilehash", "encryptedFileHash"]),
                        "directPath": self._first_string(child, ["directPath"]),
                        "sourceMessageId": child_id,
                        "detectionSource": "indexeddb-album-child-metadata",
                    }
                )
            merged["attachments"] = child_attachments or existing
            child_timestamps = [int(child.get("t") or 0) for child in children]
            if not epoch(merged.get("t")):
                merged["t"] = max(child_timestamps, default=0)
            if not self._extract_message_text(merged):
                child_text = next((self._extract_message_text(child) for child in children if self._extract_message_text(child)), None)
                if child_text:
                    merged["caption"] = child_text
            coalesced.append(merged)
        return coalesced

    @staticmethod
    def _message_id_string(value: Any) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            for key in ("_serialized", "$1"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return None

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
        allow_ui_attachment_download: bool = False,
    ) -> list[RecentMessage]:
        normalized_messages = self._coalesce_album_messages(messages)
        sorted_messages = sorted(normalized_messages, key=lambda item: item.get("t") or 0, reverse=True)
        recent_messages: list[RecentMessage] = []
        hint_ids: list[str] = []
        for message in sorted_messages:
            message_id = self._message_id_string(message.get("id")) or f"{jid}:{message.get('t') or 0}"
            message_text = self._extract_message_text(message)
            attachments = self._message_attachments(
                message,
                attachments_dir=attachments_dir,
                thread_key=thread_key or jid,
                message_id=str(message_id),
                allow_ui_download=allow_ui_attachment_download,
            )
            if not message_text and not attachments:
                continue
            hint_ids.extend(attachment.source_message_id or str(message_id) for attachment in attachments)
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
                    expiry=expiry_metadata(message, source="indexeddb-message"),
                )
            )
            if len(recent_messages) >= max_messages:
                break
        if not hasattr(self, "_attachment_hint_message_ids"):
            self._attachment_hint_message_ids: dict[str, list[str]] = {}
        self._attachment_hint_message_ids[thread_key or jid] = list(dict.fromkeys(hint_ids))
        return recent_messages

    def _message_attachments(
        self,
        message: dict[str, Any],
        *,
        attachments_dir: Path | None,
        thread_key: str,
        message_id: str,
        allow_ui_download: bool = False,
    ) -> list[RecentAttachment]:
        candidates = self._attachment_candidates(message)
        store = self._attachment_store
        if store is None and attachments_dir is not None:
            store = AttachmentStore(attachments_dir, policy=self._attachment_policy)
        attachments: list[RecentAttachment] = []
        for index, candidate in enumerate(candidates):
            attachments.append(
                self._attachment_from_candidate(
                    candidate,
                    store=store,
                    thread_key=thread_key,
                    message_id=message_id,
                    index=index,
                    allow_ui_download=allow_ui_download,
                    message_timestamp=epoch(message.get("t")) or epoch(message.get("timestamp")),
                )
            )
        return attachments

    def _message_has_exportable_content(self, message: dict[str, Any]) -> bool:
        return bool(self._extract_message_text(message) or self._attachment_candidates(message))

    def _attachment_candidates(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if self._is_noncontent_control_message(message):
            return []
        explicit = message.get("attachments")
        if isinstance(explicit, list) and explicit:
            candidates = [dict(item) for item in explicit if isinstance(item, dict)]
            if len(candidates) == 1:
                metadata = {
                    "mimeType": self._first_string(message, ["mimetype", "mimeType", "mime"]),
                    "fileName": self._first_string(message, ["fileName", "filename", "documentTitle", "title"]),
                    "sizeBytes": self._first_int(message, ["size", "fileSize", "sizeBytes"]),
                    "filehash": self._first_string(message, ["filehash", "fileHash"]),
                    "encFilehash": self._first_string(message, ["encFilehash", "encryptedFileHash"]),
                    "directPath": self._first_string(message, ["directPath"]),
                    "detectionSource": "live-message-model",
                }
                candidates[0] = {key: value for key, value in metadata.items() if value is not None} | candidates[0]
            return candidates

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
        return [
            {
                "kind": self._attachment_kind(message_type, mimetype_value, file_name),
                "mimeType": mimetype_value,
                "fileName": file_name,
                "sizeBytes": size_bytes,
                "dataUrl": data_url,
                "filehash": self._first_string(message, ["filehash", "fileHash"]),
                "encFilehash": self._first_string(message, ["encFilehash", "encryptedFileHash"]),
                "directPath": self._first_string(message, ["directPath"]),
                "detectionSource": "indexeddb-message-metadata",
            }
        ]

    def _attachment_from_candidate(
        self,
        candidate: dict[str, Any],
        *,
        store: AttachmentStore | None,
        thread_key: str,
        message_id: str,
        index: int,
        allow_ui_download: bool,
        message_timestamp: float | None = None,
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
        file_hash = self._first_string(candidate, ["filehash", "fileHash"])
        detection_source = self._first_string(candidate, ["detectionSource"]) or "message-metadata"
        source_message_id = self._first_string(candidate, ["sourceMessageId", "messageId"]) or message_id
        if source_message_id != message_id:
            message_timestamp = epoch(candidate.get("timestamp"))
        identity_index = 0 if source_message_id != message_id else index
        attachment_id = self._stable_attachment_id(
            message_id=source_message_id,
            kind=kind,
            file_name=file_name,
            index=identity_index,
        )
        download_attempts = 0
        errors: list[str] = []
        last_store_result: StoreResult | None = None

        if store is None:
            return RecentAttachment(
                attachment_id=attachment_id,
                kind=kind,
                mime_type=mime_type,
                file_name=file_name,
                size_bytes=size_bytes,
                status="notDownloaded",
                download_attempts=0,
                detection_source=detection_source,
                source_message_id=source_message_id,
                skipped_reason="attachment-storage-unavailable",
                note="Attachment metadata was found, but no attachment output folder was configured.",
            )

        existing = store.existing(
            thread_key=thread_key,
            message_id=source_message_id,
            attachment_id=attachment_id,
            expected_size=size_bytes,
            expected_filehash=file_hash,
            mime_type=mime_type,
            file_name=file_name,
            message_timestamp=message_timestamp,
        )
        if existing:
            return self._downloaded_attachment(
                existing,
                attachment_id=attachment_id,
                kind=kind,
                fallback_mime=mime_type,
                download_attempts=0,
                detection_source=detection_source,
                source_message_id=source_message_id,
            )

        preflight = store.preflight(thread_key=thread_key, expected_size=size_bytes)
        if preflight:
            return RecentAttachment(
                attachment_id=attachment_id,
                kind=kind,
                mime_type=mime_type,
                file_name=file_name,
                size_bytes=size_bytes,
                status="notAllowedToDownload" if preflight.skipped_reason in {
                    "file-size-limit", "thread-storage-limit", "total-storage-limit",
                    "thread-storage-protected", "total-storage-protected",
                } else "notDownloaded",
                download_attempts=0,
                detection_source=detection_source,
                source_message_id=source_message_id,
                skipped_reason=preflight.skipped_reason,
                note=preflight.note,
            )

        def retain(data: bytes, *, method: str, actual_mime: str | None = None) -> RecentAttachment | None:
            nonlocal last_store_result
            last_store_result = store.store_bytes(
                data,
                thread_key=thread_key,
                message_id=source_message_id,
                attachment_id=attachment_id,
                file_name=file_name,
                expected_size=size_bytes,
                expected_filehash=file_hash,
                mime_type=mime_type or actual_mime,
                download_method=method,
                message_timestamp=message_timestamp,
            )
            if not last_store_result.attachment:
                if last_store_result.skipped_reason in {
                    "file-size-limit", "thread-storage-limit", "total-storage-limit",
                    "thread-storage-protected", "total-storage-protected",
                }:
                    return RecentAttachment(
                        attachment_id=attachment_id, kind=kind, mime_type=mime_type, file_name=file_name,
                        size_bytes=size_bytes, status="notAllowedToDownload", download_attempts=download_attempts,
                        detection_source=detection_source, source_message_id=source_message_id,
                        skipped_reason=last_store_result.skipped_reason, note=last_store_result.note,
                    )
                if last_store_result.note:
                    errors.append(f"{method}:{last_store_result.note}")
                return None
            return self._downloaded_attachment(
                last_store_result.attachment,
                attachment_id=attachment_id,
                kind=kind,
                fallback_mime=mime_type or actual_mime,
                download_attempts=download_attempts,
                detection_source=detection_source,
                source_message_id=source_message_id,
            )

        if file_hash and hasattr(self.session, "read_cached_media"):
            try:
                cached = self.session.read_cached_media(file_hash, max_bytes=self._attachment_policy.max_file_bytes)
                cached_data = cached.get("data") if isinstance(cached, dict) else None
                if isinstance(cached_data, bytes):
                    downloaded = retain(cached_data, method="whatsapp-media-cache", actual_mime=cached.get("mimeType"))
                    if downloaded:
                        return downloaded
                elif isinstance(cached, dict) and cached.get("error"):
                    errors.append(f"media-cache:{cached['error']}")
            except AttachmentStorageError:
                raise
            except Exception as exc:
                errors.append(f"media-cache:{exc}")

        data_url = self._first_string(candidate, ["dataUrl", "dataURL"])
        if data_url:
            decoded = AttachmentStore.decode_data_url(data_url)
            if decoded:
                data, actual_mime = decoded
                downloaded = retain(data, method="verified-dom-media", actual_mime=actual_mime)
                if downloaded:
                    return downloaded
            else:
                errors.append("verified-dom-media:invalid-data-url")

        if hasattr(self.session, "request_visible_media_download"):
            for force in (False, True):
                download_attempts += 1
                method = "forced-message-model" if force else "message-model"
                try:
                    result = self.session.request_visible_media_download(source_message_id, file_hash=file_hash, force=force)
                    if not isinstance(result, dict) or not result.get("ok"):
                        errors.append(f"{method}:{(result or {}).get('error', 'failed') if isinstance(result, dict) else 'failed'}")
                        continue
                    if file_hash and hasattr(self.session, "read_cached_media"):
                        cached = self.session.read_cached_media(file_hash, max_bytes=self._attachment_policy.max_file_bytes)
                        cached_data = cached.get("data") if isinstance(cached, dict) else None
                        if isinstance(cached_data, bytes):
                            downloaded = retain(cached_data, method=f"{method}-cache", actual_mime=cached.get("mimeType"))
                            if downloaded:
                                return downloaded
                    errors.append(f"{method}:verified-cache-bytes-unavailable")
                except AttachmentStorageError:
                    raise
                except Exception as exc:
                    errors.append(f"{method}:{exc}")

        if allow_ui_download and hasattr(self.session, "download_visible_attachment"):
            download_attempts += 1
            try:
                browser_download = self.session.download_visible_attachment(
                    source_message_id,
                    file_hash=file_hash,
                    expected_size=size_bytes,
                    expected_file_name=file_name,
                    kind=kind,
                )
                if isinstance(browser_download, dict) and browser_download.get("ok") and browser_download.get("path"):
                    source_path = Path(str(browser_download["path"]))
                    try:
                        data = source_path.read_bytes()
                        downloaded = retain(data, method=str(browser_download.get("method") or "browser-download"))
                    finally:
                        source_path.unlink(missing_ok=True)
                    if downloaded:
                        return downloaded
                else:
                    errors.append(f"browser-download:{browser_download.get('error', 'failed') if isinstance(browser_download, dict) else 'failed'}")
            except AttachmentStorageError:
                raise
            except Exception as exc:
                errors.append(f"browser-download:{exc}")

        skipped_reason = last_store_result.skipped_reason if last_store_result and last_store_result.skipped_reason else "download-retry-exhausted"
        note = "WhatsApp Collector detected the attachment but could not retain verified bytes after its available extraction routes."
        if errors:
            compact_errors = "; ".join(re.sub(r"\s+", " ", error).strip()[:180] for error in errors[-5:])
            note = f"{note} Attempts: {compact_errors}"
        return RecentAttachment(
            attachment_id=attachment_id,
            kind=kind,
            mime_type=mime_type,
            file_name=file_name,
            size_bytes=size_bytes,
            status="notAllowedToDownload" if skipped_reason in {
                "file-size-limit", "thread-storage-limit", "total-storage-limit",
                "thread-storage-protected", "total-storage-protected",
            } else "notDownloaded",
            verified=False,
            download_attempts=download_attempts,
            detection_source=detection_source,
            source_message_id=source_message_id,
            skipped_reason=skipped_reason,
            note=note,
        )

    @staticmethod
    def _downloaded_attachment(
        stored: Any,
        *,
        attachment_id: str,
        kind: str,
        fallback_mime: str | None,
        download_attempts: int,
        detection_source: str,
        source_message_id: str,
    ) -> RecentAttachment:
        return RecentAttachment(
            attachment_id=attachment_id,
            kind=kind,
            mime_type=stored.mime_type or fallback_mime,
            file_name=stored.file_name,
            size_bytes=stored.size_bytes,
            status="downloaded",
            relative_path=stored.relative_path,
            local_path=str(stored.path),
            sha256=stored.sha256,
            download_method=stored.download_method,
            verified=True,
            download_attempts=download_attempts,
            detection_source=detection_source,
            source_message_id=source_message_id,
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
        digest = hashlib.sha256(f"{message_id}:{index}".encode("utf-8")).hexdigest()[:16]
        return f"att_{digest}"

    @staticmethod
    def _safe_path_component(value: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "").strip(".-")
        return clean[:120] or "unknown"

    @classmethod
    def _safe_attachment_filename(cls, value: str | None, *, kind: str, mime_type: str | None, index: int) -> str:
        raw = (value or "").strip()
        normalized_mime = (mime_type or "").split(";", 1)[0].strip()
        if not raw:
            extension = mimetypes.guess_extension(normalized_mime) or {
                "image": ".jpg",
                "video": ".mp4",
                "audio": ".ogg" if normalized_mime == "audio/ogg" else ".m4a",
                "document": ".bin",
                "sticker": ".webp",
            }.get(kind, ".bin")
            raw = f"attachment-{index + 1}{extension}"
        name = cls._safe_path_component(raw)
        suffix = Path(name).suffix
        if not suffix:
            guessed = mimetypes.guess_extension(normalized_mime)
            if guessed:
                name = f"{name}{guessed}"
        return name

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
    def _is_noncontent_control_message(message: dict[str, Any]) -> bool:
        kind = (str(message.get("type") or "").strip().lower(), str(message.get("subtype") or "").strip().lower())
        return kind in NONCONTENT_CONTROL_MESSAGE_KINDS

    @staticmethod
    def _extract_message_text(message: dict[str, Any]) -> str | None:
        if WhatsAppCollector._is_noncontent_control_message(message):
            return None
        candidates = [
            message.get("body"),
            message.get("caption"),
            message.get("text"),
            message.get("matchedText"),
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip() and not WhatsAppCollector._looks_like_inline_media_payload(candidate):
                return candidate.strip()
        if message.get("type") == "location":
            lat, lng = message.get("lat"), message.get("lng")
            if (isinstance(lat, (int, float)) and not isinstance(lat, bool) and -90 <= lat <= 90
                    and isinstance(lng, (int, float)) and not isinstance(lng, bool) and -180 <= lng <= 180):
                return f"Location: {lat}, {lng} (https://maps.google.com/?q={lat},{lng})"
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
    def _format_timestamp(timestamp: Any) -> str | None:
        normalized = epoch(timestamp)
        if normalized is None:
            return None
        try:
            return datetime.fromtimestamp(normalized, tz=timezone.utc).replace(microsecond=0).isoformat()
        except (ValueError, OverflowError, OSError):
            return None

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
