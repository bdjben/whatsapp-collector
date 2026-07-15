from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

from whatsapp_collector.devtools_bridge import ChromeDevToolsBridge

DEFAULT_CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PROFILE_DIR = Path("~/.whatsapp-collector/chrome-profile").expanduser()
DEFAULT_MARKER_TITLE = "WhatsApp Collector"
DEFAULT_MARKER_URL_SUBSTRING = "whatsapp-collector"
DEFAULT_TARGET_URL = "https://web.whatsapp.com/"
DEFAULT_DISPLAY_NAME = None
DEFAULT_FALLBACK_DISPLAY_NAME = "LAPTOP"
DEFAULT_DEBUG_PORT = 19220
DEFAULT_PLACEMENT_MODE = "edge-hidden"
DEFAULT_SETTLE_SECONDS = 15.0
EDGE_PLACEMENT_VISIBLE_SLIVER = 48
EDGE_PLACEMENT_WIDTH = 420
EDGE_PLACEMENT_HEIGHT = 220
EDGE_PLACEMENT_BOTTOM_MARGIN = 24
VISIBLE_PLACEMENT_WIDTH = 1280
VISIBLE_PLACEMENT_HEIGHT = 960
VISIBLE_PLACEMENT_HORIZONTAL_MARGIN = 120
VISIBLE_PLACEMENT_TOP_MARGIN = 80


class ChromeNotInstalledError(RuntimeError):
    pass


@dataclass(frozen=True)
class DisplayFrame:
    name: str
    x: int
    y: int
    width: int
    height: int

    def bounds_list(self) -> list[int]:
        return [self.x, self.y, self.x + self.width, self.y + self.height]


def _run(command: list[str], *, capture_output: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=capture_output, text=True)


def _run_applescript(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["osascript"],
        input=script,
        check=True,
        capture_output=True,
        text=True,
    )


def _is_main_chrome_command(command: str) -> bool:
    executable = command.split(" --", 1)[0].rstrip()
    return (
        executable.endswith("/Google Chrome.app/Contents/MacOS/Google Chrome")
        and " -c " not in executable
    )


def _command_has_exact_argument(command: str, argument: str) -> bool:
    start = 0
    while True:
        index = command.find(argument, start)
        if index < 0:
            return False
        before_ok = index == 0 or command[index - 1].isspace()
        end = index + len(argument)
        after_ok = end == len(command) or command[end].isspace()
        if before_ok and after_ok:
            return True
        start = index + 1


def _matching_chrome_process_ids(
    output: str,
    *,
    required_arguments: tuple[str, ...] = (),
    expected_pids: set[int] | None = None,
) -> list[int]:
    pids: list[int] = []
    current_pid = os.getpid()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1] if len(parts) > 1 else ""
        if pid == current_pid:
            continue
        if expected_pids is not None and pid not in expected_pids:
            continue
        if not _is_main_chrome_command(command):
            continue
        if not all(_command_has_exact_argument(command, argument) for argument in required_arguments):
            continue
        pids.append(pid)
    return pids


def _chrome_process_output() -> str:
    result = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout


def chrome_profile_process_ids(
    profile_dir: Path,
    *,
    debug_port: int | None = None,
    expected_pids: set[int] | None = None,
) -> list[int]:
    arguments = [f"--user-data-dir={profile_dir.expanduser()}"]
    if debug_port is not None:
        arguments.append(f"--remote-debugging-port={int(debug_port)}")
    return _matching_chrome_process_ids(
        _chrome_process_output(),
        required_arguments=tuple(arguments),
        expected_pids=expected_pids,
    )


