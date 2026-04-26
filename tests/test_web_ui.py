from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from whatsapp_collector.web_ui import UIConfig, create_app_handler, render_dashboard_html


def test_render_dashboard_html_has_setup_controls_without_display_assumption(tmp_path: Path) -> None:
    html = render_dashboard_html(
        UIConfig(
            output_path=tmp_path / "export.json",
            profile_dir=tmp_path / "profile",
            host="127.0.0.1",
            port=8765,
        )
    )

    assert "WhatsApp Collector" in html
    assert "Launch / Login" in html
    assert "Run Export" in html
    assert "Max messages" in html
    assert "Display name" in html
    assert "optional" in html.lower()
    assert "TV" not in html
    assert "Hermes" not in html


def test_ui_api_status_reports_export_and_config_without_live_collection(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    output.write_text(json.dumps({"threads": [{"threadKey": "t1"}], "exportedAt": "2026-04-26T00:00:00+00:00"}))
    config = UIConfig(output_path=output, profile_dir=tmp_path / "profile", host="127.0.0.1", port=0, max_messages=50)

    server, url = _start_test_server(config)
    try:
        status, payload = _json_request(url, "GET", "/api/status")
    finally:
        server.shutdown()

    assert status == 200
    assert payload["export"]["threadCount"] == 1
    assert payload["config"]["maxMessages"] == 50
    assert payload["config"]["displayName"] is None
    assert payload["config"]["markerTitle"] == "WhatsApp Collector"


def test_ui_api_run_export_uses_requested_max_messages(tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def fake_collect_export(**kwargs):
        calls.update(kwargs)
        return {
            "source": "whatsapp",
            "exportedAt": "2026-04-26T00:00:00+00:00",
            "maxRecentMessages": kwargs["max_messages"],
            "threads": [],
        }

    config = UIConfig(output_path=tmp_path / "export.json", profile_dir=tmp_path / "profile", host="127.0.0.1", port=0)
    server, url = _start_test_server(config, collect_export=fake_collect_export)
    try:
        status, payload = _json_request(url, "POST", "/api/export/run", {"maxMessages": 75, "accountLabel": "Ops"})
    finally:
        server.shutdown()

    assert status == 200
    assert payload["ok"] is True
    assert calls["max_messages"] == 75
    assert calls["account_label"] == "Ops"
    written = json.loads((tmp_path / "export.json").read_text())
    assert written["maxRecentMessages"] == 75


def _start_test_server(config: UIConfig, **deps):
    from http.server import ThreadingHTTPServer

    handler = create_app_handler(config, **deps)
    server = ThreadingHTTPServer((config.host, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None):
    host_port = base_url.removeprefix("http://")
    host, port_text = host_port.split(":")
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn = HTTPConnection(host, int(port_text), timeout=5)
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read().decode())
    conn.close()
    return response.status, data
