#!/usr/bin/env bash
# LAZOS — project-local setup. Never installs system-wide software or changes security settings;
# it checks prerequisites, explains what is missing, and sets up the project's own environments.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/services/ai-backend"
DESKTOP="$ROOT/apps/desktop"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; MISSING=1; }
MISSING=0

echo "LAZOS setup — checking prerequisites"
[ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] && ok "macOS on Apple Silicon" || fail "LAZOS requires macOS on Apple Silicon (arm64)"
MEM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
[ "$MEM_GB" -ge 16 ] && ok "${MEM_GB} GB unified memory" || warn "${MEM_GB} GB memory — 16 GB+ recommended"

PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$(command -v "$candidate")"; break; fi
done
[ -n "$PYTHON" ] && ok "Python: $($PYTHON --version)" || fail "Python 3.11+ not found — install with: brew install python@3.13"
command -v node >/dev/null && ok "Node.js $(node --version)" || fail "Node.js not found — install with: brew install node"
command -v cargo >/dev/null && ok "Rust $(rustc --version | cut -d' ' -f2)" || fail "Rust not found — install from https://rustup.rs"
xcode-select -p >/dev/null 2>&1 && ok "Xcode Command Line Tools" || fail "Xcode Command Line Tools missing — run: xcode-select --install"
if command -v ollama >/dev/null 2>&1 || [ -d /Applications/Ollama.app ]; then
  ok "Ollama installed"
else
  fail "Ollama not found — install from https://ollama.com/download/mac (or: brew install ollama)"
fi
command -v docker >/dev/null && ok "Docker (optional, for SearXNG search)" || warn "Docker not found — web research falls back to DuckDuckGo"

if [ "$MISSING" -ne 0 ]; then
  echo; echo "Install the missing prerequisites above, then re-run scripts/setup.sh."; exit 1
fi

echo; echo "Setting up the Python backend (project-local virtualenv)"
if [ ! -x "$BACKEND/.venv/bin/python" ]; then
  "$PYTHON" -m venv "$BACKEND/.venv"
fi
"$BACKEND/.venv/bin/pip" install -q --upgrade pip
"$BACKEND/.venv/bin/pip" install -q -r "$BACKEND/requirements/dev.txt"
ok "backend dependencies installed"

echo; echo "Setting up the desktop app"
(cd "$DESKTOP" && npm install --silent)
ok "frontend dependencies installed"

echo
echo "Done. AI models are installed from the in-app setup screen (with your approval):"
echo "  scripts/dev.sh        # launch LAZOS in development mode"
echo "  scripts/searxng.sh up # optional: start the local SearXNG search engine (Docker)"
