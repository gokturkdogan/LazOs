# LAZOS

**YOUR MACHINE. YOUR INTELLIGENCE. YOUR RULES.**

LAZOS is a voice-enabled AI assistant for macOS that runs entirely on your Mac. You can talk to it in
Turkish (or English) by text or voice, and it can research the web when a question needs fresh
information. The centre of the interface is a living, holographic AI core that reacts to everything
LAZOS does.

- **Local inference:** the language model, speech recognition, voice activity detection and speech
  synthesis all run on-device. LAZOS uses no cloud AI APIs.
- **Turkish first:** answers are written in Turkish by default, with natural spoken Turkish replies.
- **Voice conversation:** push-to-talk, or hands-free continuous conversation (half-duplex, so LAZOS
  never transcribes its own voice).
- **Web research with real sources:** search runs only when a question needs it. Answers cite
  numbered, clickable sources, and web content is treated as untrusted data.
- **Private history:** conversations are stored in a local SQLite database and never synced.

| State | Core colour | Meaning |
|---|---|---|
| Idle | Blue | Calm orbits, breathing glow |
| Listening | Cyan | Reacts to the real microphone level |
| Processing / researching | Amber | Faster orbits, scan ring, electrical activity |
| Speaking | Green | Reacts to the real synthesized voice level |
| Error | Red | Slow, non-flashing warning pulse |

## Requirements

| | |
|---|---|
| Hardware | Apple Silicon Mac. Built and measured on an M4 Pro with 24 GB. 16 GB minimum. |
| OS | macOS 13 or later |
| Tools | Python 3.11+, Node.js 20+, Rust (rustup), Xcode Command Line Tools |
| Runtime | [Ollama](https://ollama.com/download/mac) 0.12+ |
| Optional | Docker, to run the local SearXNG search engine. Without it, LAZOS falls back to DuckDuckGo. |
| Disk | About 10 GB for models |

## Quick start

```bash
scripts/setup.sh        # checks prerequisites, creates the project venv and installs npm deps
scripts/searxng.sh up   # optional: local SearXNG search (Docker, bound to 127.0.0.1)
scripts/dev.sh          # launches LAZOS (the desktop shell starts the AI backend)
```

On first launch the **setup screen** checks:

1. system compatibility,
2. that Ollama is installed and running (it can start Ollama for you),
3. which AI models are missing, with their sizes,
4. microphone access, and
5. which search providers are available.

Models download only after you click **Onayla ve yükle**, and anything already on the machine is reused.
For example, an existing `~/.cache/whisper/large-v3-turbo.pt` is converted locally instead of being
downloaded again.

| Model | Purpose | Size |
|---|---|---|
| `qwen3.5:9b` (Ollama, MLX runner) | Conversation, tool calling | 7.6 GB |
| Whisper large-v3-turbo (MLX) | Turkish/English speech recognition | 1.6 GB |
| Piper `tr_TR-dfki-medium` | Turkish speech synthesis | 63 MB |
| Silero VAD v6.2.3 | End-of-speech detection | 2.3 MB |

Models live in `~/Library/Application Support/LAZOS/models`. Ollama keeps its own model store.

## Using LAZOS

- **Type** in the bottom dock and press Enter. Press **Esc** to stop an answer or stop speaking.
- **Mic button (push-to-talk):** click and speak. LAZOS notices when you stop; you can also click again to finish.
- **SESLİ MOD:** hands-free conversation. LAZOS goes listen → understand → think → speak → listen
  again. Click the mic during an answer to interrupt and speak.
- **History (clock icon):** open, rename, delete or search past conversations.
- **Settings (sliders icon):** model, context window, voice engine (Piper or macOS Yelda) with a
  preview, speech speed, recognition language, VAD sensitivity, audio devices, research mode, visual
  quality and reduced motion.

## Development commands

```bash
scripts/dev.sh            # desktop app in dev mode (hot reload)
scripts/backend.sh        # backend only, on http://127.0.0.1:47811
scripts/test.sh           # fast, hermetic checks (lint, unit and protocol tests, types, cargo check)
scripts/test.sh --all     # also live checks: real Ollama, web search, speech models, audio device
scripts/build.sh          # release LAZOS.app (unsigned) in apps/desktop/src-tauri/target/release/bundle/macos
scripts/searxng.sh up|down|status|logs
```

For more detail, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md),
[docs/TESTING.md](docs/TESTING.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

## Privacy and security

- The backend binds to `127.0.0.1` only and refuses non-loopback addresses.
- Every API and WebSocket call needs a per-install secret token. The token is stored in
  `~/Library/Application Support/LAZOS/auth.token` with mode 0600, and the browser origin is checked.
- Research sends **only the search query** to the search provider. Page fetching is restricted to
  public http(s) addresses (SSRF guard), responses are size-capped, and nothing from the web is executed.
- Web content goes to the model as clearly marked *untrusted data* and must not be followed as
  instructions. A test verifies this against an injected instruction.
- The connectivity indicator uses a bare TCP connect and sends no data. LAZOS has no telemetry.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Yerel servis başlatılamadı" | Run `scripts/setup.sh`. Check `~/Library/Application Support/LAZOS/logs/backend-process.log`. |
| Port 47811 already in use | Another app owns the port; LAZOS refuses to talk to it. Start with `LAZOS_PORT=47999 scripts/dev.sh` and update the CSP `connect-src` in `tauri.conf.json`. |
| Red "YEREL AI" chip | Ollama isn't running. Use **Ollama'yı başlat** on the setup screen, or run `ollama serve`. |
| "Mikrofon tamamen sessiz" | macOS delivers silence when access is denied. Allow LAZOS (in dev builds, your terminal or editor) under System Settings › Privacy & Security › Microphone. |
| Voice pronunciation | Piper's Turkish voice is fast but slurs a few words. Switch to **Yelda** in settings for clearer speech. For a better Yelda, install the *Enhanced/Premium* voice under System Settings › Accessibility › Spoken Content; LAZOS uses it automatically. |
| Research says it failed | Check the search chip in the top bar. Start SearXNG with `scripts/searxng.sh up` or check internet access. |
| Slow first answer | The first request loads about 8 GB into memory. Later answers reuse the warm model and its prompt cache. |

Logs are written to `~/Library/Application Support/LAZOS/logs/`: `backend.jsonl` holds structured
logs including per-turn latency metrics.

## Licenses of bundled or downloaded components

The LAZOS code itself is unlicensed and for personal use. Third-party components:

- **Qwen 3.5:** Apache 2.0.
- **Whisper:** MIT.
- **Silero VAD:** MIT.
- **piper-tts 1.3+:** GPL-3.0.
- **Piper voice `tr_TR-dfki-medium`:** trained on the DFKI dataset under **CC BY-NC-SA 4.0**
  (non-commercial).
- **macOS voices:** governed by Apple's macOS licence.

Review these before any commercial use.
