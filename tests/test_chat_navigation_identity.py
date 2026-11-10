import json
import subprocess

from whatsapp_collector.devtools_bridge import CHAT_IDENTITY_HELPERS_JS
from test_opened_chat_state_waits import capture


def test_title_comes_from_chat_heading_not_contact_info_tooltip():
    script = '''global.document = {
        querySelector: selector => selector.includes('conversation-info-header-chat-title')
            ? {textContent:'WhatsApp Business'} : null,
        querySelectorAll: () => [{getAttribute: () => 'Contact info', textContent:'Contact info'}]
    };''' + CHAT_IDENTITY_HELPERS_JS + 'console.log(JSON.stringify(waCollectorChatTitle()));'
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == 'WhatsApp Business'


def test_named_capture_waits_for_requested_conversation():
    result = capture("chatTitle = now < 800 ? 'Previous Chat' : 'Example Chat';",
                     expected_names=['Example Chat'])
    assert result['value']['openedChatTitle'] == 'Example Chat'
    assert result['value']['captureState']['identityWaitMs'] >= 800


def test_named_capture_rejects_stale_header():
    result = capture(expected_names=['Different Chat'])
    assert 'expected opened chat did not become ready' in result['error']


def test_correct_name_does_not_override_wrong_expected_id():
    result = capture(expected_names=['Example Chat'], expected_jids=['other@lid'])
    assert 'expected opened chat did not become ready' in result['error']
