# LAZOS Architecture

LAZOS is three cooperating processes on one Mac, all communicating over loopback:

```
┌──────────────────────── LAZOS.app (Tauri 2) ────────────────────────┐
│  Rust shell                         WebView (React + R3F)           │
│  • window, CSP, permissions         • AI core (Three.js shaders)    │
│  • backend supervisor ──spawn──┐    • HUD, chat, setup, settings    │
│  • hands URL + token to UI     │    • zustand store                 │
└────────────────────────────────┼──────────────┬──────────────────────┘
                                 │              │ HTTP + WebSocket (127.0.0.1:47811, token)
                                 ▼              ▼
              ┌─────────────── AI backend (Python / FastAPI) ───────────────┐
              │ api/      HTTP routes · WebSocket · typed contracts         │
              │ chat.py   turn orchestration (route → research → LLM → TTS) │
              │ inference/ LLMBackend protocol · Ollama impl · prompts      │
              │ speech/   mic/speaker · Silero VAD · MLX Whisper · Piper/say │
              │ research/ router · SearXNG/DDG providers · safe fetch       │
              │ conversations/ SQLite store   models/ manager + converter   │
              └───────┬───────────────────────────┬─────────────────────────┘
                      │ 127.0.0.1:11434           │ 127.0.0.1:8888 / HTTPS
                      ▼                           ▼
              Ollama (qwen3.5:9b, MLX runner)   SearXNG (Docker) · DuckDuckGo · web pages
```

## Why the backend owns the audio devices

The Python backend captures the microphone and plays speech (PortAudio via `sounddevice`), not the
WebView. This keeps the whole voice loop (capture → VAD → STT → LLM → TTS → playback) in one process,
which makes half-duplex echo prevention dependable: frames are dropped whenever LAZOS isn't
listening, and the queue is flushed after playback. It also avoids WKWebView `getUserMedia` quirks.
The UI receives **real** levels (`audio.level`, about 30–40 Hz) computed from the exact samples being
captured or handed to the output device, and the AI core visualises those.

## Backend modules

