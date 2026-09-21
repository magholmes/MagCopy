#!/usr/bin/env bash
# Install MagCopy on macOS:
#
#   curl -fsSL https://raw.githubusercontent.com/magholmes/MagCopy/main/install-macos.sh | bash
#
# This exists because of one specific piece of friction. A .app downloaded in a browser is marked
# quarantined, and macOS refuses to open a quarantined app that is not notarised - outright, with
# right-click -> Open as the only way through. Nothing downloaded by this script is quarantined,
# because the mark is applied by the browser rather than by the file, so the app just opens.
#
# It writes one thing, /Applications/MagCopy.app, and needs no administrator rights.
set -euo pipefail

REPO="magholmes/MagCopy"
URL="https://github.com/$REPO/releases/latest/download/MagCopy-macos.zip"
DEST="/Applications/MagCopy.app"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This installs the macOS build; on Windows use the .zip from the releases page." >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "The published build is Apple Silicon only. On an Intel Mac, clone the repository and run" >&2
  echo "./build_mac.sh - the encoders it fetches are chosen for the machine it runs on." >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Downloading MagCopy..."
curl -fL --progress-bar "$URL" -o "$tmp/MagCopy.zip"

echo "Installing to $DEST..."
ditto -x -k "$tmp/MagCopy.zip" "$tmp/unpacked"
[ -d "$tmp/unpacked/MagCopy.app" ] || { echo "the download did not contain MagCopy.app" >&2; exit 1; }

# Quit a copy that is already running, or the replace below writes under a live process
pkill -f "MagCopy.app/Contents/MacOS/MagCopy" 2>/dev/null || true
sleep 1
rm -rf "$DEST"
ditto "$tmp/unpacked/MagCopy.app" "$DEST"
# Belt and braces: if this script was itself downloaded in a browser and run from Finder, the
# unpacked app can inherit the mark. Clearing it is the whole point of installing this way.
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

open "$DEST"

cat <<'NOTE'

MagCopy is installed and running. It lives in the menu bar - there is no Dock icon.

  ctrl+shift+a   drag a region; the screenshot goes straight to the clipboard
  ctrl+shift+s   drag a region and record it; escape stops and opens the editor

macOS will ask for Screen Recording the first time. It is the only permission it
needs, and without it captures come out blank.

NOTE
