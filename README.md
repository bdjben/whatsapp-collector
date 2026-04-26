# WhatsApp Collector

Read-only WhatsApp Web collector for macOS. It works with both WhatsApp Web and WhatsApp Business Web, turning an already logged-in Chrome session into structured JSON exports for dashboards, automations, and local reporting.

This project is deliberately not a bot and not a sender. It never types into WhatsApp, never targets the composer, never clicks send, and never creates outbound messages.

## Features

- Installable Python package with a `whatsapp-collector` CLI.
- Local polished web UI via `whatsapp-collector ui` for login/setup, export runs, settings, status, and export preview.
- Active Chrome session collection through AppleScript JavaScript from Apple Events.
- Optional dedicated Chrome profile collection through Chrome DevTools Protocol for exact no-focus targeting.
- No default display-name assumption: the dedicated window uses the first available macOS display unless `--display-name` is provided.
- Dedicated marker tab is named `WhatsApp Collector`.
- Label inventory and visible chat-list extraction.
- Labeled thread membership from WhatsApp Web IndexedDB.
- Configurable bounded recent-message windows through `--max-messages` or `WA_MAX_MESSAGES`.
- Stable export contract at `output/whatsapp-dashboard-export.json` by default.
- Atomic JSON writes with automatic backups before replacing an existing export.
- Runtime guardrails against send/composer JavaScript paths.

## UI

Start the local UI:

```bash
whatsapp-collector ui
```

Then open:

```text
http://127.0.0.1:8765/
```

The UI provides:

- Launch / Login for the dedicated Chrome profile
- optional display-name targeting without assuming a monitor name
- configurable max-message setting
- account label and output-path settings
- export run button
- status cards for thread count, freshness, and runtime state
- export preview and raw diagnostics

The UI is local-only by default (`127.0.0.1`) and exposes no send/composer capability.

## Requirements

- macOS
- Python 3.11+
- Google Chrome
- WhatsApp Web or WhatsApp Business Web already logged in at `https://web.whatsapp.com/`
- For active-session AppleScript mode: Chrome menu `View -> Developer -> Allow JavaScript from Apple Events`
- For dedicated-profile DevTools mode: Node.js 18+ available as `node`

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

## Scheduled export wrapper

A generic shell wrapper is included at `scripts/scheduled_export.sh`. It is safe to adapt for cron or launchd and is controlled through environment variables:

```bash
WA_COLLECTOR_PROJECT_DIR=/path/to/whatsapp-collector \
WA_COLLECTOR_PROFILE_DIR=$HOME/.whatsapp-collector/chrome-profile \
WA_COLLECTOR_OUTPUT=/path/to/output/whatsapp-dashboard-export.json \
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
