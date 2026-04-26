#!/bin/bash
set -euo pipefail

# Example scheduled-export wrapper. Override these paths/env vars for your machine.
PROJECT_DIR="${WA_COLLECTOR_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUTPUT_PATH="${WA_COLLECTOR_OUTPUT:-$PROJECT_DIR/output/whatsapp-dashboard-export.json}"
TMP_DIR="${WA_COLLECTOR_TMP_DIR:-$PROJECT_DIR/output/.tmp}"
DEDICATED_CANDIDATE="$TMP_DIR/whatsapp-dashboard-export.dedicated.json"
ACTIVE_CANDIDATE="$TMP_DIR/whatsapp-dashboard-export.active.json"
PROFILE_DIR="${WA_COLLECTOR_PROFILE_DIR:-$HOME/.whatsapp-collector/chrome-profile}"
DISPLAY_NAME="${WA_COLLECTOR_DISPLAY_NAME:-}"
DEBUG_PORT="${WA_CHROME_DEBUG_PORT:-19220}"
MARKER_TITLE="${WA_CHROME_MARKER_TITLE:-WhatsApp Collector}"
MARKER_URL_SUBSTRING="${WA_CHROME_MARKER_URL_SUBSTRING:-whatsapp-collector}"
TARGET_URL="${WA_CHROME_TARGET_URL:-https://web.whatsapp.com/}"
ACCOUNT_LABEL="${WA_ACCOUNT_LABEL:-WhatsApp}"
MAX_MESSAGES="${WA_MAX_MESSAGES:-15}"

PYTHON_BIN="${PYTHON_BIN:-}"
RUNNER=(whatsapp-collector)
if ! command -v whatsapp-collector >/dev/null 2>&1; then
  if [[ -n "$PYTHON_BIN" ]]; then
    RUNNER=("$PYTHON_BIN" -m whatsapp_collector.cli)
  elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    RUNNER=("$PROJECT_DIR/.venv/bin/python" -m whatsapp_collector.cli)
  elif command -v python3 >/dev/null 2>&1; then
    RUNNER=(python3 -m whatsapp_collector.cli)
  else
    RUNNER=(python -m whatsapp_collector.cli)
  fi
fi

mkdir -p "$TMP_DIR"

cleanup() {
  "${RUNNER[@]}" quit-profile --profile-dir "$PROFILE_DIR" >/tmp/wa-collector-quit.json 2>/tmp/wa-collector-quit.err || true
}
trap cleanup EXIT

validate_export() {
  python3 - "$1" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.exists():
    print(0)
    raise SystemExit(0)
try:
    payload = json.loads(path.read_text())
except Exception:
    print(0)
    raise SystemExit(0)
threads = payload.get('threads', [])
print(len(threads) if isinstance(threads, list) else 0)
PY
}

backup_and_replace_output() {
  local candidate_path="$1"
  local output_path="$2"
  local output_dir backup_dir timestamp backup_path suffix=1

  output_dir="$(dirname "$output_path")"
  backup_dir="$output_dir/backup"

  if [[ -f "$output_path" ]]; then
    mkdir -p "$backup_dir"
    timestamp="$(date -u +"%Y%m%d-%H%M%S")"
    backup_path="$backup_dir/$(basename "${output_path%.*}").$timestamp.${output_path##*.}"
    while [[ -e "$backup_path" ]]; do
      backup_path="$backup_dir/$(basename "${output_path%.*}").$timestamp-$suffix.${output_path##*.}"
      suffix=$((suffix + 1))
    done
    cp "$output_path" "$backup_path"
  fi

  cp "$candidate_path" "$output_path"
}

current_count=$(validate_export "$OUTPUT_PATH")

ENSURE_WINDOW_ARGS=(
  ensure-window
  --profile-dir "$PROFILE_DIR"
  --placement-mode edge-hidden
  --settle-seconds 15
  --marker-title "$MARKER_TITLE"
  --marker-url-substring "$MARKER_URL_SUBSTRING"
  --target-url "$TARGET_URL"
  --debug-port "$DEBUG_PORT"
)
if [[ -n "$DISPLAY_NAME" ]]; then
  ENSURE_WINDOW_ARGS+=(--display-name "$DISPLAY_NAME")
fi
"${RUNNER[@]}" "${ENSURE_WINDOW_ARGS[@]}" >/tmp/wa-collector-window.json || true

set +e
WA_CHROME_DEBUG_PORT="$DEBUG_PORT" \
WA_CHROME_MARKER_TITLE="$MARKER_TITLE" \
WA_CHROME_MARKER_URL_SUBSTRING="$MARKER_URL_SUBSTRING" \
"${RUNNER[@]}" dashboard-export \
  --account-label "$ACCOUNT_LABEL" \
  --max-messages "$MAX_MESSAGES" \
  --output "$DEDICATED_CANDIDATE" >/tmp/wa-collector-dedicated-export.json 2>/tmp/wa-collector-dedicated-export.err
DEDICATED_STATUS=$?
set -e

if [[ $DEDICATED_STATUS -eq 0 ]]; then
  dedicated_count=$(validate_export "$DEDICATED_CANDIDATE")
else
  dedicated_count=0
fi

if [[ "$dedicated_count" -gt 0 ]]; then
  backup_and_replace_output "$DEDICATED_CANDIDATE" "$OUTPUT_PATH"
  printf '{"mode":"dedicated-profile","thread_count":%s,"output":"%s"}\n' "$dedicated_count" "$OUTPUT_PATH"
  exit 0
fi

set +e
"${RUNNER[@]}" dashboard-export \
  --account-label "$ACCOUNT_LABEL" \
  --max-messages "$MAX_MESSAGES" \
  --output "$ACTIVE_CANDIDATE" >/tmp/wa-collector-active-export.json 2>/tmp/wa-collector-active-export.err
ACTIVE_STATUS=$?
set -e

if [[ $ACTIVE_STATUS -eq 0 ]]; then
  active_count=$(validate_export "$ACTIVE_CANDIDATE")
else
  active_count=0
fi

if [[ "$active_count" -gt 0 ]]; then
  backup_and_replace_output "$ACTIVE_CANDIDATE" "$OUTPUT_PATH"
  printf '{"mode":"active-session-fallback","thread_count":%s,"output":"%s"}\n' "$active_count" "$OUTPUT_PATH"
  exit 0
fi

if [[ "$current_count" -gt 0 ]]; then
  printf '{"mode":"preserved-current","thread_count":%s,"output":"%s","dedicated_status":%s,"active_status":%s}\n' "$current_count" "$OUTPUT_PATH" "$DEDICATED_STATUS" "$ACTIVE_STATUS"
  exit 0
fi

echo '{"mode":"failed","reason":"no-usable-export-source"}'
exit 1
