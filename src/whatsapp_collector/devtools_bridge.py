from __future__ import annotations

import json
import subprocess
import time
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

NODE_HELPER_RESOURCE = files("whatsapp_collector").joinpath("assets/chrome_devtools.mjs")
NODE_HELPER = Path(str(NODE_HELPER_RESOURCE))


class ChromeDevToolsBridge:
    def __init__(self, *, port: int, marker_title: str | None = None, marker_url_substring: str | None = None, target_url_substring: str | None = None) -> None:
        self.port = int(port)
        self.marker_title = marker_title
        self.marker_url_substring = marker_url_substring
        self.target_url_substring = target_url_substring

    def _run(self, payload: dict[str, Any]) -> Any:
        merged = {
            "port": self.port,
            "markerTitle": self.marker_title,
            "markerUrlSubstring": self.marker_url_substring,
            "targetUrlSubstring": self.target_url_substring,
            **payload,
        }
        with as_file(NODE_HELPER_RESOURCE) as helper_path:
            try:
                completed = subprocess.run(
                    ["node", str(helper_path)],
                    input=json.dumps(merged),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                raise RuntimeError(
                    f"Chrome DevTools helper failed (action={payload.get('action')}, port={self.port}): {detail}"
                ) from exc
        stdout = completed.stdout.strip()
        return json.loads(stdout) if stdout else None

    def wait_until_ready(self, *, attempts: int = 40, delay_seconds: float = 0.5) -> dict[str, Any]:
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                payload = self.version()
                if isinstance(payload, dict):
                    return payload
            except Exception as exc:  # pragma: no cover - exercised via retry behavior in live mode
                last_error = exc
            time.sleep(delay_seconds)
        if last_error is not None:
            raise RuntimeError(f"Timed out waiting for Chrome DevTools port {self.port}: {last_error}") from last_error
        raise RuntimeError(f"Timed out waiting for Chrome DevTools port {self.port}")

    def wait_until_page_targets_exist(self, *, attempts: int = 40, delay_seconds: float = 0.5) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                targets = self.list_targets()
                page_targets = [target for target in targets if target.get("type") == "page"]
                if page_targets:
                    return page_targets
            except Exception as exc:  # pragma: no cover - exercised in live retry paths
                last_error = exc
            time.sleep(delay_seconds)
        if last_error is not None:
            raise RuntimeError(f"Timed out waiting for Chrome page targets on port {self.port}: {last_error}") from last_error
        raise RuntimeError(f"Timed out waiting for Chrome page targets on port {self.port}")

    def version(self) -> dict[str, Any]:
        return dict(self._run({"action": "version"}))

    def list_targets(self) -> list[dict[str, Any]]:
        return list(self._run({"action": "list"}))

    def evaluate(self, expression: str) -> str:
        result = self._run({"action": "evaluate", "expression": expression})
        if result is None:
            return ""
        return str(result)

    def place_window(self, *, left: int, top: int, width: int, height: int) -> dict[str, Any]:
        return dict(
            self._run(
                {
                    "action": "place-window",
                    "bounds": {
                        "left": int(left),
                        "top": int(top),
                        "width": int(width),
                        "height": int(height),
                    },
                }
            )
        )

    def click_point(self, expression: str) -> dict[str, Any]:
        return dict(self._run({"action": "click-point", "expression": expression}))
