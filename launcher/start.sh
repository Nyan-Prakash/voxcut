#!/usr/bin/env bash
# VOXCUT launcher (macOS/Linux). Double-clickable via "Start VOXCUT.command".
# Bootstraps uv, syncs deps, starts the server, opens the browser (§15).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE/backend"

# 1. Ensure uv (standalone binary, no system Python needed).
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

# 2. Sync deps into a pinned Python 3.12 venv (first run only; cached after).
echo "Preparing environment…"
uv sync --python 3.12 --quiet

# 3. Compute the URL with the security token, then start + open.
PORT="${VOXCUT_PORT:-8484}"
URL="http://127.0.0.1:${PORT}/"
( sleep 2
  TOKEN="$(uv run --quiet python -c 'from voxcut.config import settings; print(settings().session_token)')"
  FULL="${URL}?t=${TOKEN}"
  echo "Opening $FULL"
  if command -v open >/dev/null 2>&1; then open "$FULL"; \
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$FULL"; fi
) &

echo "Starting VOXCUT on ${URL}"
exec uv run --quiet uvicorn voxcut.main:app --host 127.0.0.1 --port "${PORT}"
