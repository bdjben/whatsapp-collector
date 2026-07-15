# WhatsApp Collector

![macOS](https://img.shields.io/badge/macOS-native%20SwiftUI-0A84FF)
![Read only](https://img.shields.io/badge/WhatsApp-read--only-25D366)
![Updates](https://img.shields.io/badge/updates-Sparkle-7C3AED)
![License](https://img.shields.io/badge/license-MIT-111827)

WhatsApp Collector is a native macOS app that works with both WhatsApp Web and
WhatsApp Business Web. It reads your logged-in browser session and writes a
structured JSON export for local dashboards, automations, and AI agents.

It is deliberately non-sending. It does not type into WhatsApp, target the
composer, create chats, place calls, upload files, or send messages. When you
enable attachment downloads, it may use WhatsApp Web's received-media cache and
Download controls to retain files that belong to exported messages.

<p>
  <img width="203" height="157" alt="WhatsApp Collector menu bar item" src="https://github.com/user-attachments/assets/e59c096b-4710-4d8c-8f52-ac03336a40a2" />
  <img width="640" alt="WhatsApp Collector native dashboard" src="https://github.com/user-attachments/assets/d3ce3783-b4d7-4d3a-83d5-a50b1d9aa036" />
</p>

## Install

Most users should install the signed macOS DMG from the
[latest GitHub release](https://github.com/bdjben/whatsapp-collector/releases/latest).

1. Open the [Releases page](https://github.com/bdjben/whatsapp-collector/releases/latest).
2. Download `WhatsApp-Collector-macOS.dmg`.
3. Open the DMG.
4. Drag `WhatsApp Collector.app` into `Applications`.
5. Launch it from `Applications` or Spotlight.

Terminal equivalent:

```bash
curl -L -o WhatsApp-Collector-macOS.dmg \
  https://github.com/bdjben/whatsapp-collector/releases/latest/download/WhatsApp-Collector-macOS.dmg
open WhatsApp-Collector-macOS.dmg
```

Release DMGs are Developer ID signed, Apple notarized, and stapled. They should
open normally on macOS without the unidentified-developer warning.

## Requirements

- macOS 14 or newer.
- Google Chrome installed in `/Applications` or otherwise discoverable by macOS.
- A WhatsApp or WhatsApp Business account that can log in at `https://web.whatsapp.com/`.

You do not need to create a Chrome profile yourself. The app opens its own
dedicated Chrome profile for collection. You also do not need to turn on Chrome
developer settings for the normal native app workflow.

## First Run

1. Open `WhatsApp Collector.app`.
2. Click **Launch / Login**.
3. The app opens a dedicated Chrome window at WhatsApp Web.
4. If WhatsApp asks for a QR code, scan it from your phone.
5. Return to WhatsApp Collector and click **Run Export**.
6. Open **Export Preview** to inspect what was captured.

After the first login, **Run Export** and scheduled exports automatically open
or reuse this same dedicated profile. You do not need to press **Launch / Login**
before every collection.

The default export file is:

```text
~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json
```

That file is the main output. Give that path to local AI agents, scripts, or
dashboards that should read your WhatsApp context.

## What The App Does

| Part | What it means |
| --- | --- |
| Native macOS dashboard | Configure collection, run exports, preview results, and see status without a browser-based UI. |
| Dedicated Chrome profile | Keeps the collector's WhatsApp login separate from your normal Chrome browsing. |
| JSON export | Writes a stable local file that other tools can read. |
| Verified attachments | Optionally saves received media beside the JSON, with hashes, paths, extraction method, and clear failure reasons. |
| Label rules | Optionally include or exclude chats based on WhatsApp labels. |
| Scheduled exports | Refreshes the same JSON file on a recurring macOS LaunchAgent schedule. |
| Sparkle updates | Checks GitHub releases and shows when a newer app version is available. |

## What It Does Not Do

- It does not send WhatsApp messages.
- It does not automate replies.
- It does not scrape or export chats from your phone directly.
- It does not read a private WhatsApp server database.
- It does not require a local web server for the native app workflow.
- It does not upload your export anywhere.

The collector reads data available inside your logged-in WhatsApp Web session
and saves the result locally on your Mac.

## Main App Screens

### Dashboard

Use **Dashboard** for the normal workflow:

- launch or focus the dedicated Chrome profile
- run an export now
- see export freshness and schedule status
- set collection limits
- choose the Chrome profile folder and export file path
- copy the AI-agent prompt or reveal the output file in Finder
- enable verified attachment downloads and set a total storage cap

### Labels

Label rules are optional. If you do not use WhatsApp labels, you can ignore this
screen.

Each label can be set to:

| Rule | Meaning |
| --- | --- |
| Standard | The label does not force or block export. Chats are included only when they match normal collection rules, such as being in the recent chats window. |
| Always Include | Chats with this label are included even if they are not among the most recent chats. |
| Never Include | Chats with this label are skipped when the chat is otherwise only selected by excluded labels. |

Click **Load Labels** to read the current WhatsApp label list from WhatsApp Web.
This is read-only and does not create, edit, or delete labels.

### Export Preview

Use **Export Preview** to inspect the latest JSON export without opening the file
manually. It is useful for checking:

- which chats were exported
- each chat's most recent messages
- unread/requires-response flags
- labels and chat type
- skipped or incomplete collection details
- downloaded attachment paths, verification state, and extraction method

### Automation

Use **Automation** to run exports on a schedule. The app installs a user
LaunchAgent under `~/Library/LaunchAgents/` and calls the bundled native bridge
directly. The app window and old localhost web UI do not need to stay open.

Changing schedule settings takes effect after you click **Save Changes**.

### Diagnostics And Help

Use **Diagnostics** when Chrome, WhatsApp Web, scheduling, or export quality
needs debugging. Use **Help** for setup notes, update links, and older-app
cleanup.

## Important Settings

| Setting | Plain-English explanation |
| --- | --- |
| Messages per conversation | How many recent messages to export for each selected chat. |
| Recent chats from All | How many of the most recent chats in WhatsApp Web's All view should be considered by the standard recency rule. |
| Groups | Choose whether normal group chats can be exported, or whether groups must have an Always Include label. |
| Label rules | Optional rules that can force specific labeled chats in or keep labeled chats out. |
| Chrome window display | Optional. Use this only if you want the dedicated Chrome window placed on a particular monitor. Enter the display name as it appears in macOS System Settings > Displays. Leave blank to use the main display. |
| Chrome profile folder | The isolated Chrome profile used by the collector. Most users should leave this alone. |
| Export data file | The JSON file written by **Run Export** and scheduled exports. |
| Download attachments automatically | Optional. Saves media attached to exported messages after validating WhatsApp's metadata and file hash. |
| Total attachment storage limit | Maximum retained attachment storage. Each file is also limited to 50 MB and each thread to 150 MB. Existing files count toward the limit. |

## Using The Export With AI Agents

The app includes a **Copy Prompt** action. It produces text like this:

```text
My most recent WhatsApp Collector export is at:
~/Documents/WhatsApp Collector/Exports/whatsapp-dashboard-export.json

Treat this JSON as a read-only local resource and read it fresh before answering questions about my WhatsApp conversations. Inspect the attachments array on every relevant message. When status is downloaded, resolve localPath or relativePath, open and analyze the actual file, and combine its content with the parent message. When a file is unavailable, say so and do not claim to have analyzed it. Do not send messages or modify WhatsApp.
```

Use that prompt in local agents that can read files from your Mac.
The prompt used through `0.4.14` is preserved in
[PROMPT_HISTORY.md](PROMPT_HISTORY.md) for reference.

## Export Shape

The dashboard export is JSON. Top-level fields include:

- `source`
- `exportedAt`
- `account`
- `allowLabels`
- `excludeLabels`
- `maxRecentMessages`
- `attachmentsRoot`
- `attachmentPolicy`
- `attachmentSummary`
- `threads[]`

Each thread includes metadata plus recent messages:

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
      "subtype": null,
      "attachments": [
        {
          "attachmentId": "att_0123456789abcdef",
          "kind": "document",
          "mimeType": "application/pdf",
          "fileName": "example.pdf",
          "sizeBytes": 60646,
          "status": "downloaded",
          "relativePath": "Attachments/.../example.pdf",
          "localPath": "/Users/.../Attachments/.../example.pdf",
          "sha256": "e94b...",
          "downloadMethod": "whatsapp-media-cache",
          "verified": true,
          "downloadAttempts": 0,
          "sourceMessageId": "false_123456789@c.us_ABCDEF"
        }
      ]
    }
  ],
  "messages": []
}
```

`messages` is kept as a compatibility alias for older consumers. New consumers
should read `recentMessages`.

WhatsApp albums remain one parent message in `recentMessages`; their complete
set of child images/videos appears in that message's `attachments` array. Each
child keeps its own `sourceMessageId`, WhatsApp hash, size, and verification
result, even when WhatsApp renders only some album tiles on screen.

## Troubleshooting

### Chrome is missing

Install Google Chrome, then click **Launch / Login** again. WhatsApp Collector
does not require Chrome developer settings for its normal dedicated-profile
workflow.

### WhatsApp asks for login again

Scan the QR code in the dedicated Chrome window. The login is stored in the
collector's private Chrome profile, not your normal Chrome profile.

### The Chrome window opens on the wrong monitor

Leave **Chrome window display** blank unless you specifically need a certain
monitor. If you do use it, enter the display name exactly as macOS shows it in
System Settings > Displays, then click **Save Changes**.

### Export fails

Open **Diagnostics** and check the latest bridge response. Common causes are:

- Chrome is not installed.
- WhatsApp Web is not logged in inside the dedicated profile.
- The dedicated Chrome profile was closed while an export was already running.
- WhatsApp Web changed its internal data shape and the collector needs an update.

Starting an export while Chrome is closed is supported: the app automatically
opens the configured dedicated profile and verifies its exact process before it
collects anything.

### Scheduled exports are not running

Open **Automation** and check whether automatic exports are enabled. After
changing schedule or collection settings, click **Save Changes** so the
LaunchAgent receives the updated payload.

Each scheduled run records the PID, dedicated profile path, and DevTools port
of the Chrome process it uses. That exact process is closed immediately after a
successful run. After a failed run it remains available for five minutes for
inspection, then closes automatically. Chrome processes that do not match the
collector profile, port, and captured PID are left alone.

### The export has fewer messages than expected

Increase **Messages per conversation** and run another export. Also confirm the
chat is included by your recency, group, and label-rule settings.

### An attachment was not downloaded

Inspect `skippedReason` and `note` in that attachment's JSON entry. Downloads
may be disabled, a 50 MB file / 150 MB thread / configured total limit may have
been reached, or WhatsApp Web may not have made verified bytes available after
the automatic retry. The message remains in the export as an attachment
placeholder rather than being silently discarded.

### Older app cleanup

If an older pre-native `WhatsApp Collector.app` or legacy export folder is found,
the native app can back up old exports and move the old app to Trash with your
permission. Open **Help > Older App Cleanup**.

## Safety Model

Allowed operations:

- read page metadata
- read visible labels and chat-list text
- read WhatsApp Web IndexedDB stores from the page context
- read decrypted received-media bytes from WhatsApp Web's local media cache
- open chats only for read-only message recovery in DevTools mode
- request an inbound media download and use the exact message's Download action
- move or place the dedicated Chrome window

Forbidden operations:

- typing into the message composer
- sending messages
- creating outbound chats
- uploading, forwarding, deleting, or sending attachments
- interacting with calls or voice recording
- fabricating message IDs from DOM position or visible text

The collector prefers to export fewer trustworthy records rather than publish
unstable synthetic message IDs.

## Advanced: CLI And Zipapp

The native macOS app is the recommended install. Advanced users can also use the
Python CLI.

No-install zipapp:

```bash
curl -L -o whatsapp-collector.pyz \
  https://github.com/bdjben/whatsapp-collector/releases/latest/download/whatsapp-collector.pyz
python3.11 whatsapp-collector.pyz --help
```

Install as a `uv` tool:

```bash
uv tool install --force git+https://github.com/bdjben/whatsapp-collector.git
whatsapp-collector --help
```

Optional legacy/development web UI:

```bash
whatsapp-collector ui
```

Then open:

```text
http://127.0.0.1:8765/
```

The web UI is local-only and remains mostly for CLI development and backwards
compatibility. New users should use the native app.

## Development

Set up a local checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install pytest build
```

Run tests:

```bash
python -m pytest
```

Build Python package artifacts:

```bash
python -m build
```

Build the macOS app locally with an ad-hoc signature:

```bash
python scripts/build_macos_app.py --output-dir dist
```

Run a local app build:

```bash
./script/build_and_run.sh
```

Build a Developer ID signed, notarized, and stapled DMG when a Developer ID
certificate and notarytool profile are available:

```bash
python scripts/build_macos_app.py --output-dir dist \
  --sign-identity "Developer ID Application: Your Name (TEAMID1234)" \
  --notary-profile whatsapp-collector-notary \
  --notarize
```

## App Updates

The native app uses Sparkle 2. The appcast feed is:

```text
https://github.com/bdjben/whatsapp-collector/releases/latest/download/appcast.xml
```

Release maintainers should upload `appcast.xml` plus the matching signed app
archive to each GitHub release.

## Publishing Checklist

Before pushing to the public repo:

1. Confirm `.gitignore` is present.
2. Do not commit `.chrome-profiles/`, `output/`, `storage/`, `.venv/`, or live operational notes.
3. Run `python -m pytest`.
4. Run `python -m build`.
5. Review `git status --short`.

## License

MIT
