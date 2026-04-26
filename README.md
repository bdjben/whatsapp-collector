# WhatsApp Collector

Read-only WhatsApp Web collector for macOS. It works with both WhatsApp Web and WhatsApp Business Web, turning an already logged-in Chrome session into structured JSON exports for dashboards, automations, and local reporting.

This project is deliberately not a bot and not a sender. It never types into WhatsApp, never targets the composer, never clicks send, and never creates outbound messages.

## Features

- Installable Python package with a `whatsapp-collector` CLI.
- Drag-to-Applications macOS menu bar app (`W↗`) distributed as a signed DMG with the usual app-to-Applications install flow.
- Local polished web UI via `whatsapp-collector ui` for login/setup, export runs, settings, status, and export preview.
- Active Chrome session collection through AppleScript JavaScript from Apple Events.
- Optional dedicated Chrome profile collection through Chrome DevTools Protocol for exact no-focus targeting.
- No default display-name assumption: the dedicated window uses the first available macOS display unless `--display-name` is provided.
- Dedicated marker tab is named `WhatsApp Collector`.
- Label inventory and visible chat-list extraction.
- Labeled thread membership from WhatsApp Web IndexedDB.
- Configurable bounded recent-message windows through `--max-messages` or `WA_MAX_MESSAGES`.
- Stable export contract at `~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json` by default for the UI/macOS app.
- Atomic JSON writes with automatic backups before replacing an existing export.
- Runtime guardrails against send/composer JavaScript paths.

## Mac app

For non-developer installs, use the macOS DMG from the GitHub release:

```bash
curl -L -o WhatsApp-Collector-macOS.dmg \
  https://github.com/bdjben/whatsapp-collector/releases/latest/download/WhatsApp-Collector-macOS.dmg
open WhatsApp-Collector-macOS.dmg
```

A Finder window opens with `WhatsApp Collector.app` and an `Applications` shortcut. Drag the app onto `Applications`, then launch it from `/Applications` or Spotlight.

The app bundle is ad-hoc signed so macOS no longer reports it as damaged because of an invalid bundle signature. It is not Apple-notarized yet, so macOS may still show the normal unidentified-developer first-launch warning. If that happens, right-click `WhatsApp Collector.app` in `/Applications`, choose **Open**, and confirm once.

The app lives in the macOS menu bar as `W↗`. Use that menu to:

- open the local WhatsApp Collector UI
- show the output folder in Finder
- copy the exact output JSON path
- copy a ready-to-paste AI harness prompt that points at the latest export
- restart the local UI server
- quit the app

The app writes exports to a normal visible folder by default:

```text
~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json
```

Deleting the app from `/Applications` removes the app itself. Your exported JSON files remain in `~/Documents/WhatsApp Collector/Exports` so you do not accidentally lose collected data.

### If Launch / Login or Run Export says `No such file or directory: 'node'`

Use release `v0.3.5` or newer. Earlier dedicated-profile builds used a small Node.js DevTools helper, so Macs without Node installed could open Chrome successfully but still show a Launch / Login or Run Export failure mentioning `node`. The fixed app talks to Chrome DevTools directly from Python and does not require a system `node` binary.

### If Run Export says "Export failed"

Use release `v0.3.3` or newer for stale-port recovery. Earlier builds could leave Chrome's fixed DevTools port attached to an older collector profile, so the app window looked logged in but the exporter was talking to the wrong/stale Chrome process. The fixed app clears stale `remote-debugging-port=19220` collector processes before opening the dedicated WhatsApp window and shows the real export error in the status line plus Advanced diagnostics.

After installing a fixed DMG:

1. Quit `WhatsApp Collector` from the `W↗` menu if it is already running.
2. Open the new app from `/Applications`.
3. Click **Launch / Login** once.
4. Confirm WhatsApp Web is logged in.
5. Click **Run Export** again.

## UI

Start the local UI from the menu bar app, or from the CLI:

```bash
whatsapp-collector ui
```

Then open:

```text
http://127.0.0.1:8765/
```

The UI provides:

