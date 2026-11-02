"""Isolated Node fixtures for the actual opened-chat JavaScript, with a virtual clock."""

import json
import shutil
import subprocess

import pytest

from whatsapp_collector.collector import WhatsAppCollector
from whatsapp_collector.message_expiry import complete_live_capture, confirmed_empty


FIXTURE = r"""
let now = 0;
let frozenClock = false;
let timerStep = 0, timerCalls = 0;
global.performance = {now: () => frozenClock ? 0 : now};
Object.defineProperty(global, 'navigator', {value: {onLine: true}, configurable: true});
global.setTimeout = (fn, ms) => { timerCalls++; now += Math.max(ms, timerStep); update(); fn(); };
const load = {contextLoaded: true, noEarlierMsgs: false};
let model = {id: {_serialized: 'a@lid'}, msgs: {_models: [], msgLoadState: load}};
let available = true, jid = 'a@lid', progress = false, unresolved = false;
let appendUnresolved = false;
const unknownContainer = {};
let socketState, alertText = '', fetches = 0, scrollWrites = 0;
global.fetch = async () => {fetches++; return {blob: async () => ({size: 42, type: 'image/jpeg'})}};
global.FileReader = class {readAsDataURL() {this.result = 'data:image/jpeg;base64,AA=='; this.onload()}};
const message = {id: 'm1', type: 'chat', body: 'Real content', t: 100};
const container = {__reactFiber$x: {memoizedProps: {msg: message}}, querySelectorAll: () => []};
let visible = [];
const panel = {scrollHeight: 400, clientHeight: 400, _top: 0,
    get scrollTop() {return this._top}, set scrollTop(value) {
        scrollWrites++; this._top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight));
    }};
function update() { SCENARIO }
update();
global.window = {require: () => ({Socket: {state: socketState}, ChatCollection: {get: () => available ? model : null}})};
global.document = {
    querySelector(selector) {
        if (selector === '#main header') return {__reactFiber$x: {memoizedProps: {chat: {id: jid}}}};
        if (selector.includes('conversation-panel-messages')) return panel;
        if (selector.includes('progressbar')) return progress ? {} : null;
        return null;
    },
    querySelectorAll(selector) {
        if (selector.includes('role="alert"')) return alertText ? [{textContent: alertText}] : [];
        if (selector.includes('msg-container')) return unresolved ? [unknownContainer] : appendUnresolved ? [...visible, unknownContainer] : visible;
        return [];
    }
};
"""


