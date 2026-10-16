#!/usr/bin/env bash
# Launch LAZOS in development mode. The desktop shell starts and supervises the Python backend.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -x "$ROOT/services/ai-backend/.venv/bin/python" ] || [ ! -d "$ROOT/apps/desktop/node_modules" ]; then
  echo "Environment not set up yet — running scripts/setup.sh first."; "$ROOT/scripts/setup.sh"
fi
# Start local search if Docker is running (optional; DuckDuckGo is used otherwise).
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  "$ROOT/scripts/searxng.sh" up >/dev/null 2>&1 || true
fi
cd "$ROOT/apps/desktop" && exec npm run tauri dev