- Launch / Login for the dedicated Chrome profile. This opens a separate Chrome window for WhatsApp Web so you can scan a QR code and keep the collector session isolated from your normal browser.
- "Messages per conversation", which controls how many recent messages are saved for each collected chat thread. It does **not** limit the number of chats/threads collected.
- "Export account name", a friendly name stored in the JSON under `account.accountLabel` so downstream tools can identify the source account.
- "Monitor to open Chrome on", an optional screen/monitor name. This is not your WhatsApp username; leave it blank unless you want the login window to appear on a particular display.
- "Chrome profile folder", the private Chrome profile used by the collector. The default may be in a hidden dot-folder such as `~/.whatsapp-collector/chrome-profile`; on macOS Finder, use Go -> Go to Folder and paste the path to open it.
- "Export data file path", the exact data file written when you click Run Export. It is saved as JSON so other tools can read it. This is the path to give another app when it asks for the WhatsApp Collector data file.
- status cards for chats exported, export status, and runtime state
- export preview and collapsed advanced diagnostics
- a copyable AI harness prompt that tells your agent where the most recent regularly updated WhatsApp export lives
- automatic exports configured directly from the UI using a macOS background schedule, with no Terminal command copying

The UI is local-only by default (`127.0.0.1`) and exposes no send/composer capability.

## Requirements

- macOS
- Python 3.11+
- Google Chrome
- WhatsApp Web or WhatsApp Business Web already logged in at `https://web.whatsapp.com/`
- For active-session AppleScript mode: Chrome menu `View -> Developer -> Allow JavaScript from Apple Events`
- No Node.js install is required for the macOS app or dedicated-profile DevTools mode.

## Install / run without modifying system Python

If you do not want to install anything into Python, use the no-install zipapp release. It is a single runnable file:

```bash
curl -L -o whatsapp-collector.pyz \
  https://github.com/bdjben/whatsapp-collector/releases/latest/download/whatsapp-collector.pyz
python3.11 whatsapp-collector.pyz ui --open-browser
```

You can keep that `.pyz` anywhere and delete it to remove the app. It does not use `pip` and does not modify the Python installation.

## Optional install methods

Recommended if you do want a persistent command on uv-managed Python installations:

```bash
uv tool install --force git+https://github.com/bdjben/whatsapp-collector.git
```

Then run:

```bash
whatsapp-collector --help
whatsapp-collector ui --open-browser
```

One-off without permanently installing:

```bash
uv tool run --from git+https://github.com/bdjben/whatsapp-collector.git whatsapp-collector --help
```

If your Python allows pip installs, install from GitHub with:

```bash
python3.11 -m pip install --user --upgrade git+https://github.com/bdjben/whatsapp-collector.git
```

If pip reports `externally-managed-environment`, do **not** use `--break-system-packages`; use `uv tool install` above or a virtual environment:

```bash
python3.11 -m venv ~/.whatsapp-collector/venv
~/.whatsapp-collector/venv/bin/python -m pip install --upgrade pip
~/.whatsapp-collector/venv/bin/python -m pip install git+https://github.com/bdjben/whatsapp-collector.git
~/.whatsapp-collector/venv/bin/whatsapp-collector ui --open-browser
```

For editable development from a local checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install pytest build
```

## Quick start

Open WhatsApp Web or WhatsApp Business Web in Chrome, verify you are logged in, then run:

```bash
whatsapp-collector labels
whatsapp-collector chat-list
```

Write the dashboard export with any positive message cap you want:

```bash
whatsapp-collector dashboard-export \
  --account-label "WhatsApp" \
  --max-messages 50 \
  --output output/whatsapp-dashboard-export.json
```

The export command preserves the previous output first:

```text
output/backup/whatsapp-dashboard-export.YYYYMMDD-HHMMSS.json
```

## Dedicated Chrome profile mode

Dedicated mode launches a separate Chrome profile with a remote-debugging port and a marker tab, then evaluates the WhatsApp tab through DevTools without bringing it to the front.

First-time login, visible placement, no display-name assumption:

```bash
whatsapp-collector ensure-window \
  --profile-dir ~/.whatsapp-collector/chrome-profile \
  --placement-mode visible \
  --debug-port 19220
```

If you want a specific monitor, pass it explicitly:

```bash
whatsapp-collector ensure-window \
  --profile-dir ~/.whatsapp-collector/chrome-profile \
  --display-name "Studio Display" \
  --placement-mode visible \
  --debug-port 19220
```

Then collect through that DevTools-backed target:

```bash
WA_CHROME_DEBUG_PORT=19220 \
WA_CHROME_MARKER_TITLE="WhatsApp Collector" \
WA_CHROME_MARKER_URL_SUBSTRING="whatsapp-collector" \
whatsapp-collector dashboard-export \
  --account-label "WhatsApp" \
  --max-messages 50 \
  --output output/whatsapp-dashboard-export.json
