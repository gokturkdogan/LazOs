#!/usr/bin/env bash
# Run LAZOS automated checks.
#   scripts/test.sh        fast, hermetic suite (no models, network or audio hardware needed)
#   scripts/test.sh --all  also runs live checks: real Ollama model, web search, speech models, audio device
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKERS="not integration and not network and not models"
[ "${1:-}" = "--all" ] && MARKERS=""

echo "▸ backend lint";      (cd "$ROOT/services/ai-backend" && ./.venv/bin/ruff check app tests)
echo "▸ backend tests";     (cd "$ROOT/services/ai-backend" && ./.venv/bin/pytest -q -p no:cacheprovider ${MARKERS:+-m "$MARKERS"})
echo "▸ frontend types";    (cd "$ROOT/apps/desktop" && npx tsc --noEmit)
echo "▸ frontend tests";    (cd "$ROOT/apps/desktop" && npx vitest run --silent)
echo "▸ desktop shell";     (cd "$ROOT/apps/desktop/src-tauri" && cargo check -q)
echo "All checks passed."
