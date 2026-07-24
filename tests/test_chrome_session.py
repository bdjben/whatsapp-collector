import base64
import hashlib
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from whatsapp_collector.chrome_session import ChromeTarget, ChromeWhatsAppSession
from whatsapp_collector.collector import WhatsAppCollector


def test_readonly_guard_rejects_composer_and_send_verbs() -> None:
    session = ChromeWhatsAppSession(runner=Mock())

    with pytest.raises(ValueError, match="read-only"):
        session.assert_readonly("document.querySelector('[contenteditable=true]').click()")

    with pytest.raises(ValueError, match="read-only"):
        session.assert_readonly("sendButton.click()")


def test_readonly_guard_rejects_input_mutations_and_attach_controls() -> None:
    session = ChromeWhatsAppSession(runner=Mock())

    with pytest.raises(ValueError, match="input value assignment"):
        session.assert_readonly('document.querySelector("textarea").value = "hello"')

    with pytest.raises(ValueError, match="attachment control"):
        session.assert_readonly('document.querySelector("[aria-label=\\"Attach\\"]").click()')


def test_readonly_guard_allows_collector_attachment_metadata_script() -> None:
    session = ChromeWhatsAppSession(runner=Mock())

    session.assert_readonly(WhatsAppCollector._opened_chat_recent_messages_js(max_messages=15))


def test_run_json_wraps_javascript_in_applescript() -> None:
    runner = Mock(return_value='{"ok":true}')
    session = ChromeWhatsAppSession(runner=runner)

    result = session.run_json("JSON.stringify({ok:true})")

    assert result == {"ok": True}
    [script] = runner.call_args.args
    assert 'tell application "Google Chrome"' in script
    assert 'execute javascript' in script
    assert 'JSON.stringify({ok:true})' in script


def test_run_json_raises_for_invalid_json() -> None:
    session = ChromeWhatsAppSession(runner=Mock(return_value='not-json'))

    with pytest.raises(ValueError, match="Invalid JSON"):
        session.run_json("JSON.stringify({ok:true})")


def test_run_async_json_polls_window_result_variable() -> None:
    runner = Mock(side_effect=["started", "", '{"stores":["chat","label"]}'])
    session = ChromeWhatsAppSession(runner=runner)

    result = session.run_async_json(
        "indexedDB.open('model-storage')",
        result_var="__hermes_probe",
        attempts=3,
        delay_seconds=0,
    )

    assert result == {"stores": ["chat", "label"]}
    assert runner.call_count == 3


def test_run_json_targets_marker_window_when_configured() -> None:
    runner = Mock(return_value='{"ok":true}')
    session = ChromeWhatsAppSession(
        runner=runner,
        target=ChromeTarget(
            marker_title="WhatsApp Collector",
            marker_url_substring="whatsapp-collector",
            target_url_substring="web.whatsapp.com/",
        ),
    )

    result = session.run_json("JSON.stringify({ok:true})")

    assert result == {"ok": True}
    [script] = runner.call_args.args
    assert 'WhatsApp Collector' in script
    assert 'whatsapp-collector' in script
    assert 'web.whatsapp.com/' in script
    assert 'No Chrome tab matched configured marker/target selector' in script


def test_run_json_uses_devtools_bridge_when_debug_port_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeBridge:
        def __init__(self, *, port, marker_title, marker_url_substring, target_url_substring):
            seen['init'] = {
                'port': port,
                'marker_title': marker_title,
                'marker_url_substring': marker_url_substring,
                'target_url_substring': target_url_substring,
            }

        def evaluate(self, expression: str) -> str:
            seen['expression'] = expression
            return '{"ok":true}'

    monkeypatch.setenv('WA_CHROME_DEBUG_PORT', '19220')
    monkeypatch.setattr('whatsapp_collector.chrome_session.ChromeDevToolsBridge', FakeBridge)

    session = ChromeWhatsAppSession(target=ChromeTarget(marker_title='WhatsApp Collector', marker_url_substring='whatsapp-collector', target_url_substring='web.whatsapp.com/'))
    result = session.run_json('JSON.stringify({ok:true})')

    assert result == {'ok': True}
    assert seen['init'] == {
        'port': 19220,
        'marker_title': 'WhatsApp Collector',
        'marker_url_substring': 'whatsapp-collector',
        'target_url_substring': 'web.whatsapp.com/',
    }
    assert seen['expression'] == 'JSON.stringify({ok:true})'


def test_run_async_json_uses_single_devtools_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeBridge:
        def __init__(self, **kwargs):
            pass

        def evaluate(self, expression: str) -> str:
            seen['expression'] = expression
            return '{"stores":["chat","label"]}'

    monkeypatch.setenv('WA_CHROME_DEBUG_PORT', '19220')
    monkeypatch.setattr('whatsapp_collector.chrome_session.ChromeDevToolsBridge', FakeBridge)

    session = ChromeWhatsAppSession()
    result = session.run_async_json("indexedDB.open('model-storage')", result_var='__hermes_probe', attempts=3, delay_seconds=0.2)

    assert result == {'stores': ['chat', 'label']}
    expression = seen['expression']
    assert "indexedDB.open('model-storage')" in expression
    assert 'window["__hermes_probe"]' in expression
    assert 'setTimeout(resolve, 200)' in expression


def test_run_async_json_isolates_default_result_variables_between_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expressions: list[str] = []

    class FakeBridge:
        def __init__(self, **kwargs):
            pass

        def evaluate(self, expression: str) -> str:
            expressions.append(expression)
            return '{"ok":true}'

    monkeypatch.setattr('whatsapp_collector.chrome_session.ChromeDevToolsBridge', FakeBridge)
    session = ChromeWhatsAppSession(debug_port=19220)
    starter = 'window.__hermes_async_result = null; window.__hermes_async_result = JSON.stringify({ok:true})'

    assert session.run_async_json(starter) == {'ok': True}
    assert session.run_async_json(starter) == {'ok': True}

    first_name = f'__hermes_async_result_{os.getpid()}_1'
    second_name = f'__hermes_async_result_{os.getpid()}_2'
    assert first_name in expressions[0]
    assert second_name not in expressions[0]
    assert second_name in expressions[1]
    assert first_name not in expressions[1]
    assert 'delete window[' in expressions[0]


def test_download_wait_matches_collision_name_and_whatsapp_hash(tmp_path: Path) -> None:
    data = b"OggS" + b"voice-note"
    path = tmp_path / "voice-note (3).ogg"
    path.write_bytes(data)
    file_hash = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")

    matched = ChromeWhatsAppSession._wait_for_download(
        [tmp_path],
        baseline={},
        file_hash=file_hash,
        expected_size=len(data),
        expected_file_name="voice-note.ogg",
        timeout_seconds=1,
    )

    assert matched == path
    assert ChromeWhatsAppSession._download_name_matches("voice-note (3).ogg", "voice-note.ogg") is True
