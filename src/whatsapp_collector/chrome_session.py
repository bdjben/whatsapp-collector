from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable

from whatsapp_collector.devtools_bridge import ChromeDevToolsBridge

Runner = Callable[[str], str]

DEFAULT_TARGET_URL_SUBSTRING = "web.whatsapp.com/"
DEFAULT_MARKER_TITLE = "WhatsApp Collector"
DEFAULT_MARKER_URL_SUBSTRING = "whatsapp-collector"
DEFAULT_DEBUG_PORT_ENV = "WA_CHROME_DEBUG_PORT"
READ_ONLY_BLOCK_MESSAGE = (
    "Blocked non-read-only browser automation script ({reason}). "
    "WhatsApp Collector only reads WhatsApp Web; it will not edit message fields, "
    "open attachment controls, or send messages."
)
READ_ONLY_VIOLATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("composer editing surface", re.compile(r"\bcontenteditable\b", re.IGNORECASE)),
    (
        "send button",
        re.compile(
            r"\bsendbutton\b|aria-label\s*=\s*\\?['\"][^'\"]*\bsend\b|data-icon\s*=\s*\\?['\"][^'\"]*\bsend\b",
            re.IGNORECASE,
        ),
    ),
    ("send method call", re.compile(r"(?<![\w$])\.send\s*\(", re.IGNORECASE)),
    ("new chat control", re.compile(r"\bnew\s+chat\b", re.IGNORECASE)),
    (
        "attachment control",
        re.compile(
            r"\battach(?:ment)?button\b|aria-label\s*=\s*\\?['\"][^'\"]*\battach\b|data-icon\s*=\s*\\?['\"][^'\"]*\battach\b",
            re.IGNORECASE,
        ),
    ),
    ("input value assignment", re.compile(r"\.value\s*=", re.IGNORECASE)),
    ("synthetic input event", re.compile(r"\binputevent\b", re.IGNORECASE)),
    ("execCommand mutation", re.compile(r"\bexeccommand\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class ChromeTarget:
    marker_title: str | None = None
    marker_url_substring: str | None = None
    target_url_substring: str = DEFAULT_TARGET_URL_SUBSTRING


class ChromeWhatsAppSession:
    def __init__(
        self,
        runner: Runner | None = None,
        *,
        target: ChromeTarget | None = None,
        debug_port: int | None = None,
    ) -> None:
        self._runner = runner or self._default_runner
        self._target = target or ChromeTarget(
            marker_title=os.environ.get("WA_CHROME_MARKER_TITLE") or None,
            marker_url_substring=os.environ.get("WA_CHROME_MARKER_URL_SUBSTRING") or None,
            target_url_substring=os.environ.get("WA_CHROME_TARGET_URL_SUBSTRING", DEFAULT_TARGET_URL_SUBSTRING),
        )
        debug_port = debug_port or (int(os.environ.get(DEFAULT_DEBUG_PORT_ENV)) if os.environ.get(DEFAULT_DEBUG_PORT_ENV) else None)
        self._devtools: ChromeDevToolsBridge | None = None
        if debug_port:
            self._devtools = ChromeDevToolsBridge(
                port=int(debug_port),
                marker_title=self._target.marker_title,
                marker_url_substring=self._target.marker_url_substring,
                target_url_substring=self._target.target_url_substring,
            )

    def assert_readonly(self, js: str) -> None:
        for reason, pattern in READ_ONLY_VIOLATION_PATTERNS:
            if pattern.search(js):
                raise ValueError(READ_ONLY_BLOCK_MESSAGE.format(reason=reason))

    def run_js(self, js: str) -> str:
        self.assert_readonly(js)
        if self._devtools is not None:
            return self._devtools.evaluate(js)
        return self._runner(self._build_applescript(js))

    def click_point(self, expression: str) -> dict[str, Any]:
        self.assert_readonly(expression)
        if self._devtools is None:
            raise RuntimeError("Click-point requires DevTools-backed Chrome session")
        return self._devtools.click_point(expression)

    def run_json(self, js: str) -> Any:
        raw = self.run_js(js)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON returned from Chrome session: {raw}") from exc

    def run_async_json(
        self,
        starter_js: str,
        *,
        result_var: str = "__hermes_async_result",
        attempts: int = 40,
        delay_seconds: float = 0.1,
    ) -> Any:
        if self._devtools is not None:
            polling_expression = f'''(async () => {{
                {starter_js};
                for (let i = 0; i < {attempts}; i += 1) {{
                    const value = window[{json.dumps(result_var)}] || "";
                    if (value) {{
                        return value;
                    }}
                    await new Promise(resolve => setTimeout(resolve, {int(delay_seconds * 1000)}));
                }}
                throw new Error("Timed out waiting for Chrome async result variable {result_var}");
            }})()'''
            raw = self.run_js(polling_expression)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON returned from Chrome async session: {raw}") from exc
        self.run_js(starter_js)
        for _ in range(attempts):
            raw = self.run_js(f'{result_var} || ""')
            if raw:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON returned from Chrome async session: {raw}") from exc
            time.sleep(delay_seconds)
        raise TimeoutError(f"Timed out waiting for Chrome async result variable {result_var}")

    def _build_applescript(self, js: str) -> str:
        target_url = self._target.target_url_substring
        marker_title = self._target.marker_title
        marker_url = self._target.marker_url_substring
        if marker_title or marker_url:
            marker_conditions: list[str] = []
            if marker_title:
                marker_conditions.append(f'(title of t as text) contains {json.dumps(marker_title)}')
            if marker_url:
                marker_conditions.append(f'(URL of t as text) contains {json.dumps(marker_url)}')
            marker_clause = " or ".join(marker_conditions)
            return f'''
            tell application "Google Chrome"
              repeat with w in windows
                set markerFound to false
                repeat with t in tabs of w
                  if {marker_clause} then
                    set markerFound to true
                    exit repeat
                  end if
                end repeat
                if markerFound then
                  repeat with targetTab in tabs of w
                    if (URL of targetTab as text) contains {json.dumps(target_url)} then
                      tell targetTab
                        return execute javascript {json.dumps(js)}
                      end tell
                    end if
                  end repeat
                end if
              end repeat
              error "No Chrome tab matched configured marker/target selector"
            end tell
            '''
        return f'''
        tell application "Google Chrome"
          tell active tab of front window
            return execute javascript {json.dumps(js)}
          end tell
        end tell
        '''

    @staticmethod
    def _default_runner(applescript: str) -> str:
        completed = subprocess.run(
            ["osascript"],
            input=applescript,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
