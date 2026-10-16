# Testing LAZOS

## Automated tests

```bash
scripts/test.sh          # hermetic suite: ruff, 83 backend tests, tsc, 10 frontend tests, cargo check
scripts/test.sh --all    # adds 11 live tests (real Ollama incl. the identity/memory scenarios, web search, speech, audio)
```

| File | What it covers |
|---|---|
| `tests/test_api.py` | Public health check; token on HTTP and WebSocket; foreign-origin rejection; token file mode 0600; conversation CRUD; settings validation (422); setup/model reporting; install validation |
| `tests/test_chat_stream.py` | Streaming event order and persistence; context carries history; forced research with real citation flags; model-initiated tool calls; invalid tool arguments; search failure without fabricated sources; cancellation → `interrupted`; `llm_unavailable` / `model_missing` → error state; malformed client messages; long-conversation summarisation |
| `tests/test_units.py` | Intent routing (13 Turkish/English cases); search-query builder; tool-argument validation; state-machine transitions and recovery; event-hub back-pressure; context budget and orphan handling; deterministic history stamps; markdown → speech; streaming sentence splitter; VAD segmentation (utterance, noise, forced end); SSRF guard; passage selection |
| `tests/test_persistence_models.py` | Conversations, sources, metrics and settings survive a database reopen (application restart); cascade delete; model detection; local Whisper reuse instead of download; checksum and size verification of downloads; Whisper key remapping |
| `tests/test_voice_pipeline.py` *(models)* | **Real Silero VAD + real Whisper** with a simulated microphone: continuous conversation with the full state sequence `listening → transcribing → thinking → speaking → listening`; push-to-talk back to idle; silence produces no turn; an all-zero microphone (permission denied) reports an error and recovers |
| `tests/test_live.py` *(integration/network/models)* | Real Turkish streaming answer from `qwen3.5:9b` (no `<think>`, short small talk); real cancellation in under 0.5 s; live research with extracted pages; Piper synthesis faster than real time; macOS Turkish voice; output-device lifecycle (plays inaudible silence) |
| `tests/test_memory.py` | Identity in every system prompt and across restarts; identity editable only via settings, not via chat; explicit preference persists across restart; forgetting; deletes need an explicit request; updates keep history; background learning with source references; learning can be disabled; hypotheticals and small talk are not learned; sensitive needs permission and secrets are never stored; duplicate merging; relevant-only retrieval; Turkish folding; episodic "last week" search without web research; honest "nothing found"; model-initiated memory search; category hints; migration from the pre-memory database; memory API CRUD; memory on/off |
| `tests/test_live_memory.py` *(integration)* | **All 7 requested scenarios with the real `qwen3.5:9b`**: "Sen kimsin?", "Seni kim geliştirdi?", "Göktürk Doğan kim?", restart → "Adın ne?", "Bundan sonra bana kısa cevaplar ver." → restart → still short, "Geçen konuşmamızda hangi projeden bahsetmiştim?" → Atlas, "Kısa cevap tercihimi unut." → only that memory removed. Also checks that background learning picked up TypeScript and Atlas, and that the creator is not mentioned in an unrelated coding answer. Passed in 3 of 3 consecutive runs. |
| `src/stores/reducer.test.ts` | UI event reducer (streaming, research, errors, foreign conversations, voice state); state → colour mapping; citation linking outside code; formatting |

**Last verified run** (M4 Pro, 24 GB): 94 backend tests passed (83 hermetic + 11 live), and 10 of 10
frontend tests passed.

## Manual verification (needs hardware or a human)

These depend on your microphone, speakers, macOS permissions or a visual judgement, so automated
tests can't cover them.

### 1. Launch and the AI core
1. Run `scripts/dev.sh`. The window opens and the setup screen appears on first run.
   - [ ] Every step shows a green check after you approve any missing models.
2. Click **LAZOS'U BAŞLAT**.
   - [ ] A blue holographic core moves gently: breathing nucleus, rotating rings, drifting particles.
   - [ ] The top bar shows `YEREL AI qwen3.5:9b` in green, `AĞ BAĞLI`, and `ARAMA searxng` (or `duckduckgo`).
