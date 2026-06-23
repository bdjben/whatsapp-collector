# WhatsApp Collector

![macOS](https://img.shields.io/badge/macOS-native%20SwiftUI-0A84FF)
![Read only](https://img.shields.io/badge/WhatsApp-read--only-25D366)
![Sparkle](https://img.shields.io/badge/updates-Sparkle-7C3AED)
![License](https://img.shields.io/badge/license-MIT-111827)

Read-only WhatsApp Web collector for macOS. It works with both WhatsApp Web and WhatsApp Business Web, turning an already logged-in Chrome session into structured JSON exports for dashboards, automations, and local reporting.

This project is deliberately not a bot and not a sender. It never types into WhatsApp, never targets the composer, never clicks send, and never creates outbound messages.

## What you get

| Surface | Purpose |
| --- | --- |
| Native macOS app | A proper SwiftUI window for login, export, labels, preview, automation, diagnostics, help, and updates. |
| Stable JSON export | `~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json`, ready for local AI agents. |
| Dedicated Chrome profile | Keeps the collector session isolated from normal browsing and targets WhatsApp Web through DevTools. |
| Label-aware collection | Reads WhatsApp labels from IndexedDB so selected labels can always include, never include, or follow standard recency behavior. |
| Safe automation | A user LaunchAgent can refresh exports on a schedule without a localhost web UI. |

## Quick start

```bash
curl -L -o WhatsApp-Collector-macOS.dmg \
  https://github.com/bdjben/whatsapp-collector/releases/latest/download/WhatsApp-Collector-macOS.dmg
open WhatsApp-Collector-macOS.dmg
```

Drag `WhatsApp Collector.app` to Applications, launch it, click **Launch / Login**, confirm WhatsApp Web is logged in, then click **Run Export**.

## Features

- Installable Python package with a `whatsapp-collector` CLI.
- Drag-to-Applications native Swift macOS app distributed as a signed DMG with the usual app-to-Applications install flow.
- Native macOS control window with a sidebar, dashboard, label rules, export preview, automation, diagnostics, help, and a menu bar extra.
- Optional local web UI via `whatsapp-collector ui` for CLI development and backwards-compatible operation.
- Active Chrome session collection through AppleScript JavaScript from Apple Events.
- Optional dedicated Chrome profile collection through Chrome DevTools Protocol for exact no-focus targeting.
- No default display-name assumption: the dedicated window uses the first available macOS display unless `--display-name` is provided.
- Dedicated marker tab is named `WhatsApp Collector`.
- Label inventory and visible chat-list extraction.
- Labeled thread membership from WhatsApp Web IndexedDB, including Standard / Always Include / Never Include label rules.
- Optional group filtering that keeps groups out unless they carry an Always Include label.
- Configurable bounded recent-message windows through `--max-messages` or `WA_MAX_MESSAGES`, plus configurable recent-chat coverage from WhatsApp Web's All view through `--max-all-chats`.
- Stable export contract at `~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json` by default for the UI/macOS app.
- Atomic JSON writes with automatic backups before replacing an existing export.
- First-launch detection for the older menu-bar/web UI app and legacy export-folder content, with permission-gated export backup and old-app removal when applicable.
- Runtime guardrails against send/composer JavaScript paths.

## Mac app

For non-developer installs, use the macOS DMG from the GitHub release:

```bash
curl -L -o WhatsApp-Collector-macOS.dmg \
  https://github.com/bdjben/whatsapp-collector/releases/latest/download/WhatsApp-Collector-macOS.dmg
open WhatsApp-Collector-macOS.dmg
```

A Finder window opens with `WhatsApp Collector.app` and an `Applications` shortcut. Drag the app onto `Applications`, then launch it from `/Applications` or Spotlight.

Release DMGs can be Developer ID signed, Apple-notarized, and stapled. Those notarized releases should open normally without the unidentified-developer first-launch warning. Local development builds fall back to ad-hoc signing unless a Developer ID identity is provided.

The app opens as a normal macOS window and also provides a compact menu bar extra. Use the window to:

- launch or focus the dedicated WhatsApp Web / WhatsApp Business Web Chrome profile
- run an export without opening a browser-based collector UI
- load the current WhatsApp label inventory and choose Standard / Always Include / Never Include rules
- preview the exported threads and recent messages in a native split view
- configure automatic exports through a macOS LaunchAgent
- check for future app updates through Sparkle
- inspect bridge diagnostics and reveal/copy the output path or AI harness prompt

The `W↗` menu bar extra provides quick access to the main window, Launch / Login, Run Export, Copy Prompt, Reveal Export, Check for Updates, and Quit.

The app writes exports to a normal visible folder by default:

```text
~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json
```

Deleting the app from `/Applications` removes the app itself. Your exported JSON files remain in `~/Documents/WhatsApp Collector/Exports` so you do not accidentally lose collected data.

If an older pre-native `WhatsApp Collector.app` is still installed in `/Applications`, the native app detects the old wrapper markers (`LSUIElement`, bundled `whatsapp-collector.pyz`, and generated menu source), asks for permission, backs up the export folder to `~/Documents/WhatsApp Collector/Backups/legacy-app-YYYYMMDD-HHMMSS/`, and moves the old app to Trash. It also detects existing content in the legacy export folder even when the old app bundle is already gone, and can back up that folder without removing any app. You can run this check from **Help -> Older App Cleanup**.

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

## Native App UI

The macOS app is the primary UI. It does not host the collector controls at `127.0.0.1:8765`; it runs short-lived native bridge commands and renders state directly in SwiftUI.

The app provides:

- **Dashboard**, with export freshness, schedule state, launch/login, run export, prompt, output/profile paths, group handling, and collection limits.
- **Labels**, where **Standard**, **Always Include**, and **Never Include** are explicit native choices for each WhatsApp label. Standard means the label does not force or block export; the chat is included only if it qualifies through normal export rules such as the Recent chats from All window. Loading labels reads WhatsApp Web's IndexedDB label store through the existing read-only DevTools path, so it does not depend on brittle visible-menu scraping.
- **Export Preview**, a searchable master-detail view over the same `threads[]` JSON that AI agents consume, sorted by latest message recency.
- **Automation**, which installs a user LaunchAgent that calls the native bridge directly. No localhost web server has to be running for scheduled exports.
- **Diagnostics**, with raw bridge responses for debugging Chrome, scheduling, and export failures.
- **Help**, with setup steps, label-rule explanations, AI-agent file paths, and an older-app cleanup action.

The stable output file remains:

```text
~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json
```

## Optional Web UI

The older local web UI remains available from the CLI:

```bash
whatsapp-collector ui
```

Then open:

```text
http://127.0.0.1:8765/
```

The web UI provides:

- Launch / Login for the dedicated Chrome profile. This opens a separate Chrome window for WhatsApp Web so you can scan a QR code and keep the collector session isolated from your normal browser. Keep that window open while exporting.
- "Messages per conversation", which controls how many recent messages are saved for each collected chat thread. It does **not** limit the number of chats/threads collected.
- "Recent chats from All view", which controls how many of the most recent chats visible in WhatsApp Web's All view are collected by the standard recency rule.
- "Groups", where Standard includes groups by normal recency rules, and the stricter option keeps groups out unless they have an Always Include label.
- "Label collection rules", where "Standard" follows normal recency/default behavior, "Always Include" forces matching chats into the export, and "Never Include" skips a chat only when every label on that chat is a Never Include label. The pre-populate button reads the currently available WhatsApp Web label list and turns it into selectable chips without sending or modifying messages.
- "Export account name", a friendly name stored in the JSON under `account.accountLabel` so downstream tools can identify the source account.
- "Monitor to open Chrome on", an optional screen/monitor name. This is not your WhatsApp username; leave it blank unless you want the login window to appear on a particular display.
- "Chrome profile folder", the private Chrome profile used by the collector. The default may be in a hidden dot-folder such as `~/.whatsapp-collector/chrome-profile`; on macOS Finder, use Go -> Go to Folder and paste the path to open it.
- "Export data file path", the exact data file written when you click Run Export. It is saved as JSON so other tools can read it. This is the path to give another app when it asks for the WhatsApp Collector data file.
- status cards for chats exported, export status, and runtime state
- export preview and collapsed advanced diagnostics
- a copyable AI harness prompt that tells your agent where the most recent regularly updated WhatsApp export lives
- automatic exports configured directly from the UI using a macOS background schedule, with no Terminal command copying

The web UI is local-only by default (`127.0.0.1`) and exposes no send/composer capability.

## Requirements

- macOS
- Python 3.11+
- Google Chrome
- WhatsApp Web or WhatsApp Business Web logged in inside the dedicated Chrome profile opened by **Launch / Login**
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

The app can be refreshed manually with **Run Export**, or on a recurring schedule directly from the native Automation screen:

1. Open `WhatsApp Collector.app`.
2. Click **Launch / Login** and confirm WhatsApp Web is logged in.
3. In **Automatic exports**, choose the interval, for example every 15 minutes.
4. Click **Start automatic exports**.

WhatsApp Collector writes a user LaunchAgent under `~/Library/LaunchAgents/` and a small helper script/payload under `~/Library/Application Support/WhatsApp Collector/`. The native app schedule calls the bundled `native_bridge.py` directly and refreshes the same export data file. Use **Stop** in Automation to turn it off; no Terminal, copied `cron` command, app UI process, or localhost web server is required.

The native app, web UI, and menu bar extra provide a copyable AI prompt. The default text is:

```text
My most recent WhatsApp Collector export is at:
~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json

It is updated regularly. Treat this JSON file as a read-only local resource when answering questions about my WhatsApp conversations. You need local filesystem access to this path; if you cannot read local files directly, ask me to upload the JSON. If you need current WhatsApp context, read this file first, use its account metadata and threads/messages as source data, and cite that the information came from the local WhatsApp Collector export. Do not send messages or modify WhatsApp from this file.
```

## App updates

The native macOS app embeds Sparkle 2 for update checks. The appcast feed URL is:

```text
https://github.com/bdjben/whatsapp-collector/releases/latest/download/appcast.xml
```

Future releases should upload `appcast.xml` plus the matching signed app archive to the GitHub release. The Sparkle EdDSA public key embedded in the app is:

```text
5rau7VI4KCvnHSD4dI1xXTSek9PijJJgOFgsRjcIb58=
```

The private signing key was generated in the macOS login keychain under the Sparkle account `studio.bdjben.whatsapp-collector`. Use Sparkle's `generate_appcast` tooling from the SwiftPM artifact to sign future update archives.

After placing the signed update archive, for example `WhatsApp-Collector-macOS.zip` or `WhatsApp-Collector-macOS.dmg`, in a release staging directory, generate the appcast with:

```bash
scripts/generate_sparkle_appcast.sh dist/sparkle-updates
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

Build the macOS app locally with the default ad-hoc signature:

```bash
python scripts/build_macos_app.py --output-dir dist
```

For a quick local run without creating a DMG:

```bash
./script/build_and_run.sh
```

Build a Developer ID signed, notarized, and stapled DMG when a `Developer ID Application` certificate is installed in the keychain and `notarytool` credentials are stored:

```bash
xcrun notarytool store-credentials whatsapp-collector-notary \
  --apple-id "you@example.com" \
  --team-id "TEAMID1234" \
  --password "app-specific-password"

python scripts/build_macos_app.py --output-dir dist \
  --sign-identity "Developer ID Application: Your Name (TEAMID1234)" \
  --notary-profile whatsapp-collector-notary \
  --notarize
```

The same values can be supplied as `WHATSAPP_COLLECTOR_CODESIGN_IDENTITY` and `WHATSAPP_COLLECTOR_NOTARY_PROFILE`.

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
