from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_DEFAULT_TIMEOUT_SECONDS = 120.0


class _LocalWebSocket:
    def __init__(self, url: str, *, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._socket: socket.socket | None = None

    def __enter__(self) -> _LocalWebSocket:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def connect(self) -> None:
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme != "ws":
            raise RuntimeError(f"Only ws:// Chrome DevTools URLs are supported, got {self.url!r}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock = socket.create_connection((host, port), timeout=self.timeout_seconds)
        sock.settimeout(self.timeout_seconds)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(request)
        header = self._read_http_header(sock)
        status_line = header.split("\r\n", 1)[0]
        if " 101 " not in status_line:
            sock.close()
            raise RuntimeError(f"Chrome DevTools WebSocket handshake failed: {status_line}")
        expected_accept = base64.b64encode(hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        headers = self._parse_http_headers(header)
        actual_accept = headers.get("sec-websocket-accept")
        if actual_accept and actual_accept != expected_accept:
            sock.close()
            raise RuntimeError("Chrome DevTools WebSocket handshake returned an invalid Sec-WebSocket-Accept header")
        self._socket = sock

    def send_text(self, message: str) -> None:
        sock = self._require_socket()
        payload = message.encode("utf-8")
        header = bytearray([0x81])
        mask_bit = 0x80
        if len(payload) < 126:
            header.append(mask_bit | len(payload))
        elif len(payload) < 65536:
            header.append(mask_bit | 126)
            header.extend(struct.pack("!H", len(payload)))
        else:
            header.append(mask_bit | 127)
            header.extend(struct.pack("!Q", len(payload)))
        mask = os.urandom(4)
        masked = bytearray(payload)
        for index in range(len(masked)):
            masked[index] ^= mask[index % 4]
        sock.sendall(bytes(header) + mask + bytes(masked))

    def recv_text(self) -> str:
        fragments: list[bytes] = []
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x8:
                raise RuntimeError("Chrome DevTools WebSocket closed before a response was received")
            if opcode == 0x9:
                self._send_control_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x0):
                fragments.append(payload)
                # The minimal CDP responses Chrome sends are single-frame text payloads.
                # If a fragmented response ever arrives, the continuation loop above still
                # accumulates frames until the final one sets FIN.
                return b"".join(fragments).decode("utf-8")
            raise RuntimeError(f"Unsupported Chrome DevTools WebSocket opcode {opcode}")

    def close(self) -> None:
        sock = self._socket
        if sock is None:
            return
        try:
            self._send_control_frame(0x8, b"")
        except (OSError, RuntimeError):
            pass
        finally:
            self._socket = None
            sock.close()

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("Chrome DevTools WebSocket is not connected")
        return self._socket

    @staticmethod
    def _read_http_header(sock: socket.socket) -> str:
        chunks = bytearray()
        while b"\r\n\r\n" not in chunks:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > 65536:
                raise RuntimeError("Chrome DevTools WebSocket handshake header was too large")
        return bytes(chunks).decode("iso-8859-1", errors="replace")

    @staticmethod
    def _parse_http_headers(header: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in header.split("\r\n")[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            result[name.strip().lower()] = value.strip()
        return result

    def _recv_frame(self) -> tuple[int, bytes]:
        sock = self._require_socket()
        first, second = self._read_exact(sock, 2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(sock, 8))[0]
        mask = self._read_exact(sock, 4) if second & 0x80 else b""
        payload = bytearray(self._read_exact(sock, length))
        if mask:
            for index in range(length):
                payload[index] ^= mask[index % 4]
        if not fin:
            # Chrome DevTools normally sends complete JSON messages in one frame. If it
            # fragments, read continuations here and return the assembled payload.
            fragments = [bytes(payload)]
            while True:
                continuation_opcode, continuation_payload = self._recv_frame()
                fragments.append(continuation_payload)
                if continuation_opcode == 0x0:
                    return opcode, b"".join(fragments)
        return opcode, bytes(payload)

    def _send_control_frame(self, opcode: int, payload: bytes) -> None:
        sock = self._require_socket()
        header = bytearray([0x80 | opcode])
        mask_bit = 0x80
        header.append(mask_bit | len(payload))
        mask = os.urandom(4)
        masked = bytearray(payload)
        for index in range(len(masked)):
            masked[index] ^= mask[index % 4]
        sock.sendall(bytes(header) + mask + bytes(masked))

    @staticmethod
    def _read_exact(sock: socket.socket, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = sock.recv(length - len(chunks))
            if not chunk:
                raise RuntimeError("Chrome DevTools WebSocket closed unexpectedly")
            chunks.extend(chunk)
        return bytes(chunks)


class _CDPClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self._next_id = 1

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with _LocalWebSocket(self.websocket_url) as websocket:
            request_id = self._next_id
            self._next_id += 1
            websocket.send_text(json.dumps({"id": request_id, "method": method, "params": params or {}}))
            while True:
                payload = json.loads(websocket.recv_text())
                if payload.get("id") != request_id:
                    continue
                if "error" in payload:
                    error = payload["error"]
                    message = error.get("message") if isinstance(error, dict) else None
                    raise RuntimeError(message or json.dumps(error))
                result = payload.get("result")
                return result if isinstance(result, dict) else {}


class ChromeDevToolsBridge:
    def __init__(self, *, port: int, marker_title: str | None = None, marker_url_substring: str | None = None, target_url_substring: str | None = None) -> None:
        self.port = int(port)
        self.marker_title = marker_title
        self.marker_url_substring = marker_url_substring
        self.target_url_substring = target_url_substring

    def _run_action(self, action: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:
            message = str(exc)
            if message.startswith("Chrome DevTools request failed"):
                raise
            raise RuntimeError(f"Chrome DevTools request failed (action={action}, port={self.port}): {message}") from exc

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

    def wait_until_target_url_exists(self, *, attempts: int = 40, delay_seconds: float = 0.5) -> list[dict[str, Any]]:
        if not self.target_url_substring:
            return []
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                targets = [target for target in self._page_targets() if self._matches_url_target(target)]
                if targets:
                    return targets
            except Exception as exc:  # pragma: no cover - exercised in live retry paths
                last_error = exc
            time.sleep(delay_seconds)
        if last_error is not None:
            raise RuntimeError(f"Timed out waiting for Chrome target URL {self.target_url_substring!r} on port {self.port}: {last_error}") from last_error
        raise RuntimeError(f"Timed out waiting for Chrome target URL {self.target_url_substring!r} on port {self.port}")

    def activate_target_url(self) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            target = self._choose_target(require_target_url=True, prefer_marker_window=True)
            self._send(target, "Target.activateTarget", {"targetId": target["id"]})
            return {
                "targetId": target.get("id"),
                "title": target.get("title"),
                "url": target.get("url"),
            }

        return dict(self._run_action("activate-target", run))

    def inspect_whatsapp_readiness(self) -> dict[str, Any]:
        expression = r'''(() => {
            const bodyText = (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
            const pane = document.querySelector('#pane-side');
            const chatRows = pane ? pane.querySelectorAll('[role="row"], [data-testid^="list-item-"], [data-testid="cell-frame-container"]').length : 0;
            const loginRequired = /log in|scan|qr code|use whatsapp on your computer|link a device/i.test(bodyText)
                && !pane;
            const loading = /loading|connecting|syncing/i.test(bodyText) && !pane;
            return JSON.stringify({
                url: location.href,
                title: document.title,
                hasChatPane: !!pane,
                chatRowCount: chatRows,
                loginRequired,
                loading,
                ready: !!pane,
                bodyPreview: bodyText.slice(0, 220)
            });
        })()'''
        try:
            return dict(json.loads(self.evaluate(expression)))
        except json.JSONDecodeError as exc:
            raise RuntimeError("WhatsApp readiness check returned invalid JSON") from exc

    def wait_until_whatsapp_ready(
        self,
        *,
        attempts: int = 40,
        delay_seconds: float = 0.5,
        require_ready: bool = False,
    ) -> dict[str, Any]:
        last_state: dict[str, Any] | None = None
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                self.activate_target_url()
                state = self.inspect_whatsapp_readiness()
                last_state = state
                if state.get("ready") is True:
                    return state
                if state.get("loginRequired") is True and not require_ready:
                    return state
            except Exception as exc:  # pragma: no cover - exercised in live retry paths
                last_error = exc
            time.sleep(delay_seconds)
        if require_ready:
            if last_state:
                preview = str(last_state.get("bodyPreview") or "").strip()
                if last_state.get("loginRequired") is True:
                    raise RuntimeError("WhatsApp Web is not logged in; scan the QR code in the dedicated Chrome profile before exporting.")
                detail = f"; page preview: {preview}" if preview else ""
                raise RuntimeError(f"WhatsApp Web did not finish rendering the chat list before export{detail}")
            if last_error is not None:
                raise RuntimeError(f"Timed out waiting for WhatsApp Web readiness on port {self.port}: {last_error}") from last_error
            raise RuntimeError(f"Timed out waiting for WhatsApp Web readiness on port {self.port}")
        return last_state or {"ready": False, "error": str(last_error) if last_error else "Timed out waiting for WhatsApp Web readiness"}

    def version(self) -> dict[str, Any]:
        return dict(self._run_action("version", lambda: self._fetch_json("/json/version")))

    def list_targets(self) -> list[dict[str, Any]]:
        payload = self._run_action("list", lambda: self._fetch_json("/json/list"))
        return list(payload)

    def marker_targets(self) -> list[dict[str, Any]]:
        return [target for target in self._page_targets() if self._matches_marker_target(target)]

    def evaluate(self, expression: str) -> str:
        def run() -> str:
            target = self._choose_target(require_target_url=True, prefer_marker_window=True)
            result = self._send(target, "Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
            if result.get("exceptionDetails"):
                details = result["exceptionDetails"]
                raise RuntimeError(details.get("text") if isinstance(details, dict) else "Runtime.evaluate failed")
            remote_value = result.get("result", {})
            value = remote_value.get("value") if isinstance(remote_value, dict) else None
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            return json.dumps(value)

        return str(self._run_action("evaluate", run))

    def read_cached_media(
        self,
        file_hash: str,
        *,
        max_bytes: int,
        chunk_bytes: int = 4_000_000,
    ) -> dict[str, Any]:
        """Read decrypted WhatsApp media from Cache Storage in bounded CDP chunks."""

        def run() -> dict[str, Any]:
            cache_key = json.dumps(file_hash)
            prepare_expression = f'''(async () => {{
                const fileHash = {cache_key};
                let metadata = null;
                try {{
                    const database = await new Promise((resolve, reject) => {{
                        const request = indexedDB.open('lru-media-storage-idb');
                        request.onsuccess = () => resolve(request.result);
                        request.onerror = () => reject(request.error);
                    }});
                    metadata = await new Promise((resolve, reject) => {{
                        const transaction = database.transaction('lru-media-meta-info', 'readonly');
                        const request = transaction.objectStore('lru-media-meta-info').get(fileHash);
                        request.onsuccess = () => resolve(request.result || null);
                        request.onerror = () => reject(request.error);
                    }});
                    database.close();
                }} catch (_) {{
                    metadata = null;
                }}
                const cache = await caches.open('lru-media-array-buffer-cache');
                const url = 'https://_media_cache_v2_.whatsapp.com/lru-media-array-buffer-cache_' + encodeURIComponent(fileHash);
                const response = await cache.match(url);
                if (!response) return JSON.stringify({{available:false,metadataPresent:!!metadata,metadataSizeBytes:metadata && metadata.size || null}});
                const blob = await response.blob();
                window.__waCollectorMediaReads = window.__waCollectorMediaReads || new Map();
                window.__waCollectorMediaReads.set(fileHash, blob);
                return JSON.stringify({{available:true,sizeBytes:blob.size,mimeType:blob.type || null,metadataPresent:!!metadata,metadataSizeBytes:metadata && metadata.size || null}});
            }})()'''
            info = json.loads(self.evaluate(prepare_expression))
            if not info.get("available"):
                return info
            size_bytes = int(info.get("sizeBytes") or 0)
            if size_bytes > max(0, int(max_bytes)):
                self._release_cached_media(file_hash)
                return {
                    **info,
                    "data": None,
                    "skippedReason": "file-size-limit",
                }

            data = bytearray()
            try:
                for offset in range(0, size_bytes, max(1, int(chunk_bytes))):
                    length = min(max(1, int(chunk_bytes)), size_bytes - offset)
                    chunk_expression = f'''(async () => {{
                        const fileHash = {cache_key};
                        const reads = window.__waCollectorMediaReads;
                        const blob = reads && reads.get(fileHash);
                        if (!blob) return JSON.stringify({{ok:false,error:'cache-read-not-prepared'}});
                        const chunk = blob.slice({offset}, {offset + length});
                        const dataUrl = await new Promise((resolve, reject) => {{
                            const reader = new FileReader();
                            reader.onload = () => resolve(String(reader.result || ''));
                            reader.onerror = () => reject(reader.error);
                            reader.readAsDataURL(chunk);
                        }});
                        return JSON.stringify({{ok:true,data:dataUrl.split(',', 2)[1] || ''}});
                    }})()'''
                    chunk = json.loads(self.evaluate(chunk_expression))
                    if not chunk.get("ok"):
                        raise RuntimeError(str(chunk.get("error") or "WhatsApp media cache chunk read failed"))
                    data.extend(base64.b64decode(str(chunk.get("data") or ""), validate=True))
            finally:
                self._release_cached_media(file_hash)
            if len(data) != size_bytes:
                raise RuntimeError(f"WhatsApp media cache returned {len(data)} of {size_bytes} bytes")
            return {**info, "data": bytes(data), "source": "whatsapp-media-cache"}

        return dict(self._run_action("read-cached-media", run))

    def request_visible_media_download(self, message_id: str, *, file_hash: str | None, force: bool) -> dict[str, Any]:
        """Ask WhatsApp's live message model to fetch and decrypt one visible media item."""

        expression = f'''(async () => {{
            const targetId = {json.dumps(message_id)};
            const expectedHash = {json.dumps(file_hash)};
            const messageId = (value) => typeof value === 'string'
                ? value
                : value && (value._serialized || value.$1 || String(value));
            let message = window.__waCollectorSelectedMediaMessages && window.__waCollectorSelectedMediaMessages.get(targetId) || null;
            for (const container of [...document.querySelectorAll('#main [data-testid="msg-container"]')]) {{
                if (message) break;
                const fiberKey = Object.keys(container).find(key => key.startsWith('__reactFiber$'));
                let fiber = fiberKey ? container[fiberKey] : null;
                while (fiber) {{
                    const candidate = fiber.memoizedProps && fiber.memoizedProps.msg;
                    if (candidate && messageId(candidate.id) === targetId) {{
                        message = candidate;
                        break;
                    }}
                    fiber = fiber.return;
                }}
                if (message) break;
            }}
            if (!message) return JSON.stringify({{ok:false,error:'message-not-visible'}});
            if (expectedHash && message.filehash && message.filehash !== expectedHash) {{
                return JSON.stringify({{ok:false,error:'message-filehash-mismatch'}});
            }}
            try {{
                if ({str(bool(force)).lower()}) {{
                    if (typeof message.forceDownloadMediaEvenIfExpensive !== 'function') {{
                        return JSON.stringify({{ok:false,error:'forced-download-method-unavailable'}});
                    }}
                    await message.forceDownloadMediaEvenIfExpensive();
                }} else {{
                    if (typeof message.downloadMedia !== 'function') {{
                        return JSON.stringify({{ok:false,error:'download-method-unavailable'}});
                    }}
                    await message.downloadMedia({{downloadEvenIfExpensive:true}});
                }}
                const hash = expectedHash || message.filehash || '';
                const cache = await caches.open('lru-media-array-buffer-cache');
                const url = 'https://_media_cache_v2_.whatsapp.com/lru-media-array-buffer-cache_' + encodeURIComponent(hash);
                for (let pass = 0; pass < 40; pass += 1) {{
                    const response = hash ? await cache.match(url) : null;
                    if (response) {{
                        const blob = await response.blob();
                        return JSON.stringify({{ok:true,cachePresent:true,sizeBytes:blob.size,method:{json.dumps('forced-message-model' if force else 'message-model')}}});
                    }}
                    await new Promise(resolve => setTimeout(resolve, 250));
                }}
                return JSON.stringify({{ok:false,error:'download-completed-without-cache-entry'}});
            }} catch (error) {{
                return JSON.stringify({{ok:false,error:String(error && error.stack || error)}});
            }}
        }})()'''
        return dict(json.loads(self.evaluate(expression)))

    def trigger_visible_attachment_download(self, message_id: str, *, viewer: bool = False) -> dict[str, Any]:
        """Trigger only WhatsApp's inbound Download action for an exact visible message."""

        def run() -> dict[str, Any]:
            target = self._choose_target(require_target_url=True, prefer_marker_window=True)
            message_expression = f'''(() => {{
                const targetId = {json.dumps(message_id)};
                const messageId = (value) => typeof value === 'string'
                    ? value
                    : value && (value._serialized || value.$1 || String(value));
                for (const container of [...document.querySelectorAll('#main [data-testid="msg-container"]')]) {{
                    const fiberKey = Object.keys(container).find(key => key.startsWith('__reactFiber$'));
                    let fiber = fiberKey ? container[fiberKey] : null;
                    while (fiber) {{
                        const candidate = fiber.memoizedProps && fiber.memoizedProps.msg;
                        if (candidate && messageId(candidate.id) === targetId) {{
                            const clickTarget = {str(bool(viewer)).lower()}
                                ? (container.querySelector('[data-testid="document-thumb"]') || container.querySelector('[data-icon="document"]') || container)
                                : container;
                            clickTarget.scrollIntoView({{block:'center'}});
                            const rect = clickTarget.getBoundingClientRect();
                            return {{x:rect.left + rect.width / 2,y:rect.top + rect.height / 2}};
                        }}
                        fiber = fiber.return;
                    }}
                }}
                return null;
            }})()'''
            point = self._evaluate_point(target, message_expression)
            if viewer:
                self._dispatch_mouse_click(target, point, button="left")
                time.sleep(0.8)
                control_expression = '''(() => {
                    const candidates = [...document.querySelectorAll('[aria-label="Download"], [data-icon="download"]')]
                        .map(element => element.closest('button,[role="button"]') || element)
                        .filter(element => element && element.offsetParent !== null);
                    const element = candidates[0];
                    if (!element) return null;
                    const rect = element.getBoundingClientRect();
                    return {x:rect.left + rect.width / 2,y:rect.top + rect.height / 2};
                })()'''
                control_point = self._evaluate_point(target, control_expression)
                self._dispatch_mouse_click(target, control_point, button="left")
                return {"ok": True, "method": "document-viewer"}

            self._dispatch_mouse_click(target, point, button="right")
            time.sleep(0.35)
            menu_expression = '''(() => {
                const items = [...document.querySelectorAll('[role="menuitem"]')].filter(element => element.offsetParent !== null);
                const element = items.find(item => /^download$/i.test((item.innerText || '').trim()))
                    || items.find(item => item.querySelector('[data-icon="download"]'));
                if (!element) return null;
                const rect = element.getBoundingClientRect();
                return {x:rect.left + rect.width / 2,y:rect.top + rect.height / 2};
            })()'''
            menu_point = self._evaluate_point(target, menu_expression)
            self._dispatch_mouse_click(target, menu_point, button="left")
            return {"ok": True, "method": "context-menu"}

        return dict(self._run_action("download-visible-attachment", run))

    def dismiss_transient_ui(self) -> None:
        def run() -> None:
            target = self._choose_target(require_target_url=True, prefer_marker_window=True)
            for event_type in ("keyDown", "keyUp"):
                self._send(
                    target,
                    "Input.dispatchKeyEvent",
                    {
                        "type": event_type,
                        "key": "Escape",
                        "code": "Escape",
                        "windowsVirtualKeyCode": 27,
                        "nativeVirtualKeyCode": 53,
                    },
                )

        self._run_action("dismiss-transient-ui", run)

    def place_window(self, *, left: int, top: int, width: int, height: int) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            target = self._choose_target(require_target_url=False, prefer_marker_window=False)
            window_result = self._send(target, "Browser.getWindowForTarget", {"targetId": target["id"]})
            window_id = window_result["windowId"]
            self._send(
                target,
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "left": int(left),
                        "top": int(top),
                        "width": int(width),
                        "height": int(height),
                        "windowState": "normal",
                    },
                },
            )
            target_tab = None
            if self.target_url_substring:
                target_tab = next((item for item in self._page_targets() if self._matches_url_target(item)), None)
            selected = target_tab or target
            if target_tab is not None:
                self._send(target_tab, "Target.activateTarget", {"targetId": target_tab["id"]})
            return {
                "windowId": window_id,
                "targetId": selected.get("id"),
                "title": selected.get("title"),
                "url": selected.get("url"),
            }

        return dict(self._run_action("place-window", run))

    def click_point(self, expression: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            target = self._choose_target(require_target_url=True, prefer_marker_window=True)
            point = self._evaluate_point(target, expression)
            self._dispatch_mouse_click(target, point, button="left")
            return point

        return dict(self._run_action("click-point", run))

    def open_chat_via_search(
        self,
        chat_name: str,
        *,
        attempts: int = 20,
        delay_seconds: float = 0.25,
    ) -> dict[str, Any]:
        """Open an exact chat using only WhatsApp's sidebar search input."""
        target_name = str(chat_name or "").strip()
        if not target_name:
            raise ValueError("A non-empty chat name is required for sidebar search")

        def run() -> dict[str, Any]:
            target = self._choose_target(require_target_url=True, prefer_marker_window=True)
            input_expression = '''(() => {
                /* WA_COLLECTOR_SEARCH_INPUT_POINT */
                const input = document.querySelector('#side input[role="textbox"][aria-label="Search or start a new chat"]')
                    || document.querySelector('#side input[role="textbox"][placeholder="Search or start a new chat"]')
                    || document.querySelector('#side input[role="textbox"][data-tab="3"]');
                if (!input || input.offsetParent === null) return null;
                input.scrollIntoView({block:'center'});
                const rect = input.getBoundingClientRect();
                return {x:rect.left + rect.width / 2,y:rect.top + rect.height / 2};
            })()'''
            input_point = self._evaluate_point(target, input_expression)
            self._dispatch_mouse_click(target, input_point, button="left")

            active_expression = '''(() => {
                /* WA_COLLECTOR_SEARCH_ACTIVE_POINT */
                const input = document.querySelector('#side input[role="textbox"][aria-label="Search or start a new chat"]')
                    || document.querySelector('#side input[role="textbox"][placeholder="Search or start a new chat"]')
                    || document.querySelector('#side input[role="textbox"][data-tab="3"]');
                if (!input || document.activeElement !== input) return null;
                input.select();
                const rect = input.getBoundingClientRect();
                return {x:rect.left + rect.width / 2,y:rect.top + rect.height / 2};
            })()'''
            self._evaluate_point(target, active_expression)
            self._send(target, "Input.insertText", {"text": target_name})

            name_json = json.dumps(target_name)
            result_expression = rf'''(() => {{
                /* WA_COLLECTOR_SEARCH_RESULT_POINT */
                const targetName = {name_json};
                const normalize = value => String(value || '').normalize('NFKC').replace(/[\u200e\u200f]/g, '').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
                const wanted = normalize(targetName);
                const rows = [...document.querySelectorAll('#pane-side [role="row"], #pane-side [data-testid^="list-item-"]')]
                    .filter(element => element.offsetParent !== null);
                const row = rows.find(element => {{
                    const titles = [...element.querySelectorAll('[title]')].map(item => item.getAttribute('title'));
                    if (titles.some(title => normalize(title) === wanted)) return true;
                    const firstLine = String(element.innerText || '').split('\n').map(normalize).find(Boolean);
                    return firstLine === wanted;
                }});
                if (!row) return null;
                row.scrollIntoView({{block:'center'}});
                const rect = row.getBoundingClientRect();
                return {{x:rect.left + rect.width / 2,y:rect.top + rect.height / 2}};
            }})()'''
            result_point: dict[str, float] | None = None
            last_error: Exception | None = None
            for _ in range(max(1, int(attempts))):
                try:
                    result_point = self._evaluate_point(target, result_expression)
                    break
                except RuntimeError as exc:
                    last_error = exc
                    time.sleep(max(0.0, float(delay_seconds)))
            if result_point is None:
                raise RuntimeError(
                    f"No exact WhatsApp sidebar search result appeared for {target_name!r}: {last_error}"
                ) from last_error
            self._dispatch_mouse_click(target, result_point, button="left")
            time.sleep(0.6)
            self._clear_sidebar_search_for_target(target)
            return {
                "ok": True,
                "chatName": target_name,
                "searchInputPoint": input_point,
                "resultPoint": result_point,
            }

        return dict(self._run_action("open-chat-via-search", run))

    def clear_sidebar_search(self) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            target = self._choose_target(require_target_url=True, prefer_marker_window=True)
            return self._clear_sidebar_search_for_target(target)

        return dict(self._run_action("clear-sidebar-search", run))

    def _clear_sidebar_search_for_target(self, target: dict[str, Any]) -> dict[str, Any]:
        expression = '''(() => {
            /* WA_COLLECTOR_CLEAR_SEARCH_POINT */
            const input = document.querySelector('#side input[role="textbox"][aria-label="Search or start a new chat"]')
                || document.querySelector('#side input[role="textbox"][placeholder="Search or start a new chat"]')
                || document.querySelector('#side input[role="textbox"][data-tab="3"]');
            if (!input || input.offsetParent === null) return null;
            input.focus();
            input.select();
            const rect = input.getBoundingClientRect();
            return {x:rect.left + rect.width / 2,y:rect.top + rect.height / 2};
        })()'''
        point = self._evaluate_point(target, expression)
        for event_type in ("keyDown", "keyUp"):
            self._send(
                target,
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "key": "Backspace",
                    "code": "Backspace",
                    "windowsVirtualKeyCode": 8,
                    "nativeVirtualKeyCode": 51,
                },
            )
        time.sleep(0.15)
        return {"ok": True, "searchInputPoint": point}

    def _release_cached_media(self, file_hash: str) -> None:
        expression = f'''(() => {{
            const reads = window.__waCollectorMediaReads;
            if (reads) reads.delete({json.dumps(file_hash)});
            return true;
        }})()'''
        try:
            self.evaluate(expression)
        except Exception:
            pass

    def _evaluate_point(self, target: dict[str, Any], expression: str) -> dict[str, float]:
        result = self._send(target, "Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise RuntimeError(details.get("text") if isinstance(details, dict) else "Runtime.evaluate failed while locating click point")
        remote_value = result.get("result", {})
        point = remote_value.get("value") if isinstance(remote_value, dict) else None
        if not isinstance(point, dict) or not isinstance(point.get("x"), (int, float)) or not isinstance(point.get("y"), (int, float)):
            raise RuntimeError("Click expression did not return numeric {x,y}")
        return {"x": float(point["x"]), "y": float(point["y"])}

    def _dispatch_mouse_click(self, target: dict[str, Any], point: dict[str, float], *, button: str) -> None:
        pressed_buttons = 2 if button == "right" else 1
        for event in (
            {"type": "mouseMoved", "x": point["x"], "y": point["y"], "button": button, "buttons": pressed_buttons, "clickCount": 0},
            {"type": "mousePressed", "x": point["x"], "y": point["y"], "button": button, "buttons": pressed_buttons, "clickCount": 1},
            {"type": "mouseReleased", "x": point["x"], "y": point["y"], "button": button, "buttons": 0, "clickCount": 1},
        ):
            self._send(target, "Input.dispatchMouseEvent", event)

    def _fetch_json(self, path: str) -> Any:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=_DEFAULT_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc
        return json.loads(body)

    def _send(self, target: dict[str, Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        if not websocket_url:
            raise RuntimeError("Chrome DevTools target does not expose a webSocketDebuggerUrl")
        return _CDPClient(websocket_url).send(method, params or {})

    def _page_targets(self) -> list[dict[str, Any]]:
        return [target for target in self.list_targets() if target.get("type") == "page" and target.get("webSocketDebuggerUrl")]

    def _choose_target(self, *, require_target_url: bool = False, prefer_marker_window: bool = False) -> dict[str, Any]:
        targets = self._page_targets()
        marker_targets = [target for target in targets if self._matches_marker_target(target)]
        url_targets = [target for target in targets if self._matches_url_target(target)]

        if prefer_marker_window and marker_targets and url_targets:
            marker_window_ids: set[Any] = set()
            for marker in marker_targets:
                try:
                    marker_window_ids.add(self._window_id(marker))
                except Exception:
                    continue
            for candidate in url_targets:
                try:
                    if self._window_id(candidate) in marker_window_ids:
                        return candidate
                except Exception:
                    continue

        if url_targets:
            return url_targets[0]
        if not require_target_url and marker_targets:
            return marker_targets[0]
        raise RuntimeError("No matching Chrome DevTools target found")

    def _window_id(self, target: dict[str, Any]) -> Any:
        return self._send(target, "Browser.getWindowForTarget", {"targetId": target["id"]}).get("windowId")

    def _matches_marker_target(self, target: dict[str, Any]) -> bool:
        return self._contains(target.get("title"), self.marker_title) or self._contains(target.get("url"), self.marker_url_substring)

    def _matches_url_target(self, target: dict[str, Any]) -> bool:
        return self._contains(target.get("url"), self.target_url_substring)

    @staticmethod
    def _contains(haystack: object, needle: str | None) -> bool:
        return bool(needle) and needle in str(haystack or "")
