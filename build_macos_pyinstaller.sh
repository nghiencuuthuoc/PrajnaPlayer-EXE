#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
VENV_DIR=".venv_build_macos"
ENTRY=""
APP_NAME=""
PNG_PATH=""
ICO_PATH=""
ICNS_PATH=""
VLC_EXTRA=()
echo "==============================================="
echo "PrajnaPlayer macOS Build Kit"
echo "==============================================="
echo "1. PrajnaPlayer_v19_dualsub_color_speed.py"
echo "2. PrajnaPlayer_Dual_Subtitle_v3_state_resume.py"
read -r -p "Choose app [1/2] : " CHOICE
if [[ "$CHOICE" == "2" ]]; then
  ENTRY="PrajnaPlayer_Dual_Subtitle_v3_state_resume.py"
  APP_NAME="PrajnaPlayer_Dual_Subtitle_v3"
else
  ENTRY="PrajnaPlayer_v19_dualsub_color_speed.py"
  APP_NAME="PrajnaPlayer_v19"
fi
find_icon_png() {
  local arr=("../nct_logo.png" "./nct_logo.png" "../../nct_logo.png" "../../../nct_logo.png" "../../../../nct_logo.png")
  for p in "${arr[@]}"; do
    if [[ -f "$p" ]]; then
      python3 - <<PY
from pathlib import Path
print(Path(r"$p").resolve())
PY
      return 0
    fi
  done
  return 1
}
find_icon_ico() {
  local arr=("../nct_logo.ico" "./nct_logo.ico" "../../nct_logo.ico" "../../../nct_logo.ico" "../../../../nct_logo.ico")
  for p in "${arr[@]}"; do
    if [[ -f "$p" ]]; then
      python3 - <<PY
from pathlib import Path
print(Path(r"$p").resolve())
PY
      return 0
    fi
  done
  return 1
}
PNG_PATH="$(find_icon_png || true)"
ICO_PATH="$(find_icon_ico || true)"
make_icns_from_png() {
  local png="$1"
  local iconset_dir="build_icon.iconset"
  local icns_out="build_icon.icns"
  rm -rf "$iconset_dir" "$icns_out"
  mkdir -p "$iconset_dir"
  sips -z 16 16 "$png" --out "$iconset_dir/icon_16x16.png" >/dev/null
  sips -z 32 32 "$png" --out "$iconset_dir/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "$png" --out "$iconset_dir/icon_32x32.png" >/dev/null
  sips -z 64 64 "$png" --out "$iconset_dir/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "$png" --out "$iconset_dir/icon_128x128.png" >/dev/null
  sips -z 256 256 "$png" --out "$iconset_dir/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "$png" --out "$iconset_dir/icon_256x256.png" >/dev/null
  sips -z 512 512 "$png" --out "$iconset_dir/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "$png" --out "$iconset_dir/icon_512x512.png" >/dev/null
  cp "$png" "$iconset_dir/icon_512x512@2x.png"
  iconutil -c icns "$iconset_dir" -o "$icns_out"
  rm -rf "$iconset_dir"
  echo "$PWD/$icns_out"
}
if [[ -n "$PNG_PATH" ]] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
  ICNS_PATH="$(make_icns_from_png "$PNG_PATH")"
fi
if [[ -f "/Applications/VLC.app/Contents/MacOS/lib/libvlc.dylib" ]]; then VLC_EXTRA+=(--add-binary "/Applications/VLC.app/Contents/MacOS/lib/libvlc.dylib:."); fi
if [[ -f "/Applications/VLC.app/Contents/MacOS/lib/libvlccore.dylib" ]]; then VLC_EXTRA+=(--add-binary "/Applications/VLC.app/Contents/MacOS/lib/libvlccore.dylib:."); fi
if [[ -d "/Applications/VLC.app/Contents/MacOS/plugins" ]]; then VLC_EXTRA+=(--add-data "/Applications/VLC.app/Contents/MacOS/plugins:plugins"); fi
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt
CMD=(pyinstaller --noconfirm --clean --windowed --name "$APP_NAME")
if [[ -n "$ICNS_PATH" && -f "$ICNS_PATH" ]]; then CMD+=(--icon "$ICNS_PATH"); fi
if [[ -n "$PNG_PATH" && -f "$PNG_PATH" ]]; then CMD+=(--add-data "$PNG_PATH:."); fi
if [[ -n "$ICO_PATH" && -f "$ICO_PATH" ]]; then CMD+=(--add-data "$ICO_PATH:."); fi
CMD+=("${VLC_EXTRA[@]}")
CMD+=("$ENTRY")
"${CMD[@]}"
if [[ -d "dist/$APP_NAME.app" ]]; then
  open "dist/$APP_NAME.app"
  sleep 5
  osascript -e "tell application \"$APP_NAME\" to quit" >/dev/null 2>&1 || true
  pkill -f "$APP_NAME.app" >/dev/null 2>&1 || true
fi
read -r -p "Delete build helper data (venv, build, spec, temp icns)? [y/N] : " DO_CLEAN
if [[ "${DO_CLEAN,,}" == "y" || "${DO_CLEAN,,}" == "yes" ]]; then
  rm -rf "$VENV_DIR" build __pycache__ *.spec build_icon.icns build_icon.iconset
  find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
fi
