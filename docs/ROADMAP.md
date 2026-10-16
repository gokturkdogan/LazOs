# LAZOS Roadmap

## V1.0 (this release): conversational intelligence

**Done and verified by automated tests**
- Local streaming chat with `qwen3.5:9b` (Ollama, MLX runner), thinking disabled, keep-alive and
  prompt-cache-aware warm-up, cancellation, timeouts, and clear errors.
- Context management: token budget, newest-first window, rolling per-conversation summaries.
- Hybrid research routing (fast rules plus native tool calling); SearXNG → DuckDuckGo fallback; safe
  page extraction; numbered, clickable, verified sources; untrusted-data handling.
- Voice pipeline: Silero VAD → MLX Whisper → LLM → sentence-streamed Piper or macOS TTS; push-to-talk
  and continuous half-duplex modes; microphone fault and permission detection.
- SQLite history: create, continue, rename, delete, search; survives restarts.
- First-run setup: compatibility, Ollama runtime, approval-gated model install with progress and
  checksums, local reuse of existing Whisper weights, microphone test.
- The living AI core, with state colours driven by real audio levels.

**Needs manual confirmation** (see TESTING.md): real-microphone recognition quality in your room,
speaker playback, macOS permission prompts in the bundled app, and long-session FPS in the native
window.

## Known limitations

- **Turkish TTS quality:** Piper's only Turkish voice (`dfki-medium`) is very fast but slurs some
  words. macOS Yelda is clearer but slower. A better open Turkish neural voice is the most valuable
  upgrade.
- **Research latency:** the first token takes 7–9 s on research turns, mostly search, page fetch, and
  prefill at about 400 tok/s on the MLX runner.
- **Half-duplex only:** you can't interrupt LAZOS by voice while it speaks. The mic button and Esc do
  interrupt.
- **Development-style packaging:** `LAZOS.app` runs the backend from the repository's Python venv.
  It isn't a self-contained, signed distribution.

## Possible future improvements (not part of V1)

- **Voice:**
  - a better local Turkish voice, either a fine-tuned VITS/Piper voice or a lighter
    Chatterbox/CosyVoice variant when real-time on Apple Silicon;
  - full-duplex barge-in with acoustic echo cancellation (e.g. the WebRTC APM);
  - wake word ("LAZOS") with a tiny on-device keyword model;
  - streaming partial transcripts while the user speaks.
- **Inference:**
  - evaluate the GGUF `qwen3.5:9b-q4_K_M` (llama.cpp runner) and MTP variants for faster prefill;
  - an optional direct MLX-LM backend behind `LLMBackend`;
  - a smarter context: retrieval over past conversations, saved user facts ("memory").
- **Research:** result caching, a page-quality score, follow-up searches when sources disagree, and
  an inline "read more" mode for one source.
- **Packaging:** embed a relocatable Python runtime (or a PyInstaller sidecar) for a self-contained,
  signed and notarized `.app` plus auto-updates.
- **UI:** keyboard shortcut for push-to-talk, conversation export, light accessibility themes, and
  a multi-monitor "core only" ambient mode.

The V1 scope deliberately excludes computer control, file or app automation, screen or camera
access, autonomous agents, background tasks, smart home, email or calendar integrations, and plugins.