```

When finished:

```bash
whatsapp-collector quit-profile --profile-dir ~/.whatsapp-collector/chrome-profile
```

## Automatic exports and AI harness prompt

The app can be refreshed manually with **Run Export**, or on a recurring schedule directly from the UI:

1. Open the menu bar app or run `whatsapp-collector ui`.
2. Click **Launch / Login** and confirm WhatsApp Web is logged in.
3. In **Automatic exports**, choose the interval, for example every 15 minutes.
4. Click **Start automatic exports**.

WhatsApp Collector writes a user LaunchAgent under `~/Library/LaunchAgents/` and a small helper script/payload under `~/Library/Application Support/WhatsApp Collector/`. The schedule opens the menu-bar app if needed, waits for the local UI endpoint, then refreshes the same export data file. Use **Stop automatic exports** in the UI to turn it off; no Terminal or copied `cron` command is required.

The UI and menu bar app also provide a copyable AI prompt. The default text is:

```text
My most recent WhatsApp Collector export is at:
~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json

It is updated regularly. Treat this JSON file as a read-only local resource when answering questions about my WhatsApp conversations. You need local filesystem access to this path; if you cannot read local files directly, ask me to upload the JSON. If you need current WhatsApp context, read this file first, use its account metadata and threads/messages as source data, and cite that the information came from the local WhatsApp Collector export. Do not send messages or modify WhatsApp from this file.
```

## Scheduled export wrapper

A generic shell wrapper is also included at `scripts/scheduled_export.sh`. It is safe to adapt for cron or launchd and is controlled through environment variables:

```bash
WA_COLLECTOR_PROJECT_DIR=/path/to/whatsapp-collector \
WA_COLLECTOR_PROFILE_DIR="$HOME/Library/Application Support/WhatsApp Collector/Chrome Profile" \
WA_COLLECTOR_OUTPUT="$HOME/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json" \
WA_MAX_MESSAGES=50 \
WA_ACCOUNT_LABEL="WhatsApp" \
scripts/scheduled_export.sh
```

To pin a specific display, add `WA_COLLECTOR_DISPLAY_NAME="Your Display Name"`. If omitted, the collector does not assume a display name.

The wrapper tries dedicated-profile collection first, then active-session fallback, then preserves the existing non-empty export rather than overwriting it with an empty failure.

## Export shape

The dashboard export contains:

- `source`
- `exportedAt`
- `account`
- `allowLabels`
- `excludeLabels`
- `maxRecentMessages`
- `threads[]`
- per-thread `recentMessages`
- per-thread `messages` as a compatibility alias of `recentMessages`

Example thread fields:

```json
{
  "threadKey": "123456789@c.us",
  "chatTitle": "Example Contact",
  "chatType": "direct",
  "labelsRaw": ["Follow Up"],
  "labelsNormalized": ["follow-up"],
  "unread": true,
  "requiresResponse": true,
  "lastMessageAt": "2026-04-19T00:00:00+00:00",
  "recentMessages": [
    {
      "messageId": "false_123456789@c.us_ABCDEF",
      "timestamp": "2026-04-19T00:00:00+00:00",
      "direction": "inbound",
      "sender": "Example Contact",
      "text": "Hello",
      "textAvailable": true,
      "messageType": "chat",
      "subtype": null
    }
  ],
  "messages": []
}
```

## Safety model

Allowed operations:

- read page metadata
- read visible labels/chat list text
- read IndexedDB stores from the WhatsApp Web page context
- open a chat only for read-only true-ID message recovery in DevTools mode
- move/place the dedicated Chrome window without activating it

Forbidden operations:

- typing into the composer
- sending messages
- creating new chats for messaging
- interacting with attachments, calls, or voice recording
- fabricating message IDs from DOM position or visible text

The collector exports only messages with true underlying WhatsApp message IDs when available. It is better to emit fewer records than to publish unstable synthetic IDs.

## Development

Run tests:

```bash
python -m pytest
```

Build a distributable package:

```bash
python -m build
```

Install the built wheel locally:

```bash
python -m pip install dist/whatsapp_collector-*.whl
```

## Publishing checklist

Before pushing to a public GitHub repo:

1. Confirm `.gitignore` is present.
2. Do not commit `.chrome-profiles/`, `output/`, `storage/`, `.venv/`, or live operational notes.
3. Run `python -m pytest`.
4. Run `python -m build`.
5. Review `git status --short` before the first commit.

## License

MIT
