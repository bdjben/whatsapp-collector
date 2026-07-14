from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
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
    "WhatsApp Collector only reads WhatsApp Web and downloads received media; it will not edit message fields, "
    "upload attachments, or send messages."
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
        profile_dir: Path | str | None = None,
    ) -> None:
        self._runner = runner or self._default_runner
        self._target = target or ChromeTarget(
            marker_title=os.environ.get("WA_CHROME_MARKER_TITLE") or None,
            marker_url_substring=os.environ.get("WA_CHROME_MARKER_URL_SUBSTRING") or None,
            target_url_substring=os.environ.get("WA_CHROME_TARGET_URL_SUBSTRING", DEFAULT_TARGET_URL_SUBSTRING),
        )
        debug_port = debug_port or (int(os.environ.get(DEFAULT_DEBUG_PORT_ENV)) if os.environ.get(DEFAULT_DEBUG_PORT_ENV) else None)
        self._devtools: ChromeDevToolsBridge | None = None
        self._profile_dir = Path(profile_dir).expanduser() if profile_dir else None
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

    def read_cached_media(self, file_hash: str, *, max_bytes: int) -> dict[str, Any]:
        if self._devtools is None:
            return {"available": False, "error": "devtools-unavailable"}
        return self._devtools.read_cached_media(file_hash, max_bytes=max_bytes)

    def request_visible_media_download(self, message_id: str, *, file_hash: str | None, force: bool) -> dict[str, Any]:
        if self._devtools is None:
            return {"ok": False, "error": "devtools-unavailable"}
        return self._devtools.request_visible_media_download(message_id, file_hash=file_hash, force=force)

    def download_visible_attachment(
        self,
        message_id: str,
        *,
        file_hash: str | None,
        expected_size: int | None,
        expected_file_name: str | None,
        kind: str,
        timeout_seconds: float = 45.0,
    ) -> dict[str, Any]:
        if self._devtools is None:
            return {"ok": False, "error": "devtools-unavailable"}
        directories = self._download_directories()
        baseline = self._download_snapshot(directories)
        errors: list[str] = []
        methods = [(False, "context-menu")]
        if kind == "document":
            methods.append((True, "document-viewer"))
        for viewer, method in methods:
            try:
                self._devtools.trigger_visible_attachment_download(message_id, viewer=viewer)
            except Exception as exc:
                errors.append(f"{method}:{exc}")
                try:
                    self._devtools.dismiss_transient_ui()
                except Exception:
                    pass
                continue
            downloaded = self._wait_for_download(
                directories,
                baseline=baseline,
                file_hash=file_hash,
                expected_size=expected_size,
                expected_file_name=expected_file_name,
                timeout_seconds=timeout_seconds,
            )
            if downloaded:
                return {"ok": True, "path": str(downloaded), "method": method}
            errors.append(f"{method}:no matching completed download appeared")
            baseline = self._download_snapshot(directories)
            try:
                self._devtools.dismiss_transient_ui()
            except Exception:
                pass
        return {"ok": False, "error": "; ".join(errors) or "download-action-unavailable"}

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

    def _download_directories(self) -> list[Path]:
        candidates = [Path.home() / "Downloads"]
        if self._profile_dir:
            for preferences_path in (
                self._profile_dir / "Default" / "Preferences",
                self._profile_dir / "Preferences",
            ):
                try:
                    preferences = json.loads(preferences_path.read_text())
                except (OSError, ValueError, TypeError):
                    continue
                configured = preferences.get("download", {}).get("default_directory")
                if isinstance(configured, str) and configured.strip():
                    candidates.append(Path(configured).expanduser())
        directories: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.exists() and resolved.is_dir():
                directories.append(resolved)
        return directories

    @staticmethod
    def _download_snapshot(directories: list[Path]) -> dict[Path, tuple[int, int]]:
        snapshot: dict[Path, tuple[int, int]] = {}
        for directory in directories:
            try:
                paths = list(directory.iterdir())
            except OSError:
                continue
            for path in paths:
                if not path.is_file():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                snapshot[path] = (stat.st_size, stat.st_mtime_ns)
        return snapshot

    @classmethod
    def _wait_for_download(
        cls,
        directories: list[Path],
        *,
        baseline: dict[Path, tuple[int, int]],
        file_hash: str | None,
        expected_size: int | None,
        expected_file_name: str | None,
        timeout_seconds: float,
    ) -> Path | None:
        deadline = time.monotonic() + timeout_seconds
        stable: dict[Path, tuple[int, int]] = {}
        expected_sha = cls._filehash_hex(file_hash)
        while time.monotonic() < deadline:
            current = cls._download_snapshot(directories)
            for path, signature in sorted(current.items(), key=lambda item: item[1][1], reverse=True):
                if path.suffix.lower() in {".crdownload", ".tmp", ".download"}:
                    continue
                if baseline.get(path) == signature:
                    continue
                if expected_size is not None and expected_size > 0 and signature[0] != expected_size:
                    continue
                previous = stable.get(path)
                stable[path] = signature
                if previous != signature:
                    continue
                if expected_sha:
                    try:
                        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
                            continue
                    except OSError:
                        continue
                elif expected_file_name and not cls._download_name_matches(path.name, expected_file_name):
                    continue
                return path
            time.sleep(0.25)
        return None

    @staticmethod
    def _filehash_hex(file_hash: str | None) -> str | None:
        if not file_hash:
            return None
        try:
            digest = base64.b64decode(file_hash, validate=True)
        except (TypeError, ValueError):
            return None
        return digest.hex() if len(digest) == hashlib.sha256().digest_size else None

    @staticmethod
    def _download_name_matches(actual: str, expected: str) -> bool:
        actual_path = Path(actual)
        expected_path = Path(expected)
        if actual_path.suffix.casefold() != expected_path.suffix.casefold():
            return False
        actual_stem = re.sub(r" \(\d+\)$", "", actual_path.stem)
        return actual_stem.casefold() == expected_path.stem.casefold()

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
