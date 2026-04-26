from __future__ import annotations

import json
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from whatsapp_collector.chrome_session import ChromeTarget, ChromeWhatsAppSession
from whatsapp_collector.collector import MAX_MESSAGE_LOOKBACK_HARD_LIMIT, WhatsAppCollector
from whatsapp_collector.launcher import (
    DEFAULT_DEBUG_PORT,
    DEFAULT_MARKER_TITLE,
    DEFAULT_MARKER_URL_SUBSTRING,
    DEFAULT_PROFILE_DIR,
    DEFAULT_TARGET_URL,
    ensure_dedicated_whatsapp_window,
)
from whatsapp_collector.scheduler import install_schedule, remove_schedule, schedule_status

DEFAULT_UI_OUTPUT_PATH = Path("~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json")

CollectExport = Callable[..., dict[str, Any]]
EnsureWindow = Callable[..., dict[str, Any]]
InstallSchedule = Callable[..., dict[str, Any]]
RemoveSchedule = Callable[[], dict[str, Any]]
ScheduleStatus = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class UIConfig:
    output_path: Path = DEFAULT_UI_OUTPUT_PATH
    profile_dir: Path = DEFAULT_PROFILE_DIR
    host: str = "127.0.0.1"
    port: int = 8765
    debug_port: int = DEFAULT_DEBUG_PORT
    marker_title: str = DEFAULT_MARKER_TITLE
    marker_url_substring: str = DEFAULT_MARKER_URL_SUBSTRING
    target_url: str = DEFAULT_TARGET_URL
    display_name: str | None = None
    account_label: str = "WhatsApp"
    max_messages: int = MAX_MESSAGE_LOOKBACK_HARD_LIMIT