| Module | Responsibility |
|---|---|
| `core/config.py` | `BootConfig` (env `LAZOS_*`; loopback-only bind is enforced) and `UserSettings` (persisted, validated) |
| `core/security.py` | Per-install token (0600 file), constant-time compare, origin allow-list |
| `core/events.py` | `EventHub`: fan-out to WebSocket clients, sticky events replayed on reconnect, lossy audio levels |
| `core/state.py` | `StateMachine`: `idle · listening · transcribing · thinking · speaking · error`, with a validated transition table |
| `core/metrics.py` | Per-turn latency: STT, research, time-to-first-token, tokens/s, time-to-first-audio, total |
| `inference/base.py` | `LLMBackend` protocol + error taxonomy (`LLMUnavailable`, `ModelMissing`, `LLMTimeout`) |
| `inference/ollama.py` | Streaming chat, tool calls, warm-up, unload, pull with progress |
| `inference/prompts.py` | Persona and behaviour prompts (no hard-coded replies), voice rules, research rules, tool schema |
| `inference/context.py` | Token budgeting, newest-first history window, deterministic timestamps for prompt caching |
| `chat.py` | `ChatService`: one active turn, cancellation, research tool loop (max 2 searches), sentence TTS, persistence, rolling summarisation |
| `speech/audio_io.py` | `MicCapture` (16 kHz, 32 ms frames), `AudioPlayer` (callback stream, real output level), device listing/refresh |
| `speech/vad.py` | Silero VAD v6 on onnxruntime (no torch at runtime) + `UtteranceSegmenter` (pre-roll, min speech, silence end, max length) |
| `speech/stt.py` | MLX Whisper on a dedicated thread, tr/en auto-detect, whole-utterance hallucination filter |
| `speech/tts.py` | `PiperTTS` (primary), `MacSayTTS` (fallback; prefers Premium/Enhanced Turkish voices), `TTSManager` |
| `speech/text.py` | Markdown → speakable text, streaming `SentenceSplitter` (decimals, abbreviations, code fences) |
| `speech/speaker.py` | `SpeechSession`: pipelined synthesis and playback per answer |
| `speech/voice.py` | `VoiceController`: push-to-talk and continuous modes, microphone fault detection |
| `research/router.py` | Fast intent routing and a deterministic search-query builder |
| `research/service.py` | Provider fallback chain, domain diversity, concurrent page fetch, passage selection, validated tool args |
| `research/fetch.py` | SSRF guard (public IPs only, re-checked after redirects), size cap, trafilatura extraction |
| `models/manager.py` | Detect, approve-then-install, progress events, size + SHA-256 verification, Ollama pull |
| `models/whisper_convert.py` | Converts an existing OpenAI Whisper `.pt` to MLX, with strict key/shape validation |
| `memory/identity.py` | `CoreIdentity` (name, creator, origin, purpose, descriptions), defaults, and how it is written into the system prompt |
| `memory/store.py` | Identity table; learned memories (category, origin, sensitivity, source references, version history, usage); duplicate merging; FTS5 search over memories and messages |
| `memory/text.py` | Turkish folding (İ/ı, ş, ğ…), stopwords, prefix stemming for FTS queries, similarity measure for duplicates |
| `memory/intent.py` | Deterministic signals: remember, forget, persistent instruction, hypothetical, episodic, learnable, sensitive, secret, time windows |
| `memory/learner.py` | The local model proposes `update_memory` tool calls; rules decide what is applied; background worker that pauses for user turns |
| `memory/service.py` | Entry point used by the chat pipeline: system profile, per-turn retrieval, explicit operations, episodic search, learning queue |
| `api/memory.py` | Owner-only management of identity, memories and episode search |
| `feedback/policy.py` | Feedback reasons, correction classification (style/local/factual), blocking (secrets, identity, instructions), PII warnings, preference conflicts |
| `feedback/service.py` | Ratings, corrections, approved-example retrieval (≤ 2, lexical), speech-recognition corrections, suggestions (approve/reject/revoke), local export |
| `api/feedback.py` | Owner-only feedback, learning and export API (`/api/feedback`) |

## WebSocket contract (`/ws?token=…`)

Client → server, validated with Pydantic as a discriminated union (`api/schemas.py`):

| type | fields |
|---|---|
| `chat.send` | `text` (1–8000), `conversation_id?` |
| `chat.cancel` / `speech.stop` / `ping` | — |
| `voice.ptt` | `action: start\|stop`, `conversation_id?` |
| `voice.continuous` | `enabled`, `conversation_id?` |
| `conversation.select` | `conversation_id?` |

Server → client (mirrored in `apps/desktop/src/types/protocol.ts`):

| type | purpose |
|---|---|
| `hello` | current conversation and settings |
| `state` *(sticky)* | AI state and detail (`research`, `voice`, error code) |
| `system` *(sticky)* | LLM reachability and load state, speech engines, online status, search provider, last metrics |
| `settings`, `setup.models`, `voice.mode` *(sticky)* | configuration, model install progress, voice mode |
| `chat.user` → `chat.start` → `chat.research`* → `chat.delta`* → `chat.done` | one turn |
| `chat.error` | turn failure with a stable `code` and a user-friendly Turkish message |
| `voice.speech`, `voice.transcript`, `voice.notice` | VAD activity, transcript, voice errors |
| `audio.level` | `{source: mic\|tts, level: 0..1}`, dropped first under back-pressure |

The HTTP routes are `/health` (public), plus `/api/status`, `/api/setup`, `/api/setup/install`,
`/api/setup/ollama/start`, `/api/setup/mic-test`, `/api/conversations[/{id}]` (CRUD),
`/api/settings`, `/api/audio/devices`, `/api/voice/preview` and `/api/metrics`.

## A conversation turn

1. **Persist** the user message and publish `chat.user` / `chat.start`. State becomes `thinking`.
2. **Route** with `research/router.py`:
   - `search` for explicit requests and time-sensitive wording ("bugün", "güncel", prices, weather, scores);
   - `none` for greetings, math and timeless explanations;
   - `auto` for everything else, where the model decides through its native `web_search` tool call.
