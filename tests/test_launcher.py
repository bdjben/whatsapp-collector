from pathlib import Path

import pytest

from whatsapp_collector.launcher import (
    DisplayFrame,
    choose_display,
    debug_port_has_profile_conflict,
    edge_hidden_bounds,
    ensure_dedicated_whatsapp_window,
    launch_dedicated_chrome_window,
    marker_data_url,
    terminate_debug_port_processes,
    terminate_profile_processes,
    visible_bounds,
)


def test_marker_data_url_defaults_to_product_name() -> None:
    url = marker_data_url()
    assert url.startswith("data:text/html,")
    assert "WhatsApp%20Collector" in url
    assert "Hermes" not in url


def test_launch_dedicated_chrome_window_opens_background_chrome_instance(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pass

    def fake_popen(command, stdout=None, stderr=None):
        captured["command"] = command
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return FakeProcess()

    monkeypatch.setattr("whatsapp_collector.launcher.subprocess.Popen", fake_popen)

    launch_dedicated_chrome_window(
        chrome_binary="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        profile_dir=tmp_path,
        marker_title="WhatsApp Collector",
        target_url="https://web.whatsapp.com/",
        debug_port=19220,
    )

    command = captured["command"]
    assert command[:5] == ["open", "-g", "-n", "-a", "Google Chrome"]
    assert command[5] == "--args"
    assert f"--user-data-dir={tmp_path}" in command
    assert "--remote-debugging-port=19220" in command
    assert "--new-window" in command
    assert "https://web.whatsapp.com/" in command


def test_terminate_profile_processes_waits_until_profile_processes_exit(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    poll_results = [
        "20518 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/profile",
        "",
    ]

    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(command, check=False, capture_output=True, text=True):
        calls.append(command)
        if command[:2] == ["pkill", "-f"]:
            return Completed(returncode=0)
        if command[:2] == ["pgrep", "-fal"]:
            stdout = poll_results.pop(0) if poll_results else ""
            return Completed(stdout=stdout, returncode=0 if stdout else 1)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("whatsapp_collector.launcher.subprocess.run", fake_run)
    monkeypatch.setattr("whatsapp_collector.launcher.time.sleep", lambda *_args, **_kwargs: None)

    terminate_profile_processes(tmp_path)

    assert calls[0] == ["pkill", "-f", str(tmp_path)]
    assert calls[1] == ["pgrep", "-fal", str(tmp_path)]
    assert calls[2] == ["pgrep", "-fal", str(tmp_path)]


def test_terminate_debug_port_processes_kills_only_configured_devtools_port(monkeypatch) -> None:
    calls: list[list[str]] = []
    poll_results = [
        "20518 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=19220 --user-data-dir=/tmp/old-profile",
        "",
    ]

    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(command, check=False, capture_output=True, text=True):
        calls.append(command)
        if command[:2] == ["pkill", "-f"]:
            return Completed(returncode=0)
        if command[:2] == ["pgrep", "-fal"]:
            stdout = poll_results.pop(0) if poll_results else ""
            return Completed(stdout=stdout, returncode=0 if stdout else 1)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("whatsapp_collector.launcher.subprocess.run", fake_run)
    monkeypatch.setattr("whatsapp_collector.launcher.time.sleep", lambda *_args, **_kwargs: None)

    terminate_debug_port_processes(19220)

    assert calls[0] == ["pkill", "-f", "remote-debugging-port=19220"]
    assert calls[1] == ["pgrep", "-fal", "remote-debugging-port=19220"]
    assert calls[2] == ["pgrep", "-fal", "remote-debugging-port=19220"]


def test_debug_port_has_profile_conflict_detects_stale_profile_owner(monkeypatch, tmp_path: Path) -> None:
    desired = tmp_path / "Chrome Profile"
    old = tmp_path / "old-profile"

    class Completed:
        def __init__(self, stdout: str = ""):
            self.stdout = stdout

    monkeypatch.setattr(
        "whatsapp_collector.launcher.subprocess.run",
        lambda *args, **kwargs: Completed(
            f"101 Google Chrome --remote-debugging-port=19220 --user-data-dir={old}\n"
            f"202 Google Chrome --remote-debugging-port=19220 --user-data-dir={desired}\n"
        ),
    )

    assert debug_port_has_profile_conflict(19220, desired) is True


def test_debug_port_has_profile_conflict_accepts_requested_profile_owner(monkeypatch, tmp_path: Path) -> None:
    desired = tmp_path / "Chrome Profile"

    class Completed:
        def __init__(self, stdout: str = ""):
            self.stdout = stdout

    monkeypatch.setattr(
        "whatsapp_collector.launcher.subprocess.run",
        lambda *args, **kwargs: Completed(
            f"202 Google Chrome --remote-debugging-port=19220 --user-data-dir={desired}\n"
            f"203 Google Chrome Helper --remote-debugging-port=19220 --user-data-dir={desired}\n"
        ),
    )

    assert debug_port_has_profile_conflict(19220, desired) is False


def test_choose_display_uses_requested_or_first_available_display() -> None:
    frames = {
        "LAPTOP": DisplayFrame(name="LAPTOP", x=0, y=0, width=1728, height=1117),
        "EXTERNAL": DisplayFrame(name="EXTERNAL", x=-2123, y=1689, width=1920, height=1080),
    }
    display, fallback_used = choose_display(frames, "EXTERNAL")
    assert display.name == "EXTERNAL"
    assert fallback_used is False

    display, fallback_used = choose_display(frames, None)
    assert display.name == "LAPTOP"
    assert fallback_used is False

    display, fallback_used = choose_display(frames, "MISSING")
    assert display.name == "LAPTOP"
    assert fallback_used is True


def test_choose_display_matches_case_insensitive_display_names() -> None:
    frames = {
        "WHATSAPPMONITOR": DisplayFrame(name="WHATSAPPMONITOR", x=4224, y=-2351, width=2304, height=1440),
        "LAPTOP": DisplayFrame(name="LAPTOP", x=0, y=0, width=1728, height=1117),
    }

    display, fallback_used = choose_display(frames, "WhatsAppMonitor")

    assert display.name == "WHATSAPPMONITOR"
    assert fallback_used is False


def test_edge_hidden_bounds_uses_edge_sliver_positioning() -> None:
    display = DisplayFrame(name="EXTERNAL", x=-2123, y=1689, width=1920, height=1080)
    assert edge_hidden_bounds(display) == {
        "left": -251,
        "top": 2525,
        "width": 420,
        "height": 220,
    }


def test_visible_bounds_insets_window_inside_display() -> None:
    display = DisplayFrame(name="LINKEDINMONITOR", x=-2935, y=-2608, width=2560, height=1440)

    assert visible_bounds(display) == {
        "left": -2815,
        "top": -2528,
        "width": 1280,
        "height": 960,
    }


def test_ensure_dedicated_whatsapp_window_launches_when_debug_port_is_not_ready(monkeypatch, tmp_path: Path) -> None:
    launched: dict[str, object] = {}
    placed: dict[str, object] = {}
    sleeps: list[float] = []
    state = {"ready_calls": 0}

    class FakeBridge:
        def __init__(self, *, port, marker_title, marker_url_substring, target_url_substring):
            launched['bridge_init'] = {
                'port': port,
                'marker_title': marker_title,
                'marker_url_substring': marker_url_substring,
                'target_url_substring': target_url_substring,
            }

        def wait_until_ready(self, *, attempts, delay_seconds):
            state['ready_calls'] += 1
            if state['ready_calls'] == 1:
                raise RuntimeError('not ready yet')
            return {'Browser': 'Chrome/147'}

        def wait_until_page_targets_exist(self, *, attempts, delay_seconds):
            return [{'id': 'page-1', 'type': 'page'}]

        def place_window(self, *, left, top, width, height):
            placed.update({'left': left, 'top': top, 'width': width, 'height': height})
            return {'windowId': 4321, 'targetId': 'target-1'}

    def fake_load_display_frames():
        return {"PRIMARY": DisplayFrame(name="PRIMARY", x=-2123, y=1689, width=1920, height=1080)}

    def fake_launch_dedicated_chrome_window(**kwargs):
        launched['launch'] = kwargs

    monkeypatch.setattr("whatsapp_collector.launcher.load_display_frames", fake_load_display_frames)
    monkeypatch.setattr("whatsapp_collector.launcher.ChromeDevToolsBridge", FakeBridge)
    monkeypatch.setattr("whatsapp_collector.launcher.debug_port_has_profile_conflict", lambda debug_port, profile_dir: False)
    monkeypatch.setattr("whatsapp_collector.launcher.terminate_profile_processes", lambda profile_dir: launched.setdefault('terminated_profile', profile_dir))
    monkeypatch.setattr("whatsapp_collector.launcher.terminate_debug_port_processes", lambda debug_port: launched.setdefault('terminated_debug_port', debug_port))
    monkeypatch.setattr("whatsapp_collector.launcher.launch_dedicated_chrome_window", fake_launch_dedicated_chrome_window)
    monkeypatch.setattr("whatsapp_collector.launcher.time.sleep", lambda seconds: sleeps.append(seconds))

    payload = ensure_dedicated_whatsapp_window(profile_dir=tmp_path, wait_attempts=5, delay_seconds=0, settle_seconds=15)

    assert payload["windowId"] == 4321
    assert payload["targetId"] == 'target-1'
    assert payload["launched"] is True
    assert payload["debugPort"] == 19220
    assert payload["requestedDisplay"] is None
    assert payload["displayFallbackUsed"] is False
    assert launched['launch']['profile_dir'] == tmp_path
    assert launched['launch']['debug_port'] == 19220
    assert placed == {'left': -251, 'top': 2525, 'width': 420, 'height': 220}
    assert sleeps == [15]


def test_ensure_dedicated_whatsapp_window_restarts_stale_devtools_port_without_page_targets(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {"terminated_profiles": [], "terminated_ports": []}
    state = {"page_calls": 0}

    class FakeBridge:
        def __init__(self, **kwargs):
            calls["bridge"] = kwargs

        def wait_until_ready(self, *, attempts, delay_seconds):
            return {'Browser': 'Chrome/147'}

        def wait_until_page_targets_exist(self, *, attempts, delay_seconds):
            state["page_calls"] += 1
            if state["page_calls"] == 1:
                raise RuntimeError('port is alive but has no page targets')
            return [{'id': 'page-1', 'type': 'page'}]

        def place_window(self, *, left, top, width, height):
            return {'windowId': 2026, 'targetId': 'page-1'}

    monkeypatch.setattr(
        "whatsapp_collector.launcher.load_display_frames",
        lambda: {"PRIMARY": DisplayFrame(name="PRIMARY", x=0, y=0, width=1920, height=1080)},
    )
    monkeypatch.setattr("whatsapp_collector.launcher.ChromeDevToolsBridge", FakeBridge)
    monkeypatch.setattr("whatsapp_collector.launcher.debug_port_has_profile_conflict", lambda debug_port, profile_dir: False)
    monkeypatch.setattr(
        "whatsapp_collector.launcher.terminate_profile_processes",
        lambda profile_dir: calls["terminated_profiles"].append(profile_dir),
    )
    monkeypatch.setattr(
        "whatsapp_collector.launcher.terminate_debug_port_processes",
        lambda debug_port: calls["terminated_ports"].append(debug_port),
    )
    monkeypatch.setattr("whatsapp_collector.launcher.launch_dedicated_chrome_window", lambda **kwargs: calls.setdefault("launch", kwargs))
    monkeypatch.setattr("whatsapp_collector.launcher.time.sleep", lambda *_args, **_kwargs: None)

    payload = ensure_dedicated_whatsapp_window(profile_dir=tmp_path, wait_attempts=5, delay_seconds=0, settle_seconds=0)

    assert payload["windowId"] == 2026
    assert payload["launched"] is True
    assert calls["terminated_profiles"] == [tmp_path]
    assert calls["terminated_ports"] == [19220]
    assert calls["launch"]["profile_dir"] == tmp_path


def test_ensure_dedicated_whatsapp_window_reuses_existing_debug_port(monkeypatch, tmp_path: Path) -> None:
    placed: dict[str, object] = {}

    class FakeBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_ready(self, *, attempts, delay_seconds):
            return {'Browser': 'Chrome/147'}

        def wait_until_page_targets_exist(self, *, attempts, delay_seconds):
            return [{'id': 'page-1', 'type': 'page'}]

        def place_window(self, *, left, top, width, height):
            placed.update({'left': left, 'top': top, 'width': width, 'height': height})
            return {'windowId': 777, 'targetId': 'target-777'}

    monkeypatch.setattr(
        "whatsapp_collector.launcher.load_display_frames",
        lambda: {"EXTERNAL": DisplayFrame(name="EXTERNAL", x=0, y=0, width=1920, height=1080)},
    )
    monkeypatch.setattr("whatsapp_collector.launcher.ChromeDevToolsBridge", FakeBridge)
    monkeypatch.setattr("whatsapp_collector.launcher.debug_port_has_profile_conflict", lambda debug_port, profile_dir: False)
    monkeypatch.setattr(
        "whatsapp_collector.launcher.launch_dedicated_chrome_window",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not relaunch")),
    )

    payload = ensure_dedicated_whatsapp_window(profile_dir=tmp_path, wait_attempts=1, delay_seconds=0)

    assert payload["windowId"] == 777
    assert payload["targetId"] == 'target-777'
    assert payload["launched"] is False
    assert payload["displayFallbackUsed"] is False
    assert placed == {'left': 1872, 'top': 836, 'width': 420, 'height': 220}


def test_ensure_dedicated_whatsapp_window_supports_visible_placement_mode(monkeypatch, tmp_path: Path) -> None:
    placed: dict[str, object] = {}

    class FakeBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_ready(self, *, attempts, delay_seconds):
            return {'Browser': 'Chrome/147'}

        def wait_until_page_targets_exist(self, *, attempts, delay_seconds):
            return [{'id': 'page-1', 'type': 'page'}]

        def place_window(self, *, left, top, width, height):
            placed.update({'left': left, 'top': top, 'width': width, 'height': height})
            return {'windowId': 707, 'targetId': 'target-707'}

    monkeypatch.setattr(
        "whatsapp_collector.launcher.load_display_frames",
        lambda: {"WHATSAPPMONITOR": DisplayFrame(name="WHATSAPPMONITOR", x=4224, y=-2351, width=2304, height=1440)},
    )
    monkeypatch.setattr("whatsapp_collector.launcher.ChromeDevToolsBridge", FakeBridge)
    monkeypatch.setattr("whatsapp_collector.launcher.debug_port_has_profile_conflict", lambda debug_port, profile_dir: False)
    monkeypatch.setattr(
        "whatsapp_collector.launcher.launch_dedicated_chrome_window",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not relaunch")),
    )

    payload = ensure_dedicated_whatsapp_window(
        profile_dir=tmp_path,
        display_name="WhatsAppMonitor",
        placement_mode="visible",
        wait_attempts=1,
        delay_seconds=0,
    )

    assert payload["windowId"] == 707
    assert payload["requestedDisplay"] == 'WhatsAppMonitor'
    assert payload["display"]["name"] == 'WHATSAPPMONITOR'
    assert payload["placementMode"] == 'visible'
    assert payload["displayFallbackUsed"] is False
    assert placed == {'left': 4344, 'top': -2271, 'width': 1280, 'height': 960}


def test_ensure_dedicated_whatsapp_window_falls_back_when_requested_display_missing(monkeypatch, tmp_path: Path) -> None:
    placed: dict[str, object] = {}

    class FakeBridge:
        def __init__(self, **kwargs):
            pass

        def wait_until_ready(self, *, attempts, delay_seconds):
            return {'Browser': 'Chrome/147'}

        def wait_until_page_targets_exist(self, *, attempts, delay_seconds):
            return [{'id': 'page-1', 'type': 'page'}]

        def place_window(self, *, left, top, width, height):
            placed.update({'left': left, 'top': top, 'width': width, 'height': height})
            return {'windowId': 999, 'targetId': 'target-999'}

    monkeypatch.setattr(
        "whatsapp_collector.launcher.load_display_frames",
        lambda: {"LAPTOP": DisplayFrame(name="LAPTOP", x=0, y=0, width=1728, height=1117)},
    )
    monkeypatch.setattr("whatsapp_collector.launcher.ChromeDevToolsBridge", FakeBridge)
    monkeypatch.setattr("whatsapp_collector.launcher.debug_port_has_profile_conflict", lambda debug_port, profile_dir: False)
    monkeypatch.setattr(
        "whatsapp_collector.launcher.launch_dedicated_chrome_window",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not relaunch")),
    )

    payload = ensure_dedicated_whatsapp_window(profile_dir=tmp_path, display_name="MISSING", wait_attempts=1, delay_seconds=0)

    assert payload["windowId"] == 999
    assert payload["requestedDisplay"] == 'MISSING'
    assert payload["display"]["name"] == 'LAPTOP'
    assert payload["displayFallbackUsed"] is True
    assert placed == {'left': 1680, 'top': 873, 'width': 420, 'height': 220}
