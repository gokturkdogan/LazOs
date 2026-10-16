#!/usr/bin/env bash
# Run only the AI backend (useful for backend development or API exploration).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/services/ai-backend" && exec ./.venv/bin/python -m app
