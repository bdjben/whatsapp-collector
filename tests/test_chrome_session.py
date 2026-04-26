from unittest.mock import Mock

import pytest

from whatsapp_collector.chrome_session import ChromeTarget, ChromeWhatsAppSession


def test_readonly_guard_rejects_composer_and_send_verbs() -> None:
    session = ChromeWhatsAppSession(runner=Mock())

    with pytest.raises(ValueError, match="read-only"):
        session.assert_readonly("document.querySelector('[contenteditable=true]').click()")

    with pytest.raises(ValueError, match="read-only"):
        session.assert_readonly("sendButton.click()")


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