def _terminate_matching_processes(
    required_arguments: tuple[str, ...],
    *,
    expected_pids: set[int] | None = None,
    wait_attempts: int = 20,
    delay_seconds: float = 0.25,
) -> dict[str, object]:
    initial_pids = _matching_chrome_process_ids(
        _chrome_process_output(),
        required_arguments=required_arguments,
        expected_pids=expected_pids,
    )
    for pid in initial_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    remaining_pids: list[int] = []
    for _ in range(wait_attempts):
        remaining_pids = _matching_chrome_process_ids(
            _chrome_process_output(),
            required_arguments=required_arguments,
            expected_pids=expected_pids,
        )
        if not remaining_pids:
            return {
                "matchedProcessIds": initial_pids,
                "forcedProcessIds": [],
                "remainingProcessIds": [],
            }
        time.sleep(delay_seconds)
    forced_pids = list(remaining_pids)
    for pid in forced_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for _ in range(max(1, min(wait_attempts, 4))):
        remaining_pids = _matching_chrome_process_ids(
            _chrome_process_output(),
            required_arguments=required_arguments,
            expected_pids=expected_pids,
        )
        if not remaining_pids:
            break
        time.sleep(delay_seconds)
    return {
        "matchedProcessIds": initial_pids,
        "forcedProcessIds": forced_pids,
        "remainingProcessIds": remaining_pids,
    }


def terminate_profile_processes(
    profile_dir: Path,
    *,
    debug_port: int | None = None,
    expected_pids: set[int] | None = None,
    wait_attempts: int = 20,
    delay_seconds: float = 0.25,
) -> dict[str, object]:
    arguments = [f"--user-data-dir={profile_dir.expanduser()}"]
    if debug_port is not None:
        arguments.append(f"--remote-debugging-port={int(debug_port)}")
    return _terminate_matching_processes(
        tuple(arguments),
        expected_pids=expected_pids,
        wait_attempts=wait_attempts,
        delay_seconds=delay_seconds,
    )


def terminate_debug_port_processes(
    debug_port: int,
    *,
    wait_attempts: int = 20,
    delay_seconds: float = 0.25,
) -> dict[str, object]:
    return _terminate_matching_processes(
        (f"--remote-debugging-port={int(debug_port)}",),
        wait_attempts=wait_attempts,
        delay_seconds=delay_seconds,
    )


def debug_port_process_lines(debug_port: int) -> list[str]:
    required_argument = f"--remote-debugging-port={int(debug_port)}"
    lines: list[str] = []
    for line in _chrome_process_output().splitlines():
        stripped = line.strip()
        parts = stripped.split(maxsplit=1)
        command = parts[1] if len(parts) > 1 else ""
        if _is_main_chrome_command(command) and _command_has_exact_argument(command, required_argument):
            lines.append(stripped)
    return lines


def debug_port_has_profile_conflict(debug_port: int, profile_dir: Path) -> bool:
    profile_argument = f"--user-data-dir={profile_dir.expanduser()}"
    lines = debug_port_process_lines(debug_port)
    for line in lines:
        parts = line.split(maxsplit=1)
        command = parts[1] if len(parts) > 1 else ""
        if not _command_has_exact_argument(command, profile_argument):
            return True
    return False


def load_display_frames() -> dict[str, DisplayFrame]:
    script = r'''
import AppKit
import CoreGraphics
import Foundation
let screens = NSScreen.screens.map { screen in
    let screenFrame = screen.frame
    let visibleFrame = screen.visibleFrame
    let displayID = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0
    let cgFrame = CGDisplayBounds(displayID)
    return [
        "name": screen.localizedName,
        "screenX": screenFrame.origin.x,
        "screenY": screenFrame.origin.y,
        "screenWidth": screenFrame.size.width,
        "screenHeight": screenFrame.size.height,
        "visibleX": visibleFrame.origin.x,
        "visibleY": visibleFrame.origin.y,
        "visibleWidth": visibleFrame.size.width,
        "visibleHeight": visibleFrame.size.height,
        "cgX": cgFrame.origin.x,
        "cgY": cgFrame.origin.y,
    ]
}
let data = try! JSONSerialization.data(withJSONObject: screens, options: [])
print(String(data: data, encoding: .utf8)!)
'''
    completed = _run(["swift", "-e", script])
    payload = json.loads(completed.stdout)
    return {item["name"]: _display_frame_from_payload(item) for item in payload}


