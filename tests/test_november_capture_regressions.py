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
        const child = {id:'child',type:'image',t:102};
        model.msgs._models = [message,child]; visible = [container];
    ''')
    assert result['value']['captureState']['missingRequiredMessageCount'] == 1
    assert result['value']['captureState']['captureIncomplete']


@pytest.mark.parametrize('parent_type,child_type', [('image', 'image'), ('video', 'image'), ('image', 'video')])
@pytest.mark.parametrize('relation', ['collection', 'parentMsgKey', 'parentMsgId'])
def test_grouped_media_preserves_each_real_message_and_never_borrows_parent_bytes(parent_type, child_type, relation):
    scenario = '''
        message.type = PARENT_TYPE; message.body = ''; message.caption = 'First caption'; message.size = 42;
        const child = {id:'child',type:CHILD_TYPE,t:102,caption:'Second caption',size:84};
        if (RELATION === 'collection') message.collection = {byParentMessage: () => ({models:[child,child]})};
        else child[RELATION] = {_serialized:'m1'};
        model.msgs._models = [message,child]; visible = [container]; load.noEarlierMsgs = true;
        container.querySelectorAll = () => [{getAttribute:()=> 'blob:parent-only'}];
    '''.replace('PARENT_TYPE', repr(parent_type)).replace('CHILD_TYPE', repr(child_type)).replace('RELATION', repr(relation))
    value = capture(scenario, max_messages=15)['value']
    assert [m['id'] for m in value['messages']] == ['child', 'm1']
    assert [m['caption'] for m in value['messages']] == ['Second caption', 'First caption']
    assert value['messages'][0]['attachments'][0]['dataUrl'] is None
    assert value['messages'][0]['attachments'][0]['sizeBytes'] == 84
    assert not value['captureState']['captureIncomplete']


def test_grouped_media_respects_latest_message_limit_and_deduplicates_visible_child():
    value = capture('''
        message.type='image'; message.body='';
        const child={id:'child',type:'image',t:102,parentMsgKey:'m1'};
        model.msgs._models=[message,child];
        visible=[container,{__reactFiber$x:{memoizedProps:{msg:child}},querySelectorAll:()=>[]}];
    ''')['value']
    assert [m['id'] for m in value['messages']] == ['child']
    assert not value['captureState']['captureIncomplete']


def test_album_children_can_be_resolved_by_explicit_parent_link():
    value = capture('''
        message.type='album'; message.body='';
        const child={id:'child',type:'image',t:102,parentMsgKey:'m1'};
        model.msgs._models=[message,child]; visible=[container];
    ''')['value']
    assert value['messages'][0]['attachments'][0]['sourceMessageId'] == 'child'
    assert not value['captureState']['captureIncomplete']


def test_unrelated_media_is_not_included_and_missing_message_diagnostics_exclude_content():
    value = capture('''
        message.type='image'; message.body='';
        const child={id:'child',type:'image',t:102,parentMsgKey:'different-parent',caption:'PRIVATE CAPTION',size:84};
        message.collection={byParentMessage:()=>({models:[child]})};
        model.msgs._models=[message,child]; visible=[container];
    ''')['value']
    assert [m['id'] for m in value['messages']] == ['m1']
    state = value['captureState']
    assert state['missingRequiredMessageCount'] == 1
    assert state['missingRequiredMessages'] == [{
        'messageId':'child', 'messageType':'image', 'timestamp':'1970-01-01T00:01:42.000Z',
        'parentMessageId':'different-parent', 'fileName':None, 'sizeBytes':84,
    }]
    assert 'PRIVATE CAPTION' not in str(state)


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
