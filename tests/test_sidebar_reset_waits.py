import json
import shutil
import subprocess

import pytest

from whatsapp_collector.collector import CHAT_LIST_RESET_JS


@pytest.mark.parametrize('selected,expected_clicks,expected_wait', [
    (True, [], 0), (False, ['chats', 'all'], 600), (None, ['chats', 'all'], 600),
])
def test_sidebar_reset_does_not_reselect_active_navigation(selected, expected_clicks, expected_wait):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for browser fixtures')
    fixture = '''
        const selected = SELECTED;
        let waits = 0, clicks = [], scrolled = false;
        global.setTimeout = (fn, ms) => {waits += ms; fn();};
        const chats = {getAttribute: key => key === 'aria-label' ? 'Chats' : selected,
                       click: () => clicks.push('chats')};
        const all = {getAttribute: () => selected, click: () => clicks.push('all')};
        const pane = {scrollTop: 500, dispatchEvent: () => {scrolled = true;}};
        global.document = {querySelectorAll: () => [chats], getElementById: () => all,
                           querySelector: () => pane};
    '''.replace('SELECTED', json.dumps(str(selected).lower() if selected is not None else None))
    suffix = '.then(() => console.log(JSON.stringify({waits, clicks, scrolled, top: pane.scrollTop})));'
    result = subprocess.run([node, '-e', fixture + CHAT_LIST_RESET_JS + suffix],
                            check=True, capture_output=True, text=True)
    actual = json.loads(result.stdout)
    assert actual == {'waits': expected_wait, 'clicks': expected_clicks, 'scrolled': True, 'top': 0}
