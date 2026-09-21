#!/usr/bin/env bash
# Build MagCopy.app. Needs: python -m pip install pyinstaller
#
#   ./build_mac.sh            dist/MagCopy.app  plus  dist/MagCopy-macos.zip
#   ./build_mac.sh --no-zip   just the .app
#
# The result is ad-hoc signed. That is not the same as being notarised - see "Gatekeeper" in the
# readme - but it is what makes the two permission grants stick between builds, so it is done
# every time rather than only for a release.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="${PYTHON:-python3}"
[ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"

if [ ! -x "$ROOT/bin/ffmpeg" ]; then
  echo "bin/ffmpeg is missing - run: $PY tools/fetch_binaries.py" >&2
  exit 1
fi
if [ ! -f "$ROOT/icon.icns" ]; then
  echo "icon.icns is missing - run: $PY tools/make_icon.py icon_source.png --icns" >&2
  exit 1
fi

ARCH="$(uname -m)"
echo "building for $ARCH with $("$PY" -V)"
rm -rf build dist

# --add-binary rather than --add-data for bin/: PyInstaller then keeps the executable bit, and a
# bundled encoder that unpacks without one fails as "Permission denied" from a file plainly there.
"$PY" -m PyInstaller --noconfirm --clean --windowed \
  --name MagCopy \
  --icon "$ROOT/icon.icns" \
  --osx-bundle-identifier com.magholmes.magcopy \
  --add-data "$ROOT/fonts:fonts" \
  --add-binary "$ROOT/bin/ffmpeg:bin" \
  --add-binary "$ROOT/bin/gifski:bin" \
  --add-binary "$ROOT/bin/gifsicle:bin" \
  --add-data "$ROOT/menubar.png:." \
  --add-data "$ROOT/icon.icns:." \
  --hidden-import PIL._tkinter_finder \
  --collect-submodules objc \
  --collect-submodules Foundation \
  --collect-submodules AppKit \
  --collect-submodules Quartz \
  --collect-submodules CoreMedia \
  --collect-submodules ScreenCaptureKit \
  "$ROOT/magcopy.pyw"

APP="$ROOT/dist/MagCopy.app"
PLIST="$APP/Contents/Info.plist"

# PyInstaller writes 0.0.0 unless told otherwise, and that is the number Finder shows in Get Info
# and the one a crash report carries. Take it from the app itself so there is only one to change.
VERSION="$("$PY" -c 'import sys; sys.path.insert(0, "'"$ROOT"'"); from magcopy.settings import APP_VERSION; print(APP_VERSION)')"
echo "version $VERSION"

# LSUIElement: no Dock icon and no entry in the app switcher. MagCopy is a menu bar app that
# spends most of its life with no window open, and a Dock icon for it would be a lie.
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName MagCopy" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string MagCopy" "$PLIST"
# Named in the Screen Recording prompt, so the reason is there when the user is deciding.
/usr/libexec/PlistBuddy -c "Add :NSScreenCaptureUsageDescription string MagCopy captures the region of the screen you drag a box around." "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 12.3" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.3" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.utilities" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$PLIST"

chmod +x "$APP/Contents/Resources/bin/"* 2>/dev/null || true
chmod +x "$APP/Contents/Frameworks/bin/"* 2>/dev/null || true

# Sign last, and sign deep. macOS keys the Screen Recording grant to the bundle's signature, so an
# unsigned build re-prompts on every rebuild and sometimes remembers a stale entry instead.
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict "$APP" && echo "signature: ad-hoc, verified"

echo
echo "built $APP  ($(du -sh "$APP" | cut -f1))"
"$APP/Contents/MacOS/MagCopy" --selftest --quiet || true

if [ "${1:-}" != "--no-zip" ]; then
  ZIP="$ROOT/dist/MagCopy-macos.zip"
  rm -f "$ZIP"
  # ditto rather than zip: it keeps the symlinks and resource forks inside a .app intact, which a
  # plain zip flattens into something that will not launch.
  ( cd "$ROOT/dist" && ditto -c -k --sequesterRsrc --keepParent MagCopy.app "$ZIP" )
  echo "built $ZIP  ($(du -sh "$ZIP" | cut -f1))"

  # A .dmg as well, because it is the thing a Mac user expects to download: one file, open it,
  # drag the app onto the Applications shortcut sitting next to it. The zip stays for the install
  # script, which wants something it can unpack without mounting anything.
  DMG="$ROOT/dist/MagCopy-macos.dmg"
  STAGE="$ROOT/build/dmg"
  rm -rf "$STAGE" "$DMG"
  mkdir -p "$STAGE"
  ditto "$APP" "$STAGE/MagCopy.app"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "MagCopy" -srcfolder "$STAGE" -ov -format UDZO -quiet "$DMG"
  rm -rf "$STAGE"
  echo "built $DMG  ($(du -sh "$DMG" | cut -f1))"
fi