3. In Settings, set *Görsel kalite* to Düşük, then to Yüksek.
   - [ ] Rendering stays smooth both times.
4. Enable *Hareketi azalt*.
   - [ ] Motion slows noticeably and the reticle stops rotating.

### 2. Text conversation
1. Type `LAZOS, nasılsın?`.
   - [ ] The core turns amber, then streams a short, natural Turkish answer, and returns to blue.
2. Ask `Bana kuantum bilgisayarları kısaca anlat.`
   - [ ] A concise explanation, with no web search.
3. Ask something long, then press **Esc** mid-answer.
   - [ ] The answer stops and is marked *yarıda kesildi*.

### 3. Microphone and Turkish speech recognition
1. On the setup screen, click **Mikrofonu test et** and allow access in the macOS prompt.
   - [ ] "Mikrofon çalışıyor."
2. Click the mic button and say `Merhaba LAZOS, bugün nasılsın?`.
   - [ ] The core turns cyan and pulses with your voice, and the *Mikrofon* meter moves.
   - [ ] When you stop speaking, the state goes to ÇÖZÜMLÜYOR, then DÜŞÜNÜYOR.
   - [ ] The transcript appears as your message, with a mic icon.

### 4. Turkish voice output
1. Settings › Ses › *Sesi dene* › Piper, then Yelda.
   - [ ] Both play Turkish speech.
   - [ ] The core turns green and the rings and nucleus react to the voice; the *LAZOS* meter moves.
2. Turn on **SESLİ MOD** and ask a question by voice.
   - [ ] The spoken answer starts after the first sentence, not at the end of the whole answer.
   - [ ] After it finishes, LAZOS listens again on its own.
   - [ ] LAZOS does not react to its own voice (test with speakers, not headphones).
3. Unplug or switch the audio device while idle, then use *Aygıtları yenile*.
   - [ ] LAZOS keeps working, or shows a clear error and recovers.

### 5. Web research
1. Ask `Bugün yapay zekâ dünyasında neler oldu?`.
   - [ ] The core turns amber with the label ARAŞTIRIYOR, and a "Web araştırması" bar shows the query.
   - [ ] The answer cites numbered chips such as `[1]`, and clicking a chip opens the page in your browser.
   - [ ] The Kaynaklar list shows real pages; uncited sources are dimmed.
2. Run `scripts/searxng.sh down`, disconnect from the internet, and ask again.
   - [ ] LAZOS says it can't reach current information and shows no sources.

### 6. History and restart
1. Have two conversations, then quit LAZOS (⌘Q) and relaunch.
   - [ ] Both conversations are listed under History with titles and times.
   - [ ] Opening one restores its messages and sources.
   - [ ] Continuing it keeps the context, e.g. it remembers your name.
2. Rename one conversation and delete the other.
   - [ ] The changes persist after another restart.

### 7. Identity and memory
1. Ask "Sen kimsin?" and "Seni kim geliştirdi?".
   - [ ] Natural answers that name LAZOS and Göktürk Doğan, without repeating the full description.
2. Say "Ben genellikle TypeScript kullanıyorum."
   - [ ] Within a few seconds a toast says "Öğrendim: …", and the entry appears under the brain icon › Öğrenilenler.
3. Say "Bundan sonra bana kısa cevaplar ver.", quit with ⌘Q, relaunch, and ask a question.
   - [ ] The answer is short.
4. Say "Kısa cevap tercihimi unut."
   - [ ] A "Unuttum: …" toast appears and the entry disappears.
5. In a new conversation ask "Geçen konuşmamızda ne konuşmuştuk?".
   - [ ] LAZOS answers from the real previous conversation, or says it has no record.

### 8. Failure handling
1. Quit Ollama while LAZOS is open.
   - [ ] The top-bar chip turns red. Sending a message shows a clear Turkish error, the core briefly
     turns red, then recovers.
2. Restart Ollama.
   - [ ] The next message works without restarting LAZOS.