3. **Research** (when routed or requested by the model). The query is built deterministically with
   no extra LLM round-trip, and relative dates are anchored, e.g. "bugün" → "16 Ekim 2026". Search
   falls back SearXNG → DuckDuckGo; the top pages are fetched concurrently, extracted, and reduced to
   query-relevant passages. Results are injected as an `assistant tool_call` + `tool` message pair,
   prefixed with untrusted-data rules. At most 2 searches per turn.
4. **Stream** tokens from Ollama with `think: false`. Each delta goes to the UI and, when speaking,
   to the sentence splitter. Synthesis of sentence N+1 overlaps playback of sentence N.
5. **Finalize**: persist the answer with its sources (each marked `cited` if referenced as `[n]`) and
   metrics, then publish `chat.done`. Return to `idle`, or to `listening` in continuous voice mode.
6. **Summarise** in the background if older messages fell out of the context window.

Cancellation (Stop/Esc, a new message, or the mic during an answer) cancels the asyncio task. That
closes the HTTP stream so Ollama stops generating, saves the partial text as `interrupted`, and
silences TTS.

## Identity and long-term memory

**Storage.** (Schema v4 since V1.2.1 adds the feedback tables; see [LEARNING.md](LEARNING.md).) The database uses schema version 2, tracked with `PRAGMA user_version`; databases from
before this change upgrade in place and existing messages are backfilled into the index. It adds:
- `core_identity`: a single row;
- `memories`: category, content, origin `auto|explicit|manual`, sensitivity flag, source
  conversation and message IDs, history of previous versions, usage counters;
- two FTS5 indexes (`memories_fts`, `messages_fts`) over Turkish-folded text.

**Every turn:**
1. **System prompt** = core identity + behaviour + the *profile*: user identity, communication and
   personal-preference memories, capped at 24 entries / 1,600 chars. It changes only when those
   memories change, so the cached prompt prefix survives.
2. **Explicit commands** ("hatırla", "bundan sonra…", "…unut") run the memory model **before**
   answering, about 1–3 s, which lets LAZOS confirm exactly what it saved or deleted.
3. **Retrieval** (local, milliseconds):
   - FTS for topical memories (projects, interests, decisions, knowledge), plus category hints
     ("hangi projeden…" → projects);
   - for questions about the past, matching messages from *other* conversations, filtered by
     relative time windows ("dün", "geçen hafta", "geçen ay"), falling back to the latest
     conversations;
   - results are injected as a `search_memory` tool result with a "do not invent" rule. The model
     can also call `search_memory` itself.
