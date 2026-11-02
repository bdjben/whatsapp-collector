from pathlib import Path

import pytest

from whatsapp_collector import launcher


def process(pid, profile, port, flags=()):
    return f"{pid} {launcher.DEFAULT_CHROME_BINARY} --user-data-dir={profile} --remote-debugging-port={port} {' '.join(flags)}"


def test_missing_flags_only_match_exact_profile_port_and_main_process(monkeypatch, tmp_path):
    output = "\n".join([
        process(101, tmp_path, 19226),
        process(102, tmp_path, 19226, launcher.BACKGROUND_EXPORT_FLAGS),
        process(103, str(tmp_path) + '-other', 19226),
        process(104, tmp_path, 19220),
        process(105, tmp_path, 19226, [f + '-wrong' for f in launcher.BACKGROUND_EXPORT_FLAGS]),
    ])
    monkeypatch.setattr(launcher, '_chrome_process_output', lambda: output)
    assert launcher._owned_browser_missing_background_flags(tmp_path, 19226) == [101, 105]


def prepare(monkeypatch, tmp_path, flags=(), conflict=False, remaining=False, launch_failure=False):
    calls = []
    output = process(101, tmp_path if not conflict else str(tmp_path) + '-other', 19226, flags)
    monkeypatch.setattr(launcher, '_chrome_process_output', lambda: output)
    monkeypatch.setattr(launcher, 'load_display_frames', lambda: {'test': launcher.DisplayFrame('test', 0, 0, 1000, 800)})
    class Bridge:
        def __init__(self, **kwargs): pass
        def wait_until_ready(self, **kwargs): return {}
        def wait_until_page_targets_exist(self, **kwargs): return []
        def wait_until_target_url_exists(self, **kwargs): return []
        def place_window(self, **kwargs): return {'windowId': 1}
    monkeypatch.setattr(launcher, 'ChromeDevToolsBridge', Bridge)
    def terminate(profile, **kwargs):
        calls.append(('terminate', profile, kwargs))
        return {'remainingProcessIds': [101] if remaining else []}
    def launch(**kwargs):
        calls.append(('launch', kwargs))
        if launch_failure:
            raise RuntimeError('launch failed')
    monkeypatch.setattr(launcher, 'terminate_profile_processes', terminate)
    monkeypatch.setattr(launcher, 'terminate_debug_port_processes', lambda *args, **kwargs: pytest.fail('port-only termination is forbidden'))
    monkeypatch.setattr(launcher, 'launch_dedicated_chrome_window', launch)
    return calls


def ensure(tmp_path):
    return launcher.ensure_dedicated_whatsapp_window(profile_dir=tmp_path, debug_port=19226,
        delay_seconds=0, settle_seconds=0, wait_attempts=1)


def test_existing_owned_old_flags_get_one_pid_bound_relaunch(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path)
    result = ensure(tmp_path)
    assert result['backgroundFlagsUpgraded'] is True
    assert result['launched'] is True
    assert calls[0] == ('terminate', tmp_path, {'debug_port':19226, 'expected_pids':{101}})
    assert [c[0] for c in calls] == ['terminate', 'launch']


def test_existing_configured_browser_keeps_alive(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, flags=launcher.BACKGROUND_EXPORT_FLAGS)
    result = ensure(tmp_path)
    assert result['launched'] is False
    assert result['backgroundFlagsUpgraded'] is False
    assert not calls


def test_unrelated_port_owner_is_not_restarted(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, conflict=True)
    with pytest.raises(RuntimeError, match='another Chrome profile'):
        ensure(tmp_path)
    assert not calls


def test_failed_termination_never_launches_competing_instance(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, remaining=True)
    with pytest.raises(RuntimeError, match='did not stop'):
        ensure(tmp_path)
    assert [c[0] for c in calls] == ['terminate']


def test_failed_relaunch_is_not_retried(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, launch_failure=True)
    with pytest.raises(RuntimeError, match='launch failed'):
        ensure(tmp_path)
    assert [c[0] for c in calls] == ['terminate', 'launch']
