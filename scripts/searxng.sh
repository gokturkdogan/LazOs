#!/usr/bin/env bash
# Manage the local SearXNG search container used by LAZOS web research.
# Usage: scripts/searxng.sh [up|down|status|logs]
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../infra/searxng" && pwd)"
cmd="${1:-up}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. LAZOS will fall back to DuckDuckGo search." >&2
  exit 1
fi

case "$cmd" in
  up)
    if [ ! -f "$DIR/.env" ]; then
      umask 077
      echo "SEARXNG_SECRET=$(openssl rand -hex 32)" > "$DIR/.env"
    fi
    docker compose -f "$DIR/docker-compose.yml" up -d
    echo -n "Waiting for SearXNG"
    for _ in $(seq 1 30); do
      if curl -fsS "http://127.0.0.1:8888/search?q=test&format=json" >/dev/null 2>&1; then
        echo " — ready at http://127.0.0.1:8888"; exit 0
      fi
      echo -n "."; sleep 1
    done
    echo " — not ready yet (check: scripts/searxng.sh logs)"; exit 1 ;;
  down)   docker compose -f "$DIR/docker-compose.yml" down ;;
  status) docker compose -f "$DIR/docker-compose.yml" ps ;;
  logs)   docker compose -f "$DIR/docker-compose.yml" logs --tail 100 ;;
  *) echo "usage: $0 [up|down|status|logs]" >&2; exit 2 ;;
esac
