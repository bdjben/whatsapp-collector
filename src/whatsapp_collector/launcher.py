from __future__ import annotations

import json
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


def terminate_profile_processes(profile_dir: Path, *, wait_attempts: int = 20, delay_seconds: float = 0.25) -> None:
    profile_str = str(profile_dir)
    subprocess.run(
        ["pkill", "-f", profile_str],
        check=False,
        capture_output=True,
        text=True,
    )
    for _ in range(wait_attempts):
        remaining = subprocess.run(
            ["pgrep", "-fal", profile_str],
            check=False,
            capture_output=True,
            text=True,
        )
        if not remaining.stdout.strip():
            return
        time.sleep(delay_seconds)


def load_display_frames() -> dict[str, DisplayFrame]:
    script = r'''
import AppKit
import Foundation
let screens = NSScreen.screens.map { screen in
    let frame = screen.visibleFrame
    return [
        "name": screen.localizedName,
        "x": Int(frame.origin.x),
        "y": Int(frame.origin.y),
        "width": Int(frame.size.width),
        "height": Int(frame.size.height),
    ]
}
let data = try! JSONSerialization.data(withJSONObject: screens, options: [])
print(String(data: data, encoding: .utf8)!)
'''
    completed = _run(["swift", "-e", script])
    payload = json.loads(completed.stdout)
    return {item["name"]: DisplayFrame(**item) for item in payload}


def marker_data_url(marker_title: str = DEFAULT_MARKER_TITLE) -> str:
    html = f"<html><head><title>{marker_title}</title></head><body>{marker_title}</body></html>"
    return "data:text/html," + urllib.parse.quote(html, safe="")


def _chrome_application_name(chrome_binary: str) -> str:
    binary_path = Path(chrome_binary)
    for parent in [binary_path, *binary_path.parents]:
        if parent.suffix == ".app":
            return parent.stem
    return binary_path.stem or chrome_binary


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
        marker_data_url(marker_title),
        target_url,
    ]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    try:
        bridge.wait_until_ready(attempts=1, delay_seconds=delay_seconds)
        bridge.wait_until_page_targets_exist(attempts=1, delay_seconds=delay_seconds)
    except RuntimeError:
        terminate_profile_processes(profile_dir)
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

    bounds = placement_bounds(display, placement_mode)
    placement = bridge.place_window(
        left=bounds["left"],
        top=bounds["top"],
        width=bounds["width"],
        height=bounds["height"],
    )
    if settle_seconds > 0:
        time.sleep(settle_seconds)
    return {
        "windowId": placement["windowId"],
        "targetId": placement.get("targetId"),
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
    }
