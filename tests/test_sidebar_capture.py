import json
import shutil
import subprocess

import pytest

from whatsapp_collector.collector import CHAT_LIST_BODY_JS, AllViewSidebarError, WhatsAppCollector
from whatsapp_collector.parsing import parse_chat_list


def capture_fixture(*, loading_until=0, missing=False, count=25, alternate=False, stale_until=0,
                    stuck=False, horizon=15, administrative=False, recycled=False, hidden_text=False,
                    selection_attribute="aria-selected", replace_filter=False, filter_appears_at=0,
                    never_selected=False, conflicting=False, live_updates=False, row_count_override=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for sidebar fixtures")
    config = json.dumps(dict(loading_until=loading_until, missing=missing, count=count,
                             alternate=alternate, stale_until=stale_until, stuck=stuck, administrative=administrative,
                             recycled=recycled, hidden_text=hidden_text, selection_attribute=selection_attribute,
                             replace_filter=replace_filter, filter_appears_at=filter_appears_at,
                             never_selected=never_selected, conflicting=conflicting, live_updates=live_updates,
                             row_count_override=row_count_override))
    fixture = r'''
        const cfg = CONFIG;
        let now = 0, clicks = 0, scrollEvents = 0;
        global.performance = {now: () => now};
        global.setTimeout = (fn, ms) => {now += ms; fn();};
        const makeFilter = detached => ({
            getAttribute: key => {
                if (cfg.conflicting) return key === 'aria-selected' ? 'false' : 'true';
                if (key !== cfg.selection_attribute) return null;
                return cfg.never_selected || detached ? 'false' : 'true';
            },
            click: () => clicks++
        });
        const all = makeFilter(cfg.replace_filter), replacement = makeFilter(false);
        const pane = {scrollTop: 800, clientHeight: 500, scrollHeight: cfg.count * 100,
            dispatchEvent: () => scrollEvents++,
            querySelector: selector => selector.includes('progressbar')
                ? (now < cfg.loading_until ? {} : null)
                : selector === '[aria-rowcount]' ? {getAttribute: () => cfg.row_count_override ?? String(cfg.count)} : null,
            querySelectorAll: selector => {
                if (!selector.includes('[role="row"]') || !selector.includes('cell-frame-container')) throw Error('Missing row alternatives');
                let start = cfg.stuck || now < cfg.stale_until ? 8 : Math.floor(pane.scrollTop / 100);
                return Array.from({length: Math.min(6, Math.max(0, cfg.count - start))}, (_,i) => makeRow(start + i));
            }};
        function makeRow(i) {
            const admin = cfg.administrative && i === 0;
            const unread = cfg.live_updates ? 1 + Math.floor(now / 100) % 2 : 3;
            const preview = cfg.live_updates ? `Message ${i} update ${now}` : `Message ${i}`;
            const text = admin ? 'Locked chats' : `${unread} unread messages\nChat ${i}\n8:09 PM\n${preview}\n${unread}`;
            return {innerText: cfg.hidden_text ? '' : text, textContent:text, contains: () => false,
                getAttribute: key => key === 'data-testid' ? (cfg.alternate ? 'cell-frame-container' : `list-item-${i}`)
                    : key === 'aria-rowindex' ? (cfg.recycled ? null : String(i+1))
                    : key === 'style' && cfg.recycled ? `transform: translateY(${i*100}px)` : null,
                querySelector: selector => {
                    if (admin) return null;
                    // Exercise text-only fallback as well as title/preview fields.
                    if (cfg.alternate) return null;
                    if (selector === 'span[title]') return {getAttribute: () => `Chat ${i}`, innerText: `Chat ${i}`};
                    if (selector.includes('chat-list-timestamp')) return {innerText:cfg.hidden_text ? '' : '8:09 PM',textContent:'8:09 PM'};
                    if (selector === '[data-testid="last-msg-status"]') return {innerText:preview};
                    return null;
                }, querySelectorAll: () => []};
        }
        global.document = {
            get body() {throw Error('Conversation text must never be read');},
            getElementById: () => now < cfg.filter_appears_at ? null : cfg.replace_filter && clicks ? replacement : all,
            querySelectorAll: () => [],
            querySelector: () => cfg.missing ? null : pane
        };
    '''.replace("CONFIG", config)
    suffix = ".then(value => console.log(JSON.stringify({result:JSON.parse(value),now,clicks,scrollEvents})));"
    script = CHAT_LIST_BODY_JS.replace("const requestedHorizon = 15;", f"const requestedHorizon = {horizon};")
    result = subprocess.run([node, "-e", fixture + script + suffix],
                            check=True, capture_output=True, text=True, timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize("alternate", [False, True])
def test_sidebar_capture_reads_real_first_fifteen_across_virtualized_windows(alternate):
    actual = capture_fixture(alternate=alternate, loading_until=200, stale_until=300)
    result = actual["result"]
    assert [row["chat_name"] for row in result["rows"]] == [f"Chat {i}" for i in range(15)]
    assert all(row["timestamp_label"] == "8:09 PM" and row["unread_count"] == 3 for row in result["rows"])
    assert result["captureState"]["source"] == "all-view-sidebar"
    assert result["captureState"]["ready"] is True
    assert actual["now"] >= 550
    assert actual["clicks"] == 0
    assert actual["scrollEvents"] > 1


def test_current_all_filter_pressed_contract_accepts_full_horizon_without_clicking():
    actual = capture_fixture(count=100, horizon=31, selection_attribute="aria-pressed", recycled=True)
    assert [r["chat_name"] for r in actual["result"]["rows"]] == [f"Chat {i}" for i in range(31)]
    state = actual["result"]["captureState"]
    assert state["allFilter"]["ariaPressed"] == "true"
    assert state["allFilter"]["ariaSelected"] is None
    assert state["readyReason"] == "requested-horizon"
    assert actual["clicks"] == 0


def test_filter_that_appears_late_and_is_replaced_on_selection_is_reacquired():
    actual = capture_fixture(selection_attribute="aria-pressed", filter_appears_at=400, replace_filter=True)
    assert actual["result"]["captureState"]["ready"] is True
    assert [r["chat_name"] for r in actual["result"]["rows"]] == [f"Chat {i}" for i in range(15)]
    assert actual["clicks"] == 1


def test_preview_and_unread_churn_do_not_keep_a_stable_list_waiting():
    actual = capture_fixture(live_updates=True, selection_attribute="aria-pressed")
    assert actual["result"]["captureState"]["ready"] is True
    assert len(actual["result"]["rows"]) == 15
    assert actual["now"] < 3000


@pytest.mark.parametrize("kwargs,reason", [
    ({"missing": True}, "sidebar-unavailable"),
    ({"loading_until": 10000}, "sidebar-loading"),
    ({"stuck": True}, "sidebar-top-unverified"),
    ({"never_selected": True, "selection_attribute": "aria-pressed"}, "all-filter-unverified"),
    ({"conflicting": True}, "all-filter-unverified"),
])
def test_missing_loading_or_unverified_sidebar_fails_closed(kwargs, reason):
    result = capture_fixture(**kwargs)["result"]
    assert result["rows"] == []
    state = result["captureState"]
    assert state["ready"] is False
    assert state["blockedBy"] == result["error"] == reason
    assert state["elapsedMs"] >= state["deadlineMs"]


@pytest.mark.parametrize("count", [0, 3])
def test_explicit_sidebar_rowcount_can_complete_short_horizon(count):
    result = capture_fixture(count=count)["result"]
    assert len(result["rows"]) == count
    assert result["captureState"]["ready"] is True


def test_empty_aria_rowcount_does_not_claim_confirmed_empty_sidebar():
    result = capture_fixture(count=0, row_count_override="")["result"]
    assert result["captureState"]["ready"] is False
    assert result["captureState"]["rowCount"] is None
    assert result["error"] == "sidebar-coverage-incomplete"


@pytest.mark.parametrize("horizon", [20, 25])
def test_configured_horizon_larger_than_fifteen(horizon):
    result = capture_fixture(count=35, horizon=horizon)["result"]
    assert len(result["rows"]) == horizon
    assert result["captureState"]["requestedHorizon"] == horizon
    assert result["rows"][-1]["chat_name"] == f"Chat {horizon-1}"


def test_administrative_first_index_does_not_block_real_horizon():
    result = capture_fixture(count=25, administrative=True)["result"]
    assert result["captureState"]["ready"] is True
    assert [r["chat_name"] for r in result["rows"]] == [f"Chat {i}" for i in range(1,16)]


def test_recycled_virtual_slots_and_hidden_innertext_keep_horizon_order():
    result = capture_fixture(count=35, horizon=25, recycled=True, hidden_text=True)["result"]
    assert [r["chat_name"] for r in result["rows"]] == [f"Chat {i}" for i in range(25)]
    assert result["captureState"]["ready"] is True


def test_collector_uses_configured_horizon_without_changing_override_callers():
    class Session:
        def run_json(self, script):
            if "CHAT_LIST_BODY" in script:
                assert "const requestedHorizon = 25;" in script
            return {"rows": [], "bodyScope": "sidebar", "captureState": {"ready": True}}
    collector = WhatsAppCollector(Session())
    collector._all_view_capture_horizon = 25
    assert collector.collect_chat_list() == []


@pytest.mark.parametrize("payload", [
    {"body": "Click here to get older messages from your phone.\nToday\nConversation"},
    {"rows": [], "bodyScope": "sidebar", "captureState": {"ready": False}},
])
def test_collector_refuses_unscoped_body_and_unsettled_capture(payload):
    class Session:
        def run_json(self, script):
            return payload
    with pytest.raises(RuntimeError):
        WhatsAppCollector(Session()).collect_chat_list()


def test_sidebar_failure_has_context_and_does_not_return_partial_rows():
    state = {"ready": False, "blockedBy": "all-filter-unverified", "requestedHorizon": 31}

    class Session:
        def run_json(self, script):
            return {"rows": [{"chat_name": "Example", "timestamp_label": "Today"}], "captureState": state}

    collector = WhatsAppCollector(Session())
    with pytest.raises(AllViewSidebarError, match="select All") as failure:
        collector._timed_export_call("discovery", collector.collect_chat_list)
    assert failure.value.diagnostics == {**state, "phase": "discovery"}


def test_collector_retains_scoped_legacy_sidebar_fallback():
    class Session:
        def run_json(self, script):
            return {"bodyScope": "sidebar", "body": "My chat\n8:09 PM\nPreview"}
    rows = WhatsAppCollector(Session()).collect_chat_list()
    assert rows[0].chat_name == "My chat"


def test_sidebar_text_supports_bidi_ampm_and_chat_names_ending_chat():
    assert parse_chat_list("\u2066My chat\u2069\n8:09\u00a0PM\nPreview")[0]["chat_name"] == "My chat"
    assert parse_chat_list("Click here to get older messages from your phone.\nToday\nConversation") == []
