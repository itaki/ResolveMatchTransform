#!/bin/bash
# ResolveMatchTransform — installer / dependency bootstrap.
#
# Installs PyQt6, opencv-python and numpy into the system python3 that Resolve
# will spawn at runtime, ensures ffmpeg is present, and copies the app into
# Application Support so it works independently of this checkout. Only the
# tiny Resolve menu launcher lives in Resolve's script-scanned Utility folder.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
APP_DIR="$HOME/Library/Application Support/ResolveMatchTransform"
LINK_NAME="ResolveMatchTransform.py"

PY="${PYTHON:-$(command -v python3 || true)}"
if [ -z "$PY" ]; then
  echo "python3 not found on PATH. Install via Homebrew: brew install python" >&2
  exit 1
fi

# Resolve symlinks/shims so .python_path always points at the real interpreter
# (pyenv shims rely on shell env that Resolve's spawn doesn't inherit).
PY_REAL="$("$PY" -c 'import sys; print(sys.executable)')"
echo "Using python: $PY  (real: $PY_REAL)"
"$PY_REAL" -m pip install --upgrade opencv-python numpy PyQt6

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "ffmpeg not found — installing via Homebrew…"
    brew install ffmpeg
  else
    echo "ffmpeg not found and Homebrew is not installed." >&2
    echo "Install ffmpeg manually, then re-run this script." >&2
    exit 1
  fi
fi

mkdir -p "$SCRIPTS_DIR"
rm -rf "$SCRIPTS_DIR/ResolveMatchTransform"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp "$HERE/match_frame.py" "$APP_DIR/"
rsync -a --exclude '__pycache__' "$HERE/core" "$APP_DIR/"
rsync -a --exclude '__pycache__' "$HERE/ui" "$APP_DIR/"
if [ -f "$HERE/CLAUDE.md" ]; then
  cp "$HERE/CLAUDE.md" "$APP_DIR/"
fi

# Pin this python inside the installed app so Resolve can spawn the same
# interpreter from its minimal Finder/Dock environment.
echo "$PY_REAL" > "$APP_DIR/.python_path"
echo "Installed app -> $APP_DIR"
echo "Pinned interpreter -> $APP_DIR/.python_path"

rm -f "$SCRIPTS_DIR/$LINK_NAME"
cat > "$SCRIPTS_DIR/$LINK_NAME" <<EOF
import runpy

runpy.run_path("$APP_DIR/match_frame.py", run_name="__main__")
EOF
echo "Installed menu script -> $SCRIPTS_DIR/$LINK_NAME"

cat <<EOF

Done.

In DaVinci Resolve:
  Preferences → System → General → External scripting using: Local
  Workspace → Scripts → ResolveMatchTransform

The panel opens as a floating window outside Resolve.
EOF