def _display_frame_from_payload(item: dict[str, object]) -> DisplayFrame:
    legacy_keys = {"name", "x", "y", "width", "height"}
    screen_keys = {
        "name",
        "screenX",
        "screenY",
        "screenHeight",
        "visibleX",
        "visibleY",
        "visibleWidth",
        "visibleHeight",
        "cgX",
        "cgY",
    }
    if screen_keys.issubset(item):
        screen_y = float(item["screenY"])
        screen_height = float(item["screenHeight"])
        visible_x = float(item["visibleX"])
        visible_y = float(item["visibleY"])
        visible_width = float(item["visibleWidth"])
        visible_height = float(item["visibleHeight"])
        cg_x = float(item["cgX"])
        cg_y = float(item["cgY"])
        x = cg_x + (visible_x - float(item.get("screenX", visible_x)))
        y = cg_y + ((screen_y + screen_height) - (visible_y + visible_height))
        return DisplayFrame(
            name=str(item["name"]),
            x=int(round(x)),
            y=int(round(y)),
            width=int(round(visible_width)),
            height=int(round(visible_height)),
        )
    if legacy_keys.issubset(item):
        return DisplayFrame(
            name=str(item["name"]),
            x=int(item["x"]),
            y=int(item["y"]),
            width=int(item["width"]),
            height=int(item["height"]),
        )
    raise ValueError(f"Display payload is missing required geometry keys: {sorted(item)}")


def marker_data_url(marker_title: str = DEFAULT_MARKER_TITLE, marker_slug: str = DEFAULT_MARKER_URL_SUBSTRING) -> str:
    html = f"<html><head><title>{marker_title}</title></head><body data-marker=\"{marker_slug}\">{marker_title}</body></html>"
    return "data:text/html," + urllib.parse.quote(html, safe="")


def _chrome_application_name(chrome_binary: str) -> str:
    binary_path = Path(chrome_binary)
    for parent in [binary_path, *binary_path.parents]:
        if parent.suffix == ".app":
            return parent.stem
    return binary_path.stem or chrome_binary


def chrome_application_available(chrome_binary: str = DEFAULT_CHROME_BINARY) -> bool:
    binary_path = Path(chrome_binary).expanduser()
    if binary_path.exists():
        return True
    app_name = _chrome_application_name(chrome_binary)
    candidate_apps = [
        Path("/Applications") / f"{app_name}.app",
        Path("~/Applications").expanduser() / f"{app_name}.app",
    ]
    if any(candidate.exists() for candidate in candidate_apps):
        return True
    if shutil.which(app_name):
        return True
    mdfind = shutil.which("mdfind")
    if not mdfind:
        return False
    completed = subprocess.run(
        [mdfind, "kMDItemCFBundleIdentifier == 'com.google.Chrome'"],
        check=False,
        capture_output=True,
        text=True,
    )
    return any(Path(line).name == f"{app_name}.app" for line in completed.stdout.splitlines())


def chrome_missing_message(chrome_binary: str = DEFAULT_CHROME_BINARY) -> str:
    return (
        "Google Chrome is not installed or could not be found. "
        "Install Google Chrome from https://www.google.com/chrome/ and then click Launch / Login again. "
        "WhatsApp Collector opens its own dedicated Chrome profile and enables the needed DevTools connection automatically; "
        "you do not need to turn on Chrome developer settings yourself."
    )


