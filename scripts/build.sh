#!/usr/bin/env bash
# Build a release LAZOS.app (unsigned). The app runs the backend from this repository's
# services/ai-backend directory (override with LAZOS_BACKEND_DIR).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/apps/desktop" && npm run tauri build -- --bundles app
echo "Built: $ROOT/apps/desktop/src-tauri/target/release/bundle/macos/LAZOS.app"
