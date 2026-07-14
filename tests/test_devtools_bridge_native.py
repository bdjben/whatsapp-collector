from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest

from whatsapp_collector import devtools_bridge
from whatsapp_collector.devtools_bridge import ChromeDevToolsBridge


class _FakeDevToolsState:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.port = 0


class _FakeDevToolsHandler(BaseHTTPRequestHandler):
    state: _FakeDevToolsState

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/json/version":
            self._send_json({"Browser": "FakeChrome/1", "Protocol-Version": "1.3"})
            return
        if self.path == "/json/list":
            self._send_json(
                [
                    {
                        "id": "marker-1",
                        "type": "page",
                        "title": "WhatsApp Collector",
                        "url": "data:text/html,WhatsApp Collector",
                        "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.state.port}/devtools/page/marker-1",
                    },
                    {
                        "id": "page-1",
                        "type": "page",
                        "title": "WhatsApp",
                        "url": "https://web.whatsapp.com/",
                        "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.state.port}/devtools/page/page-1",
                    },
                ]
            )
            return
        if self.path.startswith("/devtools/page/") and self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_websocket()
            return
        self.send_error(404)

    def _send_json(self, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_websocket(self) -> None:
        key = self.headers["Sec-WebSocket-Key"]
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        while True:
            message = _read_ws_text(self.connection)
            if message is None:
                return
            request = json.loads(message)
            self.state.requests.append(request)
            response = self._response_for(request)
            _write_ws_text(self.connection, json.dumps(response))

    def _response_for(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        request_id = request["id"]
        if method == "Browser.getWindowForTarget":
            return {"id": request_id, "result": {"windowId": 17}}
        if method == "Browser.setWindowBounds":
            return {"id": request_id, "result": {}}
        if method == "Target.activateTarget":
            return {"id": request_id, "result": {}}
        if method == "Runtime.evaluate":
            expression = request.get("params", {}).get("expression")
            if isinstance(expression, str) and "hasChatPane" in expression:
                return {
                    "id": request_id,
                    "result": {
                        "result": {
                            "type": "string",
                            "value": json.dumps(
                                {
                                    "url": "https://web.whatsapp.com/",
                                    "title": "WhatsApp",
                                    "hasChatPane": True,
                                    "chatRowCount": 12,
                                    "loginRequired": False,
                                    "loading": False,
                                    "ready": True,
                                    "bodyPreview": "WhatsApp chats",
                                }
                            ),
                        }
                    },
                }
            if expression == "document.title":
                return {"id": request_id, "result": {"result": {"type": "string", "value": "WhatsApp"}}}
            if expression == "({x: 12, y: 34})":
                return {"id": request_id, "result": {"result": {"type": "object", "value": {"x": 12, "y": 34}}}}
            if isinstance(expression, str) and "download-completed-without-cache-entry" in expression:
                return {
                    "id": request_id,
                    "result": {
                        "result": {
                            "type": "string",
                            "value": json.dumps({"ok": False, "error": "message-not-visible"}),
                        }
                    },
                }
            return {"id": request_id, "result": {"result": {"type": "undefined"}}}
        if method == "Input.dispatchMouseEvent":
            return {"id": request_id, "result": {}}
        return {"id": request_id, "error": {"message": f"Unsupported fake method: {method}"}}


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("socket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _read_ws_text(sock: socket.socket) -> str | None:
    try:
        first, second = _read_exact(sock, 2)
    except ConnectionError:
        return None
    opcode = first & 0x0F
    if opcode == 0x8:
        return None
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(sock, 8))[0]
    mask = _read_exact(sock, 4) if second & 0x80 else b""
    payload = bytearray(_read_exact(sock, length))
    if mask:
        for index in range(length):
            payload[index] ^= mask[index % 4]
    return payload.decode("utf-8")


def _write_ws_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header.append(126)
        header.extend(struct.pack("!H", len(payload)))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", len(payload)))
    sock.sendall(bytes(header) + payload)


@pytest.fixture
def fake_devtools_server() -> tuple[int, _FakeDevToolsState]:
    state = _FakeDevToolsState()

    class Handler(_FakeDevToolsHandler):
        pass

    Handler.state = state
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state.port, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_devtools_bridge_timeout_allows_slow_whatsapp_indexeddb_evaluations() -> None:
    assert devtools_bridge._DEFAULT_TIMEOUT_SECONDS >= 120


def test_devtools_bridge_version_and_list_use_native_http_without_node(fake_devtools_server, monkeypatch) -> None:
    port, _state = fake_devtools_server
    monkeypatch.setenv("PATH", os.devnull)

    bridge = ChromeDevToolsBridge(port=port)

    assert bridge.version()["Browser"] == "FakeChrome/1"
    assert [target["id"] for target in bridge.list_targets()] == ["marker-1", "page-1"]


def test_devtools_bridge_waits_for_target_url(fake_devtools_server, monkeypatch) -> None:
    port, _state = fake_devtools_server
    monkeypatch.setenv("PATH", os.devnull)

    bridge = ChromeDevToolsBridge(port=port, target_url_substring="https://web.whatsapp.com/")

    assert [target["id"] for target in bridge.wait_until_target_url_exists(attempts=1, delay_seconds=0)] == ["page-1"]


def test_devtools_bridge_target_url_wait_fails_when_only_other_pages_exist(fake_devtools_server, monkeypatch) -> None:
    port, _state = fake_devtools_server
    monkeypatch.setenv("PATH", os.devnull)

    bridge = ChromeDevToolsBridge(port=port, target_url_substring="https://example.invalid/")

    with pytest.raises(RuntimeError, match="Timed out waiting for Chrome target URL"):
        bridge.wait_until_target_url_exists(attempts=1, delay_seconds=0)


def test_devtools_bridge_evaluates_and_clicks_via_native_websocket_without_node(fake_devtools_server, monkeypatch) -> None:
    port, state = fake_devtools_server
    monkeypatch.setenv("PATH", os.devnull)
    bridge = ChromeDevToolsBridge(
        port=port,
        marker_title="WhatsApp Collector",
        target_url_substring="https://web.whatsapp.com/",
    )

    assert bridge.evaluate("document.title") == "WhatsApp"
    assert bridge.click_point("({x: 12, y: 34})") == {"x": 12, "y": 34}

    methods = [request["method"] for request in state.requests]
    assert "Runtime.evaluate" in methods
    assert methods.count("Input.dispatchMouseEvent") == 3
    assert "Page.bringToFront" not in methods


def test_devtools_bridge_activates_and_waits_for_whatsapp_readiness(fake_devtools_server, monkeypatch) -> None:
    port, state = fake_devtools_server
    monkeypatch.setenv("PATH", os.devnull)
    bridge = ChromeDevToolsBridge(port=port, target_url_substring="https://web.whatsapp.com/")

    activated = bridge.activate_target_url()
    readiness = bridge.wait_until_whatsapp_ready(attempts=1, delay_seconds=0, require_ready=True)

    assert activated["targetId"] == "page-1"
    assert readiness["ready"] is True
    activate_requests = [request for request in state.requests if request["method"] == "Target.activateTarget"]
    assert activate_requests
    assert activate_requests[0]["params"] == {"targetId": "page-1"}


def test_devtools_bridge_places_window_via_native_websocket_without_node(fake_devtools_server, monkeypatch) -> None:
    port, state = fake_devtools_server
    monkeypatch.setenv("PATH", os.devnull)
    bridge = ChromeDevToolsBridge(port=port, target_url_substring="https://web.whatsapp.com/")

    result = bridge.place_window(left=1, top=2, width=800, height=600)

    assert result["windowId"] == 17
    assert result["targetId"] == "page-1"
    set_bounds = [request for request in state.requests if request["method"] == "Browser.setWindowBounds"]
    assert set_bounds[0]["params"] == {
        "windowId": 17,
        "bounds": {"left": 1, "top": 2, "width": 800, "height": 600, "windowState": "normal"},
    }
    assert any(request["method"] == "Target.activateTarget" for request in state.requests)


def test_visible_media_download_uses_stable_whatsapp_message_id(fake_devtools_server, monkeypatch) -> None:
    port, state = fake_devtools_server
    monkeypatch.setenv("PATH", os.devnull)
    bridge = ChromeDevToolsBridge(port=port, target_url_substring="https://web.whatsapp.com/")

    result = bridge.request_visible_media_download("false_chat_message", file_hash="hash", force=False)

    assert result == {"ok": False, "error": "message-not-visible"}
    expressions = [
        request["params"]["expression"]
        for request in state.requests
        if request["method"] == "Runtime.evaluate"
    ]
    assert any("value._serialized || value.$1 || String(value)" in expression for expression in expressions)
