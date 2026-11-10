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
        def create_background_target_window(self, url): pass
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


@pytest.mark.parametrize("foreground", [False, True])
@pytest.mark.parametrize("scenario", ["cold", "reuse", "stale", "upgrade-flags"])
def test_foreground_requires_explicit_request_on_every_launch_path(monkeypatch, tmp_path, foreground, scenario):
    prepare(monkeypatch, tmp_path, flags=() if scenario == "upgrade-flags" else launcher.BACKGROUND_EXPORT_FLAGS)
    events = []

    class Bridge:
        def __init__(self, **kwargs):
            self.probes = 0

        def wait_until_ready(self, **kwargs):
            self.probes += 1
            if scenario == "cold" and self.probes == 1:
                raise RuntimeError("Not running")

        def wait_until_page_targets_exist(self, **kwargs):
            if scenario == "stale" and self.probes == 1:
                raise RuntimeError("No pages")

        def wait_until_target_url_exists(self, **kwargs): pass

        def create_background_target_window(self, url):
            events.append(("create-background", url))

        def place_window(self, **kwargs):
            events.append(("place", kwargs["restore"]))
            return {"windowId": 1}

        def activate_target_url(self): events.append(("activate", True))

        def wait_until_whatsapp_ready(self, **kwargs):
            events.append(("readiness", True))
            return {"ready": True}

    monkeypatch.setattr(launcher, "ChromeDevToolsBridge", Bridge)
    monkeypatch.setattr(launcher, "_foreground_chrome_profile",
        lambda profile, port: events.append(("foreground-process", (profile, port))))
    result = launcher.ensure_dedicated_whatsapp_window(profile_dir=tmp_path, debug_port=19226,
        foreground=foreground, delay_seconds=0, settle_seconds=0, wait_attempts=1)
    assert result["launched"] == (scenario != "reuse")
    assert result["foregroundRequested"] == foreground
    expected = [("create-background", launcher.DEFAULT_TARGET_URL)] if scenario != "reuse" else []
    expected += [("place", foreground)]
    if foreground:
        expected += [("activate", True), ("foreground-process", (tmp_path, 19226))]
    assert events == expected + [("readiness", True)]


@pytest.mark.parametrize("pids", [[], [101, 102]])
def test_explicit_foreground_refuses_ambiguous_process(monkeypatch, tmp_path, pids):
    monkeypatch.setattr(launcher, "chrome_profile_process_ids", lambda *args, **kwargs: pids)
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: pytest.fail("Must not activate"))
    with pytest.raises(RuntimeError, match="refusing to activate another profile"):
        launcher._foreground_chrome_profile(tmp_path, 19226)


def test_explicit_foreground_uses_only_exact_profile_port_pid(monkeypatch, tmp_path):
    calls = []

    def pids(profile, *, debug_port):
        assert profile == tmp_path and debug_port == 19226
        return [101]

    monkeypatch.setattr(launcher, "chrome_profile_process_ids", pids)
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    launcher._foreground_chrome_profile(tmp_path, 19226)
    assert len(calls) == 1
    assert calls[0][0][0] == ["osascript", "-l", "JavaScript"]
    assert "runningApplicationWithProcessIdentifier(101)" in calls[0][1]["input"]
