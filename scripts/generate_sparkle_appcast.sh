#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVES_DIR="${1:-$ROOT_DIR/dist/sparkle-updates}"
SPARKLE_ACCOUNT="${SPARKLE_ACCOUNT:-studio.bdjben.whatsapp-collector}"
DOWNLOAD_URL_PREFIX="${SPARKLE_DOWNLOAD_URL_PREFIX:-https://github.com/bdjben/whatsapp-collector/releases/latest/download/}"

mkdir -p "$ARCHIVES_DIR"
swift build --package-path "$ROOT_DIR/native-macos" >/dev/null

TOOL="$ROOT_DIR/native-macos/.build/artifacts/sparkle/Sparkle/bin/generate_appcast"
if [ ! -x "$TOOL" ]; then
  echo "Sparkle generate_appcast tool not found at $TOOL" >&2
  exit 1
fi

"$TOOL" \
  --account "$SPARKLE_ACCOUNT" \
  --download-url-prefix "$DOWNLOAD_URL_PREFIX" \
  "$ARCHIVES_DIR"