4. **Learning** after the answer: if the user message looks like a durable first-person statement
   and isn't hypothetical, it's queued. A background worker waits until LAZOS is idle, sends the
   message plus four messages of context and candidate memories (shown to the model as `m1`, `m2`, …)
   to the local Qwen, and is cancelled and re-queued as soon as a new turn starts. Measured: this
   call does not evict the chat prompt cache (the next turn's first token arrived in 121 ms).

**Guard rails applied after the model's proposal:**
- deletes happen only on an explicit "forget" request;
- sensitive facts need explicit permission;
- secrets are never stored;
- facts about LAZOS's own identity are rejected;
- near-duplicates (Jaccard ≥ 0.75) refresh the existing entry;
- updates keep the previous text in the entry's history.

Identity and episodic questions never trigger web research.

**Embeddings.** None are used. At personal scale (hundreds of memories, thousands of messages),
Turkish-folded FTS5 with prefix stemming plus category hints is fast and accurate enough. Semantic
embeddings are on the roadmap if recall proves insufficient.

## Context management and prompt caching

Ollama's MLX runner does **not** enforce `num_ctx`: we measured a 17k-token prompt being fully
processed. LAZOS therefore enforces its own budget: `num_ctx` minus the answer reserve and room for
research results.

- History is kept newest-first. Older messages are folded into a rolling summary of at most 150
  words, stored per conversation as `summary` / `summary_upto`.
- Prefix caching makes warm turns fast (about 50–100 ms to the first token when the prefix matches).
  To keep the prefix stable:
  - the system prompt contains no clock;
  - each user turn carries its own `[Şu an: …]` stamp, reproduced identically from `created_at`
    for history messages;
  - the tool definitions are always sent;
  - the warm-up primes exactly this prefix.

## Voice state machine

```
          ┌──────── continuous mode loops ────────┐
          ▼                                       │
IDLE ─▶ LISTENING ─VAD end─▶ TRANSCRIBING ─▶ THINKING ─first audio─▶ SPEAKING ─done─┘
          │  ▲                     │ empty/hallucination ─▶ LISTENING (or IDLE in PTT)
          │  └── typed message in progress: yield, resume after
          └─ mic stall / all-zero signal (permission) ─▶ ERROR ─2.5 s─▶ IDLE
```

- **Segmenter:** speech starts after about 64 ms of voiced frames, keeps 300 ms of pre-roll, needs
  at least 250 ms of speech, ends after 700 ms of silence (configurable), and cuts off at 30 s.
- **Echo guard:** mic frames are discarded outside `LISTENING`, and the capture queue is flushed
  plus a 350 ms tail guard after playback.
- **Faults:** device errors, a stream that stops delivering frames, and an all-zero signal (macOS
  permission denied) all surface as `voice.notice` messages and the red state. The loop always exits
  cleanly, so the app never stays stuck.

## Frontend

- `hooks/useBackend.ts`
  - discovers the backend (Tauri command/event or a dev env token),
  - routes events (audio levels go to a mutable buffer, everything else to the reducer),
  - re-syncs the open conversation after a reconnect,
  - drives the CSS `--accent` colour, which the whole HUD follows.
- `stores/reducer.ts` is a pure, unit-tested event reducer. `stores/app.ts` holds the zustand store
  and its actions.
- `components/ai-core/`
  - a shared uniform set driven by one `useFrame` driver (lerped colours, energy, turbulence and
    phase; fast-attack/slow-release audio level; pointer parallax);
  - layers: backdrop, volumetric glows, an orbital particle field (all motion computed on the GPU),
    energy-stream comets, seven procedural holographic rings, an audio waveform band, a geodesic
    shell, electrical arcs (processing and error only), a scan ring, and a nucleus inside a
    translucent plasma shell;
  - Bloom and Vignette post-processing;
  - `PerformanceMonitor` lowers DPR and then particle and geometry detail. Reduced motion follows the
    OS setting or the in-app setting.
- React never re-renders per frame. Meters and the mic halo update the DOM directly from
  `requestAnimationFrame`.

## Resource management (24 GB target)

| Component | Measured memory | Notes |
|---|---|---|
| qwen3.5:9b in Ollama (MLX nvfp4, 8K context) | **7.8 GB** | `keep_alive` is configurable (default 30 min); one LLM only |
| Backend process: Whisper turbo fp16, Piper, VAD, torch | **2.8 GB** physical footprint | Whisper loaded once, on a dedicated thread |
| **Total AI working set** | **about 10.5 GB of 24 GB** | |

The MLX runner grows its KV cache with the prompt. One oversized 17k-token prompt pushed it to
13.3 GB until the model was reloaded. LAZOS's context budget keeps normal prompts within `num_ctx`.

The output audio stream closes three seconds after playback, so idle CPU stays near zero and device
changes are picked up.

## Measured performance (M4 Pro, 24 GB)

| Metric | Value |
|---|---|
| LLM decode speed | 47–56 tok/s |
| Time to first token, warm small talk | 0.3–0.6 s |
| Time to first token, research turn | 7–9 s (search about 2–4 s, then prefill of about 2k tokens at about 400 tok/s) |
| Whisper turbo, about 6 s of Turkish speech | 0.8 s warm (2.6 s cold) |
| Piper synthesis per sentence | about 70 ms (real-time factor 0.013) |
| macOS Yelda synthesis per sentence | 0.8–1.4 s (real-time factor 0.13–0.27) |
| AI core render rate | 120 fps in headless Chrome. Real-window FPS isn't measured yet; check with Safari Web Inspector. |