def render_dashboard_html(config: UIConfig) -> str:
    config_json = json.dumps(_public_config(config), ensure_ascii=False)
    output_display_path = _display_path(config.output_path)
    profile_display_path = _display_path(config.profile_dir)
    ai_prompt = _ai_harness_prompt(output_display_path)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WhatsApp Collector</title>
  <style>
    :root {{
      --bg: #07080d; --panel: rgba(255,255,255,.075); --panel-strong: rgba(255,255,255,.12);
      --text: #f7f7fb; --muted: #a7adbc; --line: rgba(255,255,255,.14); --green: #25d366;
      --blue: #8ab4ff; --amber: #ffd166; --danger: #ff6b7a; --shadow: 0 24px 80px rgba(0,0,0,.42);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; min-height:100vh; color:var(--text); font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:
      radial-gradient(circle at 18% 10%, rgba(37,211,102,.18), transparent 30%),
      radial-gradient(circle at 82% 0%, rgba(138,180,255,.2), transparent 28%), var(--bg); }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 34px 22px 60px; }}
    header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom: 26px; }}
    .eyebrow {{ color: var(--green); text-transform: uppercase; letter-spacing:.18em; font-size:12px; font-weight:750; }}
    h1 {{ margin:8px 0 8px; font-size: clamp(36px, 6vw, 72px); line-height:.92; letter-spacing:-.06em; }}
    .lead {{ max-width:760px; color:var(--muted); font-size:17px; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:9px 12px; color:var(--muted); background:rgba(255,255,255,.055); white-space:nowrap; }}
    .grid {{ display:grid; grid-template-columns: 1.05fr .95fr; gap:18px; }}
    .cards {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:14px; margin-bottom:18px; }}
    .card, .panel {{ border:1px solid var(--line); background:var(--panel); box-shadow:var(--shadow); border-radius:24px; backdrop-filter: blur(16px); }}
    .card {{ padding:18px; min-height:118px; }}
    .card .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.13em; }}
    .card .value {{ font-size:31px; margin-top:13px; letter-spacing:-.04em; font-weight:760; }}
    .panel {{ padding:20px; }}
    .panel h2 {{ margin:0 0 14px; font-size:18px; letter-spacing:-.02em; }}
    label {{ display:block; color:var(--muted); font-size:12px; margin:14px 0 7px; text-transform:uppercase; letter-spacing:.12em; }}
    input {{ width:100%; color:var(--text); background:rgba(0,0,0,.28); border:1px solid var(--line); border-radius:14px; padding:12px 13px; outline:none; }}
    input:focus {{ border-color:rgba(37,211,102,.7); box-shadow:0 0 0 4px rgba(37,211,102,.11); }}
    .row {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .field-help {{ margin:0 0 10px; color:#c2c8d6; font-size:12.5px; line-height:1.45; }}
    .field-help strong {{ color:var(--text); font-weight:720; }}
    .mini-note {{ margin-top:7px; color:var(--muted); font-size:12px; line-height:1.4; }}
    .path-box {{ margin-top:8px; padding:10px 12px; border-radius:13px; background:rgba(37,211,102,.08); border:1px solid rgba(37,211,102,.2); color:#d8ffe8; font:12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap:anywhere; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:18px; }}
    button {{ border:0; border-radius:14px; padding:12px 15px; font-weight:750; cursor:pointer; color:#05110a; background:var(--green); }}
    button.secondary {{ color:var(--text); background:var(--panel-strong); border:1px solid var(--line); }}
    button.warn {{ background:var(--amber); }}
    pre, textarea.copy-box {{ margin:0; max-height:360px; overflow:auto; padding:16px; border-radius:18px; background:#030407; border:1px solid var(--line); color:#d8ffe8; font:12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; white-space:pre-wrap; overflow-wrap:anywhere; }}
    textarea.copy-box {{ width:100%; min-height:150px; resize:vertical; }}
    details summary {{ cursor:pointer; font-weight:760; font-size:18px; letter-spacing:-.02em; }}
    details .field-help {{ margin-top:8px; }}
    .status {{ display:flex; align-items:center; gap:8px; color:var(--muted); margin-top:8px; }}
    .dot {{ width:9px; height:9px; border-radius:99px; background:var(--amber); box-shadow:0 0 22px var(--amber); }}
    .dot.ok {{ background:var(--green); box-shadow:0 0 22px var(--green); }}
    .threads {{ display:grid; gap:10px; margin-top:12px; }}
    .thread {{ padding:13px; border:1px solid var(--line); border-radius:16px; background:rgba(0,0,0,.18); }}
    .thread b {{ display:block; margin-bottom:4px; }} .thread span {{ color:var(--muted); }}
    @media (max-width: 900px) {{ .grid, .cards, .row {{ grid-template-columns:1fr; }} header {{ display:block; }} .pill {{ display:inline-block; margin-top:14px; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div><div class="eyebrow">Read-only local control plane</div><h1>WhatsApp Collector</h1><p class="lead">Launch/login, run exports, inspect output freshness, and tune collection settings without editing shell scripts. No send path. No composer access. Localhost only.</p></div>
      <div class="pill" id="endpoint">{config.host}:{config.port}</div>
    </header>
    <section class="cards">
      <div class="card"><div class="label">Chats exported</div><div class="value" id="threadCount">—</div></div>
      <div class="card"><div class="label">Export status</div><div class="value" id="exportAge">—</div></div>
      <div class="card"><div class="label">Runtime</div><div class="value" id="runtimeState">Idle</div></div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Collector controls</h2>
        <p class="field-help"><strong>Start here:</strong> click Launch / Login to open a separate Chrome window for WhatsApp Web, scan the QR code if needed, then click Run Export to write the JSON file below.</p>
        <div class="row">
          <div>
            <label for="maxMessages">Messages per conversation</label>
            <p class="field-help">Maximum recent messages saved for each chat thread. This does not limit how many chats are collected.</p>
            <input id="maxMessages" type="number" min="1" value="{config.max_messages}" />
          </div>
          <div>
            <label for="accountLabel">Export account name</label>
            <p class="field-help">A friendly label stored in the JSON under account.accountLabel so downstream tools know which WhatsApp account produced the export.</p>
            <input id="accountLabel" value="{_escape_attr(config.account_label)}" />
          </div>
        </div>
        <label for="displayName">Monitor to open Chrome on <span style="text-transform:none;letter-spacing:0;color:#c2c8d6">(optional)</span></label>
        <p class="field-help">This is a monitor, not a WhatsApp username. The collector needs a dedicated Chrome window so you can log in and keep WhatsApp Web loaded; this setting only chooses which screen that window appears on. Leave blank unless you want the login window on a specific screen.</p>
        <input id="displayName" placeholder="Example: Studio Display. Leave blank to use the first available screen." value="{_escape_attr(config.display_name or '')}" />
        <div class="row">
          <div>
            <label for="profileDir">Chrome profile folder</label>
            <p class="field-help">Private Chrome data for this collector window. Most users should leave this unchanged. Hidden folders start with a dot; in Finder use Finder → Go → Go to Folder and paste this path to open it.</p>
            <input id="profileDir" value="{_escape_attr(str(config.profile_dir))}" />
            <div class="path-box" id="profileDirHint">Current profile folder: {_escape_html(profile_display_path)}</div>
            <div class="actions"><button class="secondary" onclick="copyText('profileDir')">Copy profile folder</button></div>
            <div class="mini-note">If you delete this folder, WhatsApp will ask you to log in again.</div>
          </div>
          <div>
            <label for="outputPath">Export data file path</label>
            <p class="field-help">This is the data file the collector writes when you click Run Export. It is saved as JSON so other tools can read it. Open or copy this exact path when another app asks for the WhatsApp Collector data file.</p>
            <input id="outputPath" value="{_escape_attr(str(config.output_path))}" />
            <div class="path-box" id="outputPathHint">Current file: {_escape_html(output_display_path)}</div>
            <div class="actions"><button class="secondary" onclick="copyText('outputPath')">Copy data file path</button></div>
          </div>
        </div>
        <div class="actions"><button onclick="launchLogin()">Launch / Login</button><button onclick="runExport()">Run Export</button><button class="secondary" onclick="refreshStatus()">Refresh</button><button class="secondary" onclick="loadExport()">Preview Export</button></div>
        <div class="status"><span class="dot" id="dot"></span><span id="statusText">Ready.</span></div>
      </div>
      <div class="panel"><h2>Export preview</h2><div class="threads" id="threads"><span style="color:var(--muted)">No export loaded yet. Click Preview Export or Run Export.</span></div></div>
    </section>
    <section class="panel" style="margin-top:18px">
      <h2>Use this export with your AI tools</h2>
      <p class="field-help">Copy this prompt into your AI harness, agent, or automation tool so it knows where the regularly updated WhatsApp Collector data file lives. The AI tool needs local file access to this path; browser-only chat tools usually cannot read local files unless you upload the JSON.</p>
      <textarea class="copy-box" id="aiPrompt" readonly>{_escape_html(ai_prompt)}</textarea>
      <div class="actions"><button class="secondary" onclick="copyText('aiPrompt')">Copy AI prompt</button></div>
      <h2 style="margin-top:22px">Automatic exports</h2>
      <p class="field-help">Set this up here — no Terminal command copying. WhatsApp Collector installs a macOS background schedule that opens the menu-bar app if needed and refreshes the same export file shown above.</p>
      <div class="row">
        <div>
          <label for="scheduleInterval">Every</label>
          <input id="scheduleInterval" type="number" min="1" value="15" />
        </div>
        <div>
          <label>Automatic export status</label>
          <div class="path-box" id="scheduleStatus">Automatic exports: checking…</div>
        </div>
      </div>
      <div class="actions"><button onclick="startSchedule()">Start automatic exports</button><button class="secondary" onclick="stopSchedule()">Stop automatic exports</button><button class="secondary" onclick="refreshScheduleStatus()">Refresh schedule status</button></div>
      <p class="mini-note">This uses a macOS background schedule while you are logged in. Keep WhatsApp Web logged in inside the collector Chrome profile.</p>
    </section>
    <section class="panel" style="margin-top:18px"><details><summary>Advanced diagnostics</summary><p class="field-help">Raw JSON for troubleshooting. Most users can ignore this unless an export fails or another app asks for diagnostics.</p><pre id="log">{config_json}</pre></details></section>
  </main>
<script>
const initialConfig = {config_json};
function cfg() {{ return {{ maxMessages: Number(document.getElementById('maxMessages').value || 15), accountLabel: document.getElementById('accountLabel').value, displayName: document.getElementById('displayName').value || null, profileDir: document.getElementById('profileDir').value, outputPath: document.getElementById('outputPath').value }}; }}
function setBusy(text) {{ document.getElementById('runtimeState').textContent='Busy'; document.getElementById('statusText').textContent=text; document.getElementById('dot').className='dot'; }}
function setOk(text) {{ document.getElementById('runtimeState').textContent='Ready'; document.getElementById('statusText').textContent=text; document.getElementById('dot').className='dot ok'; }}
function setError(prefix, error) {{ document.getElementById('runtimeState').textContent='Failed'; document.getElementById('statusText').textContent=prefix + ': ' + errorMessage(error); document.getElementById('dot').className='dot'; }}
function errorMessage(error) {{ if (!error) return 'Unknown error'; if (typeof error === 'string') return error; return error.error || error.message || JSON.stringify(error); }}
function setLog(x) {{ document.getElementById('log').textContent=JSON.stringify(x,null,2); }}
async function post(url, body) {{ const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}); const j=await r.json(); if(!r.ok) throw j; return j; }}
async function get(url) {{ const r=await fetch(url); const j=await r.json(); if(!r.ok) throw j; return j; }}
async function refreshStatus() {{ try {{ const j=await get('/api/status'); document.getElementById('threadCount').textContent=j.export.threadCount ?? 0; document.getElementById('exportAge').textContent=j.export.updatedAt ? 'Ready' : 'No export yet'; setLog(j); setOk('Status refreshed.'); }} catch(e) {{ setLog(e); }} }}
async function launchLogin() {{ setBusy('Opening dedicated Chrome profile...'); try {{ const j=await post('/api/window/ensure', cfg()); setLog(j); setOk('Window ready. Scan QR if WhatsApp asks.'); }} catch(e) {{ setLog(e); setError('Launch failed', e); }} }}
async function runExport() {{ setBusy('Collecting export...'); try {{ const j=await post('/api/export/run', cfg()); setLog(j); setOk('Export complete. JSON saved to ' + currentOutputPathForHumans() + '.'); await loadExport(); await refreshStatus(); }} catch(e) {{ setLog(e); setError('Export failed', e); }} }}
async function loadExport() {{ try {{ const j=await get('/api/export'); const threads=j.threads||[]; document.getElementById('threadCount').textContent=threads.length; document.getElementById('threads').innerHTML=threads.slice(0,12).map(t=>`<div class="thread"><b>${{escapeHtml(t.chatTitle||t.threadKey||'Untitled')}}</b><span>${{escapeHtml((t.labelsRaw||[]).join(', '))}} · ${{(t.recentMessages||[]).length}} messages</span></div>`).join('') || '<span style="color:var(--muted)">No threads in the export yet.</span>'; setLog(j); }} catch(e) {{ setLog(e); }} }}
function pathForHumans(fieldId, configKey) {{ const path = document.getElementById(fieldId).value || initialConfig[configKey]; if (path.startsWith('/')) return path; if (path.startsWith('~')) return path + ' (expanded by the server when running)'; return `${{initialConfig.workingDirectory}}/${{path}}`; }}
function currentOutputPathForHumans() {{ return pathForHumans('outputPath', 'outputPath'); }}
function currentProfileDirForHumans() {{ return pathForHumans('profileDir', 'profileDir'); }}
function aiPromptForPath(path) {{ return `My most recent WhatsApp Collector export is at:\n${{path}}\n\nIt is updated regularly. Treat this JSON file as a read-only local resource when answering questions about my WhatsApp conversations. You need local filesystem access to this path; if you cannot read local files directly, ask me to upload the JSON. If you need current WhatsApp context, read this file first, use its account metadata and threads/messages as source data, and cite that the information came from the local WhatsApp Collector export. Do not send messages or modify WhatsApp from this file.`; }}
function scheduleCfg() {{ return {{ ...cfg(), intervalMinutes: Math.max(1, Number(document.getElementById('scheduleInterval').value || 15)) }}; }}
function renderScheduleStatus(schedule) {{ const state = schedule.enabled ? (schedule.loaded ? 'On' : 'Configured') : 'Off'; const interval = schedule.intervalMinutes ? ` · every ${{schedule.intervalMinutes}} minutes` : ''; document.getElementById('scheduleStatus').textContent = `Automatic exports: ${{state}}${{interval}}. ${{schedule.nextStep || ''}}`; }}
async function refreshScheduleStatus() {{ try {{ const j=await get('/api/schedule'); renderScheduleStatus(j.schedule); setLog(j); }} catch(e) {{ setLog(e); setError('Schedule status failed', e); }} }}
async function startSchedule() {{ setBusy('Setting up automatic exports...'); try {{ const j=await post('/api/schedule/install', scheduleCfg()); renderScheduleStatus(j.schedule); setLog(j); setOk('Automatic exports are on.'); }} catch(e) {{ setLog(e); setError('Schedule setup failed', e); }} }}
async function stopSchedule() {{ setBusy('Turning off automatic exports...'); try {{ const j=await post('/api/schedule/remove', {{}}); renderScheduleStatus(j.schedule); setLog(j); setOk('Automatic exports are off.'); }} catch(e) {{ setLog(e); setError('Schedule stop failed', e); }} }}
function updatePathHints() {{ const outputPath = currentOutputPathForHumans(); document.getElementById('outputPathHint').textContent = 'Current file: ' + outputPath; document.getElementById('profileDirHint').textContent = 'Current profile folder: ' + currentProfileDirForHumans(); document.getElementById('aiPrompt').value = aiPromptForPath(outputPath); }}
async function copyText(id) {{ const value = document.getElementById(id).value || document.getElementById(id).textContent; try {{ await navigator.clipboard.writeText(value); setOk('Copied.'); }} catch(e) {{ setLog({{error:'copy-failed', detail:String(e)}}); }} }}
function escapeHtml(s) {{ return String(s).replace(/[&<>"']/g, c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
document.getElementById('outputPath').addEventListener('input', updatePathHints);
document.getElementById('profileDir').addEventListener('input', updatePathHints);
document.getElementById('maxMessages').addEventListener('input', updatePathHints);
document.getElementById('accountLabel').addEventListener('input', updatePathHints);
document.getElementById('displayName').addEventListener('input', updatePathHints);
document.getElementById('scheduleInterval').addEventListener('input', refreshScheduleStatus);
updatePathHints();
refreshStatus();
refreshScheduleStatus();
</script>
</body></html>"""


def _escape_attr(value: str) -> str:
    return _escape_html(value).replace('"', "&quot;")


def _escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _display_path(path: Path) -> str:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str((Path.cwd() / expanded).resolve())


def _ai_harness_prompt(output_display_path: str) -> str:
    return (
        "My most recent WhatsApp Collector export is at:\n"
        f"{output_display_path}\n\n"
        "It is updated regularly. Treat this JSON file as a read-only local resource when answering "
        "questions about my WhatsApp conversations. You need local filesystem access to this path; "
        "if you cannot read local files directly, ask me to upload the JSON. If you need current "
        "WhatsApp context, read this file first, use its account metadata and threads/messages as source data, and cite that the "
        "information came from the local WhatsApp Collector export. Do not send messages or modify "
        "WhatsApp from this file."
    )


def _public_config(config: UIConfig) -> dict[str, Any]:
    return {
        "outputPath": str(config.output_path),
        "outputPathDisplay": _display_path(config.output_path),
        "profileDir": str(config.profile_dir),
        "profileDirDisplay": _display_path(config.profile_dir),
        "workingDirectory": str(Path.cwd()),
        "host": config.host,
        "port": config.port,
        "debugPort": config.debug_port,
        "markerTitle": config.marker_title,
        "markerUrlSubstring": config.marker_url_substring,
        "targetUrl": config.target_url,
        "displayName": config.display_name,
        "accountLabel": config.account_label,
        "maxMessages": config.max_messages,
    }


def default_collect_export(
    *,
    account_label: str,
    max_messages: int,
    output_path: Path,
    debug_port: int,
    marker_title: str,
    marker_url_substring: str,
    target_url: str,
) -> dict[str, Any]:
    target = ChromeTarget(marker_title=marker_title, marker_url_substring=marker_url_substring, target_url_substring=target_url)
    session = ChromeWhatsAppSession(target=target, debug_port=debug_port)
    collector = WhatsAppCollector(session=session)
    return collector.collect_dashboard_export(account_label=account_label, max_messages=max_messages)


def create_app_handler(
    config: UIConfig,
    *,
    collect_export: CollectExport = default_collect_export,
    ensure_window: EnsureWindow = ensure_dedicated_whatsapp_window,
    install_schedule: InstallSchedule = install_schedule,
    remove_schedule: RemoveSchedule = remove_schedule,
    schedule_status: ScheduleStatus = schedule_status,
):
    class WhatsAppCollectorHandler(BaseHTTPRequestHandler):
        server_version = "WhatsAppCollectorUI/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/" or self.path.startswith("/?"):
                self._send_html(render_dashboard_html(config))
                return
            if self.path == "/api/status":
                self._send_json({"ok": True, "checkedAt": _now(), "config": _public_config(config), "export": _read_export_summary(config.output_path)})
                return
            if self.path == "/api/schedule":
                self._send_json({"ok": True, "checkedAt": _now(), "schedule": schedule_status()})
                return
            if self.path == "/api/export":
                if not config.output_path.exists():
                    self._send_json({"ok": False, "error": "export-not-found", "path": str(config.output_path)}, status=404)
                    return
                self._send_json(json.loads(config.output_path.read_text()))
                return
            self._send_json({"ok": False, "error": "not-found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = self._read_json_body()
                effective = _config_from_payload(config, payload)
                if self.path == "/api/window/ensure":
                    result = ensure_window(
                        profile_dir=effective.profile_dir.expanduser(),
                        display_name=effective.display_name,
                        placement_mode="visible",
                        marker_title=effective.marker_title,
                        marker_url_substring=effective.marker_url_substring,
                        target_url=effective.target_url,
                        debug_port=effective.debug_port,
                    )
                    self._send_json({"ok": True, "window": result})
                    return
                if self.path == "/api/export/run":
                    export = collect_export(
                        account_label=effective.account_label,
                        max_messages=effective.max_messages,
                        output_path=effective.output_path,
                        debug_port=effective.debug_port,
                        marker_title=effective.marker_title,
                        marker_url_substring=effective.marker_url_substring,
                        target_url=effective.target_url,
                    )
                    _write_atomic_json(export, effective.output_path)
                    self._send_json({"ok": True, "export": _read_export_summary(effective.output_path), "threadCount": len(export.get("threads", []))})
                    return
                if self.path == "/api/schedule/install":
                    result = install_schedule(
                        ui_url=self._base_url(),
                        payload=_schedule_payload(effective),
                        interval_minutes=_interval_minutes_from_payload(payload),
                    )
                    self._send_json({"ok": True, "schedule": result})
                    return
                if self.path == "/api/schedule/remove":
                    self._send_json({"ok": True, "schedule": remove_schedule()})
                    return
                self._send_json({"ok": False, "error": "not-found"}, status=404)
            except Exception as exc:  # pragma: no cover - defensive API boundary
                self._send_json({"ok": False, "error": str(exc)}, status=500)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _base_url(self) -> str:
            host, port = self.server.server_address[:2]
            return f"http://{host}:{port}"

        def _send_json(self, payload: Any, *, status: int = 200) -> None:
            body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return WhatsAppCollectorHandler


def _config_from_payload(config: UIConfig, payload: dict[str, Any]) -> UIConfig:
    return UIConfig(
        output_path=Path(payload.get("outputPath") or config.output_path).expanduser(),
        profile_dir=Path(payload.get("profileDir") or config.profile_dir).expanduser(),
        host=config.host,
        port=config.port,
        debug_port=int(payload.get("debugPort") or config.debug_port),
        marker_title=str(payload.get("markerTitle") or config.marker_title),
        marker_url_substring=str(payload.get("markerUrlSubstring") or config.marker_url_substring),
        target_url=str(payload.get("targetUrl") or config.target_url),
        display_name=(str(payload.get("displayName")).strip() if payload.get("displayName") else None),
        account_label=str(payload.get("accountLabel") or config.account_label),
        max_messages=max(1, int(payload.get("maxMessages") or config.max_messages)),
    )


def _schedule_payload(config: UIConfig) -> dict[str, Any]:
    return {
        "maxMessages": config.max_messages,
        "accountLabel": config.account_label,
        "displayName": config.display_name,
        "profileDir": str(config.profile_dir),
        "outputPath": str(config.output_path),
    }


def _interval_minutes_from_payload(payload: dict[str, Any]) -> int:
    return max(1, min(int(payload.get("intervalMinutes") or 15), 24 * 60))


def _read_export_summary(output_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(output_path),
        "exists": output_path.exists(),
        "threadCount": 0,
        "sizeBytes": 0,
        "updatedAt": None,
    }
    if not output_path.exists():
        return summary
    stat = output_path.stat()
    summary["sizeBytes"] = stat.st_size
    summary["updatedAt"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()
    try:
        payload = json.loads(output_path.read_text())
    except Exception as exc:
        summary["parseError"] = str(exc)
        return summary
    threads = payload.get("threads", [])
    summary["threadCount"] = len(threads) if isinstance(threads, list) else 0
    summary["exportedAt"] = payload.get("exportedAt")
    return summary


def _write_atomic_json(payload: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        backup_dir = output_path.parent / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{output_path.stem}.{backup_timestamp}{output_path.suffix}"
        suffix = 1
        while backup_path.exists():
            backup_path = backup_dir / f"{output_path.stem}.{backup_timestamp}-{suffix}{output_path.suffix}"
            suffix += 1
        backup_path.write_text(output_path.read_text())
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temp_path.replace(output_path)
    return output_path


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_ui_server(config: UIConfig, *, open_browser: bool = False) -> None:
    handler = create_app_handler(config)
    server = ThreadingHTTPServer((config.host, config.port), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"WhatsApp Collector UI running at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
