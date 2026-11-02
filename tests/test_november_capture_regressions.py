import pytest

from whatsapp_collector.collector import WhatsAppCollector
from test_opened_chat_state_waits import capture


def test_album_children_count_as_covered_only_when_exported_as_attachments():
    result = capture('''
        message.type = 'album'; message.body = ''; message.t = 100;
        const child = {id:'child',type:'image',t:102,parentMsgKey:'m1',size:42};
        message.collection = {byParentMessage: () => ({models:[child]})};
        model.msgs._models = [message,child]; visible = [container];
    ''')
    value = result['value']
    assert value['messages'][0]['t'] == 100
    assert value['messages'][0]['attachments'][0]['sourceMessageId'] == 'child'
    assert value['captureState']['missingRequiredMessageCount'] == 0
    assert not value['captureState']['captureIncomplete']


def test_album_does_not_hide_unrepresented_child():
    result = capture('''
        message.type = 'album'; message.body = ''; message.t = 100;
        const child = {id:'child',type:'image',t:102,parentMsgKey:'m1'};
        model.msgs._models = [message,child]; visible = [container];
    ''')
    assert result['value']['captureState']['missingRequiredMessageCount'] == 1
    assert result['value']['captureState']['captureIncomplete']


def test_shared_location_is_real_content_not_its_base64_map_thumbnail():
    result = capture("message.type='location'; message.body='/9j/'+'A'.repeat(200); message.lat=32.08; message.lng=34.76; model.msgs._models=[message]; visible=[container];")
    message = result['value']['messages'][0]
    assert WhatsAppCollector._extract_message_text(message) == 'Location: 32.08, 34.76 (https://maps.google.com/?q=32.08,34.76)'
    assert not result['value']['captureState']['captureIncomplete']


def test_thumbnail_only_without_coordinates_does_not_consume_message_limit():
    result = capture("message.type='location'; message.body='/9j/'+'A'.repeat(200); model.msgs._models=[message]; visible=[container]; load.noEarlierMsgs=true;")
    assert result['value']['messages'] == []


@pytest.mark.parametrize('lat,lng', [(True,0),(91,0),(0,181),(float('nan'),0),(0,float('inf')),('32',34)])
def test_invalid_location_is_not_fabricated(lat,lng):
    assert WhatsAppCollector._extract_message_text({'type':'location','lat':lat,'lng':lng}) is None


def test_tall_media_can_require_more_than_eight_scrolls_for_fifteen_messages():
    result = capture('''
        panel.scrollHeight=10000; panel.clientHeight=400;
        const messages = Array.from({length:20},(_,i)=>({id:'media'+i,type:'image',t:100+i}));
        model.msgs._models=messages;
        const index=Math.min(19,Math.floor(panel.scrollTop/500));
        visible=[{__reactFiber$x:{memoizedProps:{msg:messages[index]}},querySelectorAll:()=>[]}];
    ''', max_messages=15)
    assert len(result['value']['messages']) == 15
    assert not result['value']['captureState']['captureIncomplete']
    assert result['scrollWrites'] > 8


def test_indexeddb_location_preserves_valid_zero_coordinates():
    assert WhatsAppCollector._extract_message_text({'type':'location','lat':0,'lng':0}) == 'Location: 0, 0 (https://maps.google.com/?q=0,0)'


def test_identity_resolved_from_named_sidebar_capture_is_verified(monkeypatch):
    payload = {'openedChatJid':'0@c.us','openedChatTitle':'WhatsApp Business',
               'messages':[{'id':'false_0@c.us_1','t':100,'type':'chat','body':'Message'}],
               'captureState':{'emptyEvidence':{'identityStable':True,'modelAvailable':True}}}
    class Session:
        def click_point(self, expression): pass
        def run_json(self, script): return payload
    collector = WhatsAppCollector(Session())
    monkeypatch.setattr(collector, '_reset_chat_list_to_top', lambda:None)
    monkeypatch.setattr(collector, '_chat_open_identity', lambda *args: (['WhatsApp Business'],[]))
    collector._opened_chat_recent_messages_for_chat('WhatsApp Business',max_messages=15)
    assert collector._opened_capture_details['WhatsApp Business']['identityVerified']


def test_name_match_cannot_hide_foreign_message_identity():
    collector = WhatsAppCollector(object())
    assert not collector._opened_chat_matches_identity(
        {'openedChatJid':'0@c.us','openedChatTitle':'WhatsApp Business',
         'messages':[{'id':'false_other@lid_1'}]}, candidates=['WhatsApp Business'], expected_jids=[])


def test_whole_capture_durations_keep_float_schema_after_json_serialization():
    import json
    state = {'identityWaitMs':0,'stateWaits':[{'budgetMs':3000,'elapsedMs':300,'timedOut':False}]}
    WhatsAppCollector._normalize_capture_timing(state)
    decoded = json.loads(json.dumps(state))
    assert type(decoded['identityWaitMs']) is float
    assert type(decoded['stateWaits'][0]['elapsedMs']) is float
    assert type(decoded['stateWaits'][0]['budgetMs']) is int


def test_capture_timing_normalization_keeps_missing_evidence_missing():
    state = {}
    WhatsAppCollector._normalize_capture_timing(state)
    assert state == {}