def _normalized_display_name(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def choose_display(display_frames: dict[str, DisplayFrame], requested_name: str | None = None) -> tuple[DisplayFrame, bool]:
    if not display_frames:
        raise ValueError("No macOS displays were detected")
    if not requested_name:
        return next(iter(display_frames.values())), False

    if requested_name in display_frames:
        return display_frames[requested_name], False

    normalized_requested_name = _normalized_display_name(requested_name)
    for display in display_frames.values():
        if _normalized_display_name(display.name) == normalized_requested_name:
            return display, False

    if DEFAULT_FALLBACK_DISPLAY_NAME in display_frames:
        return display_frames[DEFAULT_FALLBACK_DISPLAY_NAME], True
    first_name = sorted(display_frames)[0]
    return display_frames[first_name], True


def edge_hidden_bounds(display: DisplayFrame) -> dict[str, int]:
    width = min(EDGE_PLACEMENT_WIDTH, display.width)
    height = min(EDGE_PLACEMENT_HEIGHT, display.height)
    left = display.x + display.width - EDGE_PLACEMENT_VISIBLE_SLIVER
    top = display.y + max(display.height - height - EDGE_PLACEMENT_BOTTOM_MARGIN, 0)
    return {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


def visible_bounds(display: DisplayFrame) -> dict[str, int]:
    width = min(VISIBLE_PLACEMENT_WIDTH, max(display.width - (VISIBLE_PLACEMENT_HORIZONTAL_MARGIN * 2), 320))
    height = min(VISIBLE_PLACEMENT_HEIGHT, max(display.height - (VISIBLE_PLACEMENT_TOP_MARGIN * 2), 240))
    left = display.x + min(VISIBLE_PLACEMENT_HORIZONTAL_MARGIN, max(display.width - width, 0))
    top = display.y + min(VISIBLE_PLACEMENT_TOP_MARGIN, max(display.height - height, 0))
    return {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


def placement_bounds(display: DisplayFrame, placement_mode: str) -> dict[str, int]:
    if placement_mode == "edge-hidden":
        return edge_hidden_bounds(display)
    if placement_mode == "visible":
        return visible_bounds(display)
    raise ValueError(f"Unsupported placement mode: {placement_mode}")


def ensure_profile_allows_apple_events(profile_dir: Path) -> bool:
    preferences_path = profile_dir / "Default" / "Preferences"
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    if preferences_path.exists():
        payload = json.loads(preferences_path.read_text())
    else:
        payload = {}
    browser_payload = payload.setdefault("browser", {})
    if browser_payload.get("allow_javascript_apple_events") is True:
        return False
    browser_payload["allow_javascript_apple_events"] = True
    temp_path = preferences_path.with_suffix(preferences_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    temp_path.replace(preferences_path)
    return True


def close_marker_window(window_id: int) -> None:
    applescript = f'''
    tell application "Google Chrome"
      if (exists window id {window_id}) then
        close window id {window_id}
      end if
    end tell
    '''
    _run_applescript(applescript)


def find_marker_window_id(*, marker_title: str = DEFAULT_MARKER_TITLE, marker_url_substring: str = DEFAULT_MARKER_URL_SUBSTRING) -> int | None:
    applescript = f'''
    tell application "Google Chrome"
      repeat with w in windows
        repeat with t in tabs of w
          if ((title of t as text) contains {json.dumps(marker_title)}) or ((URL of t as text) contains {json.dumps(marker_url_substring)}) then
            return id of w
          end if
        end repeat
      end repeat
      return ""
    end tell
    '''
    completed = _run_applescript(applescript)
    raw = completed.stdout.strip()
    return int(raw) if raw else None


def ensure_window_on_display(
    *,
    window_id: int,
    display: DisplayFrame,
    target_url: str = DEFAULT_TARGET_URL,
) -> None:
    bounds = display.bounds_list()
    applescript = f'''
    tell application "Google Chrome"
      set targetWindow to window id {window_id}
      set bounds of targetWindow to {{{bounds[0]}, {bounds[1]}, {bounds[2]}, {bounds[3]}}}
      repeat with i from 1 to count of tabs of targetWindow
        if (URL of tab i of targetWindow as text) contains {json.dumps(target_url)} then
          set active tab index of targetWindow to i
          exit repeat
        end if
      end repeat
      return id of targetWindow
    end tell
    '''
    _run_applescript(applescript)


def launch_dedicated_chrome_window(
    *,
    chrome_binary: str = DEFAULT_CHROME_BINARY,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    marker_title: str = DEFAULT_MARKER_TITLE,
    target_url: str = DEFAULT_TARGET_URL,
    debug_port: int = DEFAULT_DEBUG_PORT,
) -> None:
    if not chrome_application_available(chrome_binary):
        raise ChromeNotInstalledError(chrome_missing_message(chrome_binary))
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "open",
        "-g",
        "-n",
        "-a",
        _chrome_application_name(chrome_binary),
        "--args",
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={int(debug_port)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        target_url,
    ]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _activate_whatsapp_target(bridge: ChromeDevToolsBridge) -> None:
    activator = getattr(bridge, "activate_target_url", None)
    if callable(activator):
        activator()


def _marker_targets(bridge: ChromeDevToolsBridge) -> list[dict[str, object]]:
    finder = getattr(bridge, "marker_targets", None)
    if not callable(finder):
        return []
    return list(finder())


def _wait_whatsapp_readiness(
    bridge: ChromeDevToolsBridge,
    *,
    attempts: int,
    delay_seconds: float,
) -> dict[str, object]:
    waiter = getattr(bridge, "wait_until_whatsapp_ready", None)
    if not callable(waiter):
        return {"ready": True, "probe": "unavailable"}
    return dict(waiter(attempts=attempts, delay_seconds=delay_seconds, require_ready=False))


def ensure_dedicated_whatsapp_window(
    *,
    display_name: str | None = DEFAULT_DISPLAY_NAME,
    placement_mode: str = DEFAULT_PLACEMENT_MODE,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    chrome_binary: str = DEFAULT_CHROME_BINARY,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    marker_title: str = DEFAULT_MARKER_TITLE,
    marker_url_substring: str = DEFAULT_MARKER_URL_SUBSTRING,
    target_url: str = DEFAULT_TARGET_URL,
    debug_port: int = DEFAULT_DEBUG_PORT,
    wait_attempts: int = 40,
    delay_seconds: float = 0.5,
) -> dict[str, object]:
    display_frames = load_display_frames()
    if not display_frames:
        raise ValueError("No macOS displays were detected")
    display, display_fallback_used = choose_display(display_frames, display_name)

    bridge = ChromeDevToolsBridge(
        port=int(debug_port),
        marker_title=marker_title,
        marker_url_substring=marker_url_substring,
        target_url_substring=target_url,
    )

    launched = False

    def relaunch_window() -> None:
        nonlocal launched
        terminate_profile_processes(profile_dir)
        terminate_debug_port_processes(debug_port)
        if delay_seconds:
            time.sleep(delay_seconds)
        launch_dedicated_chrome_window(
            chrome_binary=chrome_binary,
            profile_dir=profile_dir,
            marker_title=marker_title,
            target_url=target_url,
            debug_port=debug_port,
        )
        launched = True
        bridge.wait_until_ready(attempts=wait_attempts, delay_seconds=delay_seconds)
        bridge.wait_until_page_targets_exist(attempts=wait_attempts, delay_seconds=delay_seconds)
        bridge.wait_until_target_url_exists(attempts=wait_attempts, delay_seconds=delay_seconds)
        _activate_whatsapp_target(bridge)

    if debug_port_has_profile_conflict(debug_port, profile_dir):
        relaunch_window()
    else:
        try:
            bridge.wait_until_ready(attempts=1, delay_seconds=delay_seconds)
            bridge.wait_until_page_targets_exist(attempts=1, delay_seconds=delay_seconds)
            bridge.wait_until_target_url_exists(attempts=1, delay_seconds=delay_seconds)
            if _marker_targets(bridge):
                relaunch_window()
            else:
                _activate_whatsapp_target(bridge)
        except RuntimeError:
            relaunch_window()

    bounds = placement_bounds(display, placement_mode)
    placement = bridge.place_window(
        left=bounds["left"],
        top=bounds["top"],
        width=bounds["width"],
        height=bounds["height"],
    )
    readiness = _wait_whatsapp_readiness(bridge, attempts=wait_attempts, delay_seconds=delay_seconds)
    if settle_seconds > 0:
        time.sleep(settle_seconds)
    chrome_process_ids = chrome_profile_process_ids(profile_dir, debug_port=debug_port)
    return {
        "windowId": placement["windowId"],
        "targetId": placement.get("targetId"),
        "whatsappReady": readiness.get("ready") is True,
        "whatsappReadiness": readiness,
        "requestedDisplay": display_name,
        "displayFallbackUsed": display_fallback_used,
        "display": asdict(display),
        "placementMode": placement_mode,
        "settleSeconds": settle_seconds,
        "placementBounds": bounds,
        "profileDir": str(profile_dir),
        "markerTitle": marker_title,
        "markerUrlSubstring": marker_url_substring,
        "targetUrl": target_url,
        "debugPort": int(debug_port),
        "launched": launched,
        "chromeProcessIds": chrome_process_ids,
        "chromeProcessId": chrome_process_ids[0] if chrome_process_ids else None,
    }