def capture(scenario="", *, max_messages=1, expected_jids=None, verified_media=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for JavaScript fixtures")
    script = WhatsAppCollector._opened_chat_recent_messages_js(
        max_messages=max_messages, expected_jids=expected_jids, verified_media=verified_media)
    result = subprocess.run(
        [node, "-e", FIXTURE.replace("SCENARIO", scenario) + script +
         ".then(value => console.log(JSON.stringify({value:JSON.parse(value), elapsed:now, fetches, scrollWrites, timerCalls})))"
         ".catch(error => console.log(JSON.stringify({error:String(error), elapsed:now, timerCalls})));"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_stable_empty_has_explicit_alternate_proof_and_downstream_compatibility():
    result = capture("model.ephemeralDuration = 7776000;")
    state = result["value"]["captureState"]
    assert state["confirmedEmpty"] and state["historyStartReached"]
    assert not state["explicitHistoryStart"]
    evidence = state["emptyEvidence"]
    assert evidence["proof"] == "observed-stable-empty"
    assert evidence["observedStableMs"] >= 2500
    assert evidence["samples"] >= 26
    assert evidence["identityStable"]
    state["identityVerified"] = True  # The caller supplies target-chat verification.
    assert confirmed_empty({"openedChatChecked": True, "captureState": state})


def test_empty_proof_does_not_depend_on_current_expiry_setting():
    assert capture()["value"]["captureState"]["confirmedEmpty"]


@pytest.mark.parametrize("scenario", [
    "load.isLoadingRecentMsgs = now >= 500 && now < 1700;",
    "load.isRepairingMsgHistory = now >= 500 && now < 1700;",
    "progress = now >= 500 && now < 1700;",
    "load.contextLoaded = !(now >= 500 && now < 1700);",
])
def test_loading_or_context_loss_restarts_empty_observation(scenario):
    result = capture(scenario)
    state = result["value"]["captureState"]
    assert state["confirmedEmpty"]
    assert result["elapsed"] >= 4200
    assert state["emptyEvidence"]["resets"] >= 1


def test_delayed_model_is_refreshed_before_empty_proof():
    result = capture("available = now >= 1200;")
    assert result["value"]["captureState"]["confirmedEmpty"]
    assert result["elapsed"] >= 3700


@pytest.mark.parametrize("scenario", [
    "if (now >= 1800) {model.msgs._models = [message]; visible = [container];}",
    "load.isLoadingRecentMsgs = now >= 600 && now < 2000; "
    "if (now >= 2000) {model.msgs._models = [message]; visible = [container];}",
])
def test_delayed_content_is_captured_not_confirmed_empty(scenario):
    result = capture(scenario)
    assert result["value"]["messages"][0]["body"] == "Real content"
    assert not result["value"]["captureState"]["confirmedEmpty"]


@pytest.mark.parametrize("scenario", [
    "available = false;",
    "load.contextLoaded = false;",
    "load.isLoadingRecentMsgs = true;",
    "load.isRepairingMsgHistory = true;",
    "model.id._serialized = 'wrong@lid';",
    "unresolved = true;",
    "if (now >= 1000) model.msgs._models = [message];",
    "load.isLoadingEarlierMsgs = now % 1000 < 200;",
])
def test_unproven_empty_times_out_closed(scenario):
    result = capture(scenario)
    state = result["value"]["captureState"]
    assert not state["confirmedEmpty"]
    assert not state["historyStartReached"]
    assert state["waitTimedOut"]
    assert state["emptyEvidence"]["proof"] is None
    assert result["elapsed"] <= 18000


def test_identity_change_and_return_is_rejected():
    result = capture("jid = now >= 500 && now < 1000 ? 'wrong@lid' : 'a@lid';")
    assert "opened chat changed" in result["error"]


def test_transient_model_content_disqualifies_alternate_proof():
    result = capture("model.msgs._models = now >= 500 && now < 1000 ? [message] : [];")
    state = result["value"]["captureState"]
    assert not state["confirmedEmpty"]
    assert state["emptyEvidence"]["observedContent"]


def test_ready_content_uses_state_waits_faster_than_old_fixed_waits():
    result = capture("model.msgs._models = [message]; visible = [container];")
    assert result["value"]["messages"]
    assert result["elapsed"] == 300


def test_explicit_history_start_remains_a_separate_proof():
    result = capture("load.noEarlierMsgs = true;")
    assert result["value"]["captureState"]["emptyEvidence"]["proof"] == "explicit-history-exhausted"


@pytest.mark.parametrize("explicit", [False, True])
def test_offline_blocks_both_proof_types(explicit):
    result = capture(f"load.noEarlierMsgs = {json.dumps(explicit)}; navigator.onLine = false;")
    state = result["value"]["captureState"]
    assert not state["confirmedEmpty"]
    assert state["isLoading"] and state["waitTimedOut"]
    assert state["connection"]["signals"] == ["browser-offline-or-unknown"]


def test_disconnect_resets_empty_interval():
    result = capture("navigator.onLine = !(now >= 500 && now < 1700);")
    assert result["value"]["captureState"]["confirmedEmpty"]
    assert result["elapsed"] >= 4200
    assert result["value"]["captureState"]["disconnectedObserved"]


def test_explicit_history_does_not_override_transient_content_or_timeout():
    result = capture("model.msgs._models = now < 700 ? [message] : []; load.noEarlierMsgs = now >= 700;")
    assert not result["value"]["captureState"]["confirmedEmpty"]
    result = capture("load.isLoadingRecentMsgs = now < 6500; load.noEarlierMsgs = true;")
    assert not result["value"]["captureState"]["confirmedEmpty"]
    assert result["value"]["captureState"]["waitTimedOut"]


def test_nonempty_timeout_is_incomplete():
    result = capture("visible = [container]; model.msgs._models = [message]; load.isLoadingRecentMsgs = true;")
    assert result["value"]["messages"]
    assert result["value"]["captureState"]["isLoading"]


@pytest.mark.parametrize("initial", ["old@lid", None])
def test_expected_identity_waits_for_async_click(initial):
    result = capture(f"jid = now < 800 ? {json.dumps(initial)} : 'a@lid';", expected_jids=["a@lid"])
    assert result["value"]["openedChatJid"] == "a@lid"
    assert result["value"]["captureState"]["identityWaitMs"] == 800
    assert result["value"]["captureState"]["confirmedEmpty"]


def test_expected_identity_timeout_never_captures_old_chat():
    result = capture("jid = 'old@lid';", expected_jids=["a@lid"])
    assert "did not become ready" in result["error"]
    assert result["elapsed"] == 3000


def test_missing_initial_identity_cannot_prove_empty():
    result = capture("jid = null;")
    assert not result["value"]["captureState"]["confirmedEmpty"]


@pytest.mark.parametrize("scenario", ["socketState = 'DISCONNECTED';", "alertText = 'Computer not connected';"])
@pytest.mark.parametrize("explicit", [False, True])
def test_application_disconnect_blocks_both_empty_proofs(scenario, explicit):
    result = capture(scenario + f"load.noEarlierMsgs = {json.dumps(explicit)};")
    state = result["value"]["captureState"]
    assert not state["confirmedEmpty"]
    assert not state["connection"]["connected"]
    assert state["isLoading"]


def test_empty_observation_requires_elapsed_time_not_just_poll_count():
    result = capture("frozenClock = true;")
    assert not result["value"]["captureState"]["confirmedEmpty"]
    assert result["value"]["captureState"]["waitTimedOut"]


def test_model_replacement_restarts_observation():
    result = capture("if (now === 1000) model = {...model, msgs: {...model.msgs}};")
    assert result["value"]["captureState"]["confirmedEmpty"]
    assert result["elapsed"] >= 3500
    assert result["value"]["captureState"]["emptyEvidence"]["resets"] >= 1


def test_unresolved_dom_blocks_explicit_empty_proof():
    result = capture("unresolved = true; load.noEarlierMsgs = true;")
    state = result["value"]["captureState"]
    assert not state["confirmedEmpty"]
    assert state["domContainerCount"] == 1
    assert state["domResolvedCount"] == 0
    assert state["domUnresolvedCount"] == 1
    assert state["domExportableCount"] == 0


def test_unresolved_system_like_dom_blocks_alternate_empty_with_counts():
    result = capture("unresolved = true;")
    state = result["value"]["captureState"]
    assert not state["confirmedEmpty"]
    assert state["domContainerCount"] == state["domUnresolvedCount"] == 1
    assert state["domResolvedCount"] == state["domExportableCount"] == 0
    assert not state["emptyEvidence"]["domModelsResolved"]


MEDIA = """
message.type = 'image'; message.filehash = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='; message.size = 42;
model.msgs._models = [message]; visible = [container];
container.querySelectorAll = () => [{getAttribute: () => 'blob:fixture'}];
"""


@pytest.mark.parametrize("mismatch", [None, "sourceMessageId", "filehash", "sizeBytes"])
def test_media_skip_requires_exact_verified_identity_hash_and_size(mismatch):
    hint = {"sourceMessageId": "m1", "filehash": "A" * 43 + "=", "sizeBytes": 42}
    if mismatch:
        hint[mismatch] = "wrong" if mismatch != "sizeBytes" else 41
    result = capture(MEDIA, verified_media=[hint])
    attachment = result["value"]["messages"][0]["attachments"][0]
    assert result["fetches"] == (0 if mismatch is None else 1)
    assert attachment["skippedReason"] == ("verified-local-media" if mismatch is None else None)


def test_no_media_hints_preserves_normal_blob_fetch():
    assert capture(MEDIA)["fetches"] == 1


def test_bottom_scroll_that_moves_still_waits_for_settling():
    result = capture("panel.scrollHeight = 800; model.msgs._models = [message]; visible = [container];")
    assert result["elapsed"] == 600
    assert result["scrollWrites"] == 1


def test_sparse_exhausted_history_with_all_ids_seen_skips_top_waits():
    result = capture("load.noEarlierMsgs = true; model.msgs._models = [message]; visible = [container];", max_messages=15)
    assert result["elapsed"] == 300
    assert result["scrollWrites"] == 1
    assert [msg["id"] for msg in result["value"]["messages"]] == ["m1"]
    assert result["value"]["captureState"]["historyStartReached"]


def test_explicit_zero_skips_top_waits_without_weakening_proof():
    result = capture("load.noEarlierMsgs = true;", max_messages=15)
    assert result["elapsed"] == 300
    assert result["scrollWrites"] == 1
    assert result["value"]["captureState"]["confirmedEmpty"]


@pytest.mark.parametrize("same_count", [False, True])
def test_exhausted_virtualized_history_requires_matching_ids_not_counts(same_count):
    scenario = """
load.noEarlierMsgs = true;
const older = {id: 'm2', type: 'chat', body: 'Virtualized older message', t: 50};
model.msgs._models = MODELS;
visible = scrollWrites < 2 ? [container] : [{__reactFiber$x: {memoizedProps: {msg: older}}, querySelectorAll: () => []}];
""".replace("MODELS", "[older]" if same_count else "[message, older]")
    result = capture(scenario, max_messages=15)
    assert {msg["id"] for msg in result["value"]["messages"]} == {"m1", "m2"}
    assert result["scrollWrites"] == 2
    assert result["elapsed"] >= 600


@pytest.mark.parametrize("guard", [
    "load.noEarlierMsgs = false;",
    "load.contextLoaded = false;",
    "load.isLoadingEarlierMsgs = true;",
    "navigator.onLine = false;",
    "model.msgs._models = undefined;",
    "model.msgs._models = [{type: 'chat', body: 'Missing ID'}];",
])
def test_sparse_history_early_exit_requires_positive_complete_evidence(guard):
    result = capture("load.noEarlierMsgs = true; model.msgs._models = [message]; visible = [container];" + guard, max_messages=15)
    assert result["scrollWrites"] > 1


def test_moving_to_bottom_retains_delayed_loading_safety():
    result = capture("""
panel.scrollHeight = 800;
model.msgs._models = [message]; visible = [container];
load.noEarlierMsgs = true;
load.isLoadingRecentMsgs = scrollWrites > 0 && now < 1200;
""", max_messages=15)
    assert result["elapsed"] >= 1500
    assert result["value"]["messages"]
    assert not result["value"]["captureState"]["isLoading"]


def assert_incomplete(result, limit):
    value = result["value"]
    state = value["captureState"]
    assert state["captureIncomplete"] and state["isLoading"]
    assert not state["historyStartReached"]
    assert not state["confirmedEmpty"]
    state["identityVerified"] = True
    assert not complete_live_capture({"openedChatChecked": True, "sourcesUsed": ["opened-chat"],
                                      "openedChatMessageCount": len(value["messages"]), "captureState": state}, limit)


def test_two_models_one_unresolved_container_cannot_be_authoritative():
    result = capture("""
load.noEarlierMsgs = true;
model.msgs._models = [message, {id: 'missing', type: 'chat', body: 'Legitimate content', t: 101}];
visible = [container]; appendUnresolved = true;
""", max_messages=15)
    assert len(result["value"]["messages"]) == 1
    assert_incomplete(result, 15)
    state = result["value"]["captureState"]
    assert state["missingRequiredMessageCount"] == 1
    assert "unresolved-dom-message-models" in state["incompleteReasons"]
    assert "missing-required-model-messages" in state["incompleteReasons"]


def test_exhausted_model_gap_without_unresolved_dom_is_incomplete():
    result = capture("""
load.noEarlierMsgs = true;
model.msgs._models = [message, {id: 'missing', type: 'chat', body: 'Not rendered', t: 90}];
visible = [container];
""", max_messages=15)
    assert_incomplete(result, 15)
    assert result["value"]["captureState"]["domUnresolvedCount"] == 0


CAPPED = """
load.noEarlierMsgs = true;
const models = Array.from({length: 15}, (_, i) => ({id: 'id' + i, type: 'chat', body: 'Message ' + i, t: 100 + i}));
model.msgs._models = models;
visible = models.map(msg => ({__reactFiber$x: {memoizedProps: {msg}}, querySelectorAll: () => []}));
"""


def test_capped_capture_cannot_ignore_an_unresolved_newer_container():
    result = capture(CAPPED + "appendUnresolved = true;", max_messages=15)
    assert len(result["value"]["messages"]) == 15
    assert_incomplete(result, 15)


@pytest.mark.parametrize("timestamp", [115, None, 0, "unknown"])
def test_capped_capture_rejects_missing_newer_or_unknown_time_models(timestamp):
    result = capture(CAPPED + "model.msgs._models.push({id:'missing',type:'chat',body:'Not rendered',t:" +
                     json.dumps(timestamp) + "});", max_messages=15)
    assert_incomplete(result, 15)
    assert result["value"]["captureState"]["missingRequiredMessageCount"] == 1


def test_capped_capture_allows_provably_older_models_without_claiming_exhaustion():
    result = capture(CAPPED + "model.msgs._models.push({id:'older',type:'chat',body:'Older',t:99});", max_messages=15)
    state = result["value"]["captureState"]
    assert not state["captureIncomplete"]
    assert not state["historyStartReached"]
    assert state["missingModelMessageCount"] == 1
    assert state["missingRequiredMessageCount"] == 0
    assert complete_live_capture({"sourcesUsed": ["opened-chat"], "openedChatMessageCount": 15,
                                  "captureState": state}, 15)


@pytest.mark.parametrize("same_second_burst", [False, True])
def test_capped_capture_allows_equally_recent_boundary_without_claiming_exhaustion(same_second_burst):
    scenario = CAPPED
    if same_second_burst:
        scenario += "model.msgs._models.forEach(msg => {msg.t = 100});"
    scenario += "model.msgs._models.push({id:'boundary',type:'chat',body:'Equally recent',t:100});"
    result = capture(scenario, max_messages=15)
    state = result["value"]["captureState"]
    assert len(result["value"]["messages"]) == 15
    assert state["modelExportableCount"] == 16
    assert not state["captureIncomplete"]
    assert not state["historyStartReached"]
    assert state["missingModelMessageCount"] == 1
    assert state["missingRequiredMessageCount"] == 0
    assert complete_live_capture({"sourcesUsed": ["opened-chat"], "openedChatMessageCount": 15,
                                  "captureState": state}, 15)


def test_unresolved_container_scrolled_out_of_view_remains_incomplete():
    result = capture("""
load.noEarlierMsgs = true; model.msgs._models = [message]; visible = [container];
appendUnresolved = scrollWrites < 2;
""", max_messages=15)
    state = result["value"]["captureState"]
    assert state["domUnresolvedCount"] == 0
    assert state["unresolvedObservedContainerCount"] == 1
    assert_incomplete(result, 15)


GROUP_CONTROLS = """
load.noEarlierMsgs = true;
const chats = [940, 50, 18, 28].map((length, i) => ({id: 'chat' + i, type: 'chat', body: 'x'.repeat(length), t: 100 + i}));
model.msgs._models = [
    {id:'encrypt',type:'e2e_notification',subtype:'encrypt'},
    {id:'create',type:'gp2',subtype:'create',body:'Hineni Group Name',t:10},
    {id:'timer',type:'gp2',subtype:'ephemeral',t:20},
    ...chats
];
visible = chats.map(msg => ({__reactFiber$x: {memoizedProps: {msg}}, querySelectorAll: () => []}));
"""


def test_group_creation_name_is_not_a_missing_exportable_message():
    result = capture(GROUP_CONTROLS, max_messages=15)
    state = result["value"]["captureState"]
    assert len(result["value"]["messages"]) == 4
    assert state["modelExportableCount"] == 4
    assert state["modelCoverageComplete"]
    assert state["historyStartReached"]
    assert not state["captureIncomplete"]
    assert state["missingRequiredMessageCount"] == 0
    assert result["elapsed"] == 300


@pytest.mark.parametrize("kind", ["unknown-user-type", "e2e_notification", "gp2"])
def test_unknown_body_bearing_model_is_not_silently_excluded(kind):
    result = capture(GROUP_CONTROLS + "model.msgs._models.push({id:'unknown',type:" + json.dumps(kind) +
                     ",subtype:'unknown',body:'Potential real content',t:104});", max_messages=15)
    assert_incomplete(result, 15)
    assert result["value"]["captureState"]["modelExportableCount"] == 5
    assert result["value"]["captureState"]["missingRequiredMessageCount"] == 1


@pytest.mark.parametrize("kind,subtype,expected", [
    ("gp2", "create", False), ("gp2", "ephemeral", False),
    ("e2e_notification", "encrypt", True), ("unknown-user-type", "", True),
    ("gp2", "unknown", True), ("chat", "", True),
])
def test_python_and_js_share_narrow_control_classification(kind, subtype, expected):
    raw = {"id": "m1", "type": kind, "subtype": subtype, "body": "Real content or group name", "t": 100}
    collector = WhatsAppCollector(session=object())
    assert collector._message_has_exportable_content(raw) is expected
    assert bool(collector._extract_message_text(raw)) is expected
    recent = collector._recent_messages_for_thread(jid="group@g.us", display_name="Group", phone_or_history_id=None,
                                                 messages=[raw], preview="", max_messages=15)
    assert bool(recent) is expected
    result = capture("Object.assign(message," + json.dumps(raw) +
                     "); model.msgs._models = [message]; visible = [container]; load.noEarlierMsgs = true;", max_messages=15)
    assert bool(result["value"]["messages"]) is expected


@pytest.mark.parametrize("kind", ["image", "video", "document", "ptt", "audio", "sticker"])
def test_shared_control_exclusion_preserves_real_attachment_types(kind):
    collector = WhatsAppCollector(session=object())
    raw = {"type": kind, "id": "m1", "t": 100}
    assert collector._message_has_exportable_content(raw)
    result = capture("Object.assign(message," + json.dumps(raw) +
                     "); delete message.body; model.msgs._models = [message]; visible = [container];", max_messages=1)
    assert result["value"]["messages"][0]["attachments"]
    assert not result["value"]["captureState"]["captureIncomplete"]


@pytest.mark.parametrize("timer_step,elapsed,calls", [(1000, 9000, 9), (5000, 15000, 3)])
def test_throttled_state_waits_use_real_deadlines(timer_step, elapsed, calls):
    result = capture(f"timerStep = {timer_step}; load.isLoadingRecentMsgs = true;")
    assert_incomplete(result, 1)
    assert result["elapsed"] == elapsed
    assert result["timerCalls"] == calls
    waits = result["value"]["captureState"]["stateWaits"]
    assert [wait["budgetMs"] for wait in waits] == [6000, 3000]
    assert all(wait["timedOut"] for wait in waits)
    assert all(wait["budgetMs"] <= wait["elapsedMs"] < wait["budgetMs"] + timer_step for wait in waits)


@pytest.mark.parametrize("timer_step,elapsed,calls", [(1000, 3000, 3), (5000, 5000, 1)])
def test_throttled_identity_wait_uses_real_deadline(timer_step, elapsed, calls):
    result = capture(f"timerStep = {timer_step}; jid = 'old@lid';", expected_jids=["a@lid"])
    assert "did not become ready" in result["error"]
    assert f"{elapsed} ms" in result["error"]
    assert result["elapsed"] == elapsed
    assert result["timerCalls"] == calls


def test_identity_diagnostics_report_actual_throttled_elapsed_time():
    result = capture("timerStep = 1000; jid = now < 1000 ? 'old@lid' : 'a@lid';", expected_jids=["a@lid"])
    assert result["value"]["captureState"]["identityWaitMs"] == 1000


def test_identity_arriving_after_deadline_is_not_accepted():
    result = capture("timerStep = 5000; jid = now < 5000 ? 'old@lid' : 'a@lid';", expected_jids=["a@lid"])
    assert "did not become ready" in result["error"]
    assert result["timerCalls"] == 1


def test_state_becoming_ready_after_deadline_stays_incomplete():
    result = capture("""
timerStep = 5000; load.isLoadingRecentMsgs = now < 10000;
if (now >= 10000) {visible = [container]; model.msgs._models = [message]; load.noEarlierMsgs = true;}
""")
    assert result["value"]["messages"]
    assert_incomplete(result, 1)
    assert result["value"]["captureState"]["stateWaits"][0] == {
        "budgetMs": 6000, "elapsedMs": 10000, "timedOut": True}


def test_identity_wait_frozen_clock_iteration_guard():
    result = capture("frozenClock = true; jid = 'old@lid';", expected_jids=["a@lid"])
    assert "did not become ready" in result["error"]
    assert result["timerCalls"] == 30
