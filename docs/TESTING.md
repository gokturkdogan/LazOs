# Testing LAZOS

## Automated tests

```bash
scripts/test.sh          # hermetic suite: ruff, 151 backend tests, tsc, 11 frontend tests, cargo check
scripts/test.sh --all    # adds 16 live tests (real Ollama: identity, memory and the V1.2 conversation scenarios; web search; real voices and Whisper; audio)
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
| `tests/test_pronunciation.py` | Per-voice respelling (backend, TypeScript, …) inside Turkish sentences; apostrophe suffixes; word boundaries and case sensitivity; URLs, paths, commands and code blocks never altered; abbreviations and punctuation; custom rules (override, engine scope, disable); dictionary API (add, edit, delete, test); corrections off; **display text, history and memory unchanged end to end**; < 0.5 ms per sentence |
| `tests/test_conversation.py` | The "· sesli" tag is deterministic and kept in history; the system prompt is identical for voice and text turns; style settings reach the prompt; Turkish suffix helpers; "bugün" alone no longer triggers web research; "az önce" is not episodic; no contradicting "not found" note; short replies don't pull unrelated memories; identity facts kept but unused; brief memory acknowledgement; deep-reasoning detection and generation profiles; speech-recognition vocabulary; punctuation-only transcripts discarded |
| `tests/test_live_conversation.py` *(integration/models)* | **All 10 V1.2 scenarios with the real model**, checked for behaviour: no clichés or emoji, length limits, identity, continuity, the persisted preference after restart, and corrected pronunciation in the speech input. Also a spoken-style check, deep reasoning solving a word problem, and a real Piper/Yelda → Whisper round trip with the dictionary (Piper 6–7/7 across runs because synthesis is stochastic; Yelda 6/7) |
| `tests/test_feedback.py` | **V1.2.1 learning:** ratings (save, re-rate, withdraw); corrections keep the original answer; edit/delete; approved examples injected as *data* in the tool result, never in the system prompt; local, factual/pending and blocked corrections never retrieved; unrelated or generic requests get no examples; at most 2; repeated signals create a suggestion but **no memory before approval**, with no duplicates; approve/revoke; contradicting preference replaces the old one and revoke restores it; transcript corrections keep Whisper's original and never touch the TTS dictionary; STT vocabulary suggestion → approve/revoke; temporary states not learned; identity/instruction/secret corrections blocked; deleting a conversation keeps approved examples; v3 → v4 migration idempotent and data-preserving; export privacy (approved style only, no identity/secrets); retrieval switch; streaming unchanged; retrieval < 15 ms with 1,000 corrections; restart persistence; no cloud dependencies; "çıktısı" (output) is not a factual claim; pool / dev / held-out sets are disjoint |
| `src/stores/reducer.test.ts` | UI event reducer (streaming, research, errors, foreign conversations, voice state, learning events and suggestion toasts); state → colour mapping; citation linking outside code; formatting |

**Last verified run** (M4 Pro, 24 GB, V1.2.1, 2026-10-16): 167 backend tests passed (151 hermetic + 16 live), and 11 of 11
frontend tests passed.

## Conversation benchmark (real model)

```bash
cd services/ai-backend
.venv/bin/python -m evals.run benchmark --split dev      # 36 scenarios used while fixing
.venv/bin/python -m evals.run benchmark --split heldout  # 32 scenarios, never tuned on
.venv/bin/python -m evals.run feedback                   # approved-example retrieval A/B experiment
```

Each scenario runs in a fresh temporary data directory with a recording fake web search. Results and
full transcripts go to `evals/results/`. See [LEARNING.md](LEARNING.md#evaluation) for the method
and the latest numbers.

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

### 8. Natural speech (V1.2) — human listening required
1. Open Ayarlar › Ses › **Sözlük**, then use *Piper ile dinle* and *Yelda ile dinle* on the sample sentence.
   - [ ] "backend", "TypeScript", "React", "FastAPI" sound natural.
2. Check every rule marked **doğrulanmadı**: Docker, JSON, Mac, macOS, MacBook, FastAPI (Yelda), server (Yelda), deployment (Piper).
   - [ ] Each sounds right; customise any that don't.
3. In voice mode ask "Backend neden yavaş olabilir?".
   - [ ] A short spoken answer: no list reading, no "yıldız yıldız".
4. Say "LAZOS, biraz daha ciddi konuş", then chat for a while.
   - [ ] The tone changes without any self-introduction.

### 9. Feedback and learning (V1.2.1)
1. Hover an answer, press 👎, choose "Çok uzun", and save. Repeat on two more answers.
   - [ ] A toast "Yeni öğrenme önerisi: …" appears; Hafıza › Öğrenme shows it as *bekliyor*.
   - [ ] Hafıza › Öğrenilmiş anılar has **no** new entry until you press *Onayla*.
2. Press *Düzelt* on an answer, write a shorter answer, tick *Benzer sorularda örnek olarak kullan*.
   Then ask a similar question in a new conversation.
   - [ ] The turn's metrics show `examples: 1`; the answer follows the style but doesn't copy it.
3. In voice mode, say a technical word that Whisper mishears; fix it with the pen icon. Do it twice
   for the same word.
   - [ ] The message shows "düzeltildi" (hover shows Whisper's original); a vocabulary suggestion appears.
4. Öğrenme › Veriler › *Dışa aktar*: check the preview, untick one record, export.
   - [ ] The file in `~/Downloads/LAZOS/` contains only the ticked approved records.

### 10. Failure handling
1. Quit Ollama while LAZOS is open.
   - [ ] The top-bar chip turns red. Sending a message shows a clear Turkish error, the core briefly
     turns red, then recovers.
2. Restart Ollama.
   - [ ] The next message works without restarting LAZOS.
