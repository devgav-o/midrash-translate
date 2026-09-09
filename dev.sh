#!/usr/bin/env bash
# Local development runner (macOS/Linux, no Word required).
#
#   ./dev.sh              watch demo_transcript.txt with the echo backend
#   ./dev.sh notes.txt    watch your own file
#
# Type into the file and save; the browser pages update. This exercises the
# whole pipeline -- diffing, debounce, websockets, projector pages, export --
# without Word, Windows, or any models downloaded.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtualenv (system pip is blocked by Homebrew; a venv is the fix)..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q fastapi "uvicorn[standard]" pyyaml python-docx requests
fi

SRC="${1:-demo_transcript.txt}"
[ -f "$SRC" ] || : > "$SRC"

exec .venv/bin/python run.py --file "$SRC" --backend "${BACKEND:-echo}" "${@:2}"
