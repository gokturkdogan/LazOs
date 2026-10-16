# Developing LAZOS

## Layout

```
LazOs/
├── apps/desktop/                 Tauri 2 + React 19 + TypeScript + Vite
│   ├── src/
│   │   ├── components/ai-core/   living core: palette, uniforms, GLSL shaders, scene, canvas
│   │   ├── components/chat/      chat panel, messages, markdown + citations, sources
│   │   ├── components/voice/     input dock: text, mic (push-to-talk), voice mode, send/stop
│   │   ├── components/layout/    top bar, HUD overlay (reticle, telemetry, meters, captions)
│   │   ├── components/history/   conversation history drawer
│   │   ├── components/settings/  settings panel
│   │   ├── components/setup/     first-run setup flow
│   │   ├── components/common/    emblem, toasts, connection overlay
│   │   ├── hooks/useBackend.ts   connection lifecycle and event routing
│   │   ├── services/             backend discovery (Tauri), HTTP API, resilient WebSocket
│   │   ├── stores/               zustand store, pure event reducer (+ tests), audio level buffer
│   │   └── types/protocol.ts     typed client/server contract
│   └── src-tauri/                Rust shell: backend supervisor, CSP, capabilities, Info.plist
├── services/ai-backend/          Python 3.11+ FastAPI backend (see ARCHITECTURE.md)
│   ├── app/                      api · core · inference · speech · research · conversations · models
│   ├── tests/                    pytest: unit, protocol, persistence, voice pipeline, live
│   └── requirements/             base.txt (runtime), dev.txt (+ pytest, ruff)
├── infra/searxng/                localhost-only SearXNG (docker compose + settings)
├── assets/icons/                 app icon source (SVG)
├── scripts/                      setup, dev, backend, test, build, searxng
└── docs/
```

## Daily workflow

```bash
scripts/dev.sh                        # Vite + Tauri with hot reload; the shell starts the backend
```

- **Frontend changes** hot-reload instantly.
- **Backend changes** need a backend restart. The Tauri shell *adopts* an already-running healthy
  backend, so in another terminal run `scripts/backend.sh` and restart it whenever you change Python.
  Without that, quit and relaunch `scripts/dev.sh`.
- **UI-only development in a browser:** start the backend, then
  `VITE_LAZOS_TOKEN=$(cat ~/Library/Application\ Support/LAZOS/auth.token) npx vite` in `apps/desktop`.
  Origin `http://127.0.0.1:1420` is allowed. In dev builds `window.__lazos` exposes the store, e.g.
  `__lazos.setState({aiState: "speaking"})` previews any core state.

## Configuration

**Boot configuration** comes from environment variables, prefixed `LAZOS_`:

| Variable | Default | Purpose |
|---|---|---|
| `LAZOS_PORT` | `47811` | backend port (also update the CSP in `tauri.conf.json`) |
| `LAZOS_DATA_DIR` | `~/Library/Application Support/LAZOS` | database, models, token, logs |
| `LAZOS_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `LAZOS_SEARXNG_URL` | `http://127.0.0.1:8888` | SearXNG endpoint |
| `LAZOS_WHISPER_TORCH_CHECKPOINT` | `~/.cache/whisper/large-v3-turbo.pt` | reused if present (converted to MLX) |
| `LAZOS_BACKEND_DIR` | repository path | where the Tauri shell finds `services/ai-backend` |
| `LAZOS_LOG_LEVEL` | `INFO` | backend log level |
| `LAZOS_WARMUP` | `true` | preload models at startup |

**User settings** are edited in the UI and persisted in SQLite (`UserSettings` in
`app/core/config.py`). They are validated on the server, so invalid values return 422.

## Swapping the language model

Any Ollama chat model with tool support works. Change it in Settings › Dil modeli, or pull another
model first with `ollama pull <tag>`. Inference sits behind the `LLMBackend` protocol
(`app/inference/base.py`): to add a different runtime (for example MLX-LM directly), implement that
protocol and construct it in `Services`.

## Adding a search provider

Implement `SearchProvider` (`app/research/providers/base.py`): `name`, `available()` and
`search()`. Add it to the provider list in `Services`; providers are tried in order.

## Code quality

- Python uses `ruff check app tests` and `ruff format app`, with line length 140. The tests use fakes
  for the LLM, search, microphone, speaker and TTS (`tests/fakes.py`).
- TypeScript runs in strict mode; check with `npx tsc --noEmit`. Tests use `vitest`.
- Rust: `cargo check` / `cargo clippy` in `apps/desktop/src-tauri`.

## Logs

| File | Content |
|---|---|
| `~/Library/Application Support/LAZOS/logs/backend.jsonl` | structured JSON logs, including a `turn metrics` record per answer (stt, research, time to first token, tok/s, first audio, total) |
| `…/logs/backend-process.log` | stdout/stderr of a backend started by the Tauri shell |
| `…/logs/ollama.log` | output of an Ollama server started from the setup screen |
