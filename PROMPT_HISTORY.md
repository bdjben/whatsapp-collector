# AI Prompt History

## Through 0.4.14

The app filled in the configured export and attachment paths at runtime.

```text
My most recent WhatsApp Collector export is at:
<EXPORT_PATH>

It is updated regularly. Treat this JSON file as a read-only local resource when answering questions about my WhatsApp conversations. You need local filesystem access to this path; if you cannot read local files directly, ask me to upload the JSON. If you need current WhatsApp context, read this file first, use its account metadata and threads/messages as source data, and cite that the information came from the local WhatsApp Collector export. Do not send messages or modify WhatsApp from this file.

Some messages may include an attachments array. When an attachment has status=downloaded, open the referenced localPath, or resolve relativePath from the export folder; attachments are stored under <ATTACHMENTS_ROOT>. When an attachment has status=notDownloaded, treat it as a real media/document placeholder attached to that message, not as a new message. If skippedReason is video-over-10mb, tell me that WhatsApp has a video for that message but it was not downloaded automatically because it is over 10 MB.
```

Starting in 0.4.15, the prompt explicitly instructs an AI agent to inspect every
relevant attachment, resolve its local file safely, analyze the actual media or
document, combine it with its parent message, and disclose unavailable files.
