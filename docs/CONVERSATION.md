# Conversation and voice intelligence (V1.2)

This document records why LAZOS sounded unnatural, what was changed, and the measurements behind each
decision. All measurements were taken on the target M4 Pro with `qwen3.5:9b`.

## 1. Audit: root causes

The audit read the real conversation history (from a read-only copy) and replayed scripted
conversations. 44 of 115 earlier assistant messages showed at least one problem.

| Symptom | Root cause |
|---|---|
| "Bundan sonra sana Göktürk olarak hitap edeceğim" | The system prompt said "Bu Mac üzerinde…" and "bu Mac onun bilgisayarı", so the model kept mentioning *Mac*. Piper reads "Mac" as **"Maç"**, and the model once even wrote "Meki". The user objected to being called "Mek", and the model treated that as a request to change how it addresses him. |
| Pronunciation complaints that went nowhere | Whisper transcribed the spoken word "backend" as **"başkent"** and later "Butch Kent", so the model never saw the real word. It then repeated its previous answer verbatim. |
| "Senin yaratıcın sen", "Sen Göktürk'ün", "Ben Göktürk Doğan'ın geliştirdiğim…" | The identity block described the creator in the third person and also said "the person talking is your creator", which confused I/you. A memory that slipped in made it worse: "Kullanıcı LAZOS'un yaratıcısıdır" got past the identity guard because the guard ignored the apostrophe in "LAZOS'**un**". |
| Name in almost every reply; "Sana nasıl yardımcı olabilirim?"; emojis | The persona was a list of prohibitions with no positive examples. |
| Long, self-introducing answers | The persona opened with a self-description that the model parroted. |
| "Hangi modeli kullanıyorsun?" → "bilemiyorum" or a hallucinated answer | The prompt contained no facts about LAZOS's own system. |
| "Bugün hiç kod yazasım yok" → a **weather report** | The research router treated "bugün" (today) as a request for fresh information. |
| "Az önce hangi projeden…" → a correct answer followed by "hafızamda kayıtlı değil" | The episodic search excluded the current conversation, found nothing, and told the model "no record found". |
| Unrelated memories in answers | A category hint ("uygulama" → projects) and single-word full-text matches injected project memories into replies like "Bir futbol uygulaması." |
| Markdown, lists and long answers in voice mode | Voice rules were thin, and they were only added to the system prompt on voice turns. That also broke the prompt cache whenever the mode switched. |
| A hidden latency cost | The old system prompt contained the current time (minute precision), so every minute invalidated the prompt cache. |

## 2. Conversation changes

- **Identity**
  - Written in the second person with explicit roles, plus worked question/answer pairs with
    correct Turkish suffixes computed from the configured names (`tr_copula`, `tr_genitive`).
  - Identity facts that slipped into memory are **kept in the database** but excluded from the
    prompt.
- **Persona**
  - Positive guidance for a composed, efficient, JARVIS-inspired tone, plus a small set of style
    examples. The examples are deliberately *different* from the evaluation prompts.
  - Explicit rules on continuity, context priority (current request > this conversation >
    preferences > memories), style requests, and brief memory acknowledgements.
- **System facts:** the model, speech recognition and voice engines, and the privacy facts, so
  LAZOS answers capability questions correctly.
- **Voice vs text**
  - The voice rules live permanently in the system prompt. Each user turn carries a deterministic
    `[Şu an: … · sesli]` tag, so voice and text turns share one cached prefix.
  - The *Sesli yanıt tarzı* setting (natural / detailed) controls spoken length.
- **Conversation style setting:** natural, professional or concise.
- **Router:** time words count only together with an information-seeking question, or with
  inherently fresh topics (news, prices, weather, scores).
- **Episodic memory:**
  - "az önce", "demin" and "bu konuşmada" refer to the current conversation, so they never trigger
    an episodic search.
  - The "not found" note now tells the model that the current conversation is in front of it.
- **Memory relevance:** a topical memory is injected only with at least two shared meaningful stems
  or a shared proper noun. Category hints apply only to first-person questions ("projelerim",
  "tercihim") and questions about the past.

## 3. Inference profiles

**Sampling.** Controlled comparison: 14 prompts × 2 runs per configuration, judged automatically
plus by reading the transcripts.

| Config | Identity/continuity | Clichés | Emoji | Casual answers ending in "?" | Median casual length |
|---|---|---|---|---|---|
| A current (T 0.7, `repeat_penalty` 1.05) | 11/12 | 0 | 0 | 5/12 | 143 |
| **B Qwen-recommended (T 0.7, rp 1.0, presence 1.5)** | **12/12** | 0 | 0 | 7/12 | 117 |
| C T 0.5 | 12/12 | 0 | 0 | 6/12 | 130 |
| D no penalties | 12/12 | 0 | 0 | 2/12 | 102 |

The differences are small; the large improvement came from the prompt. B was chosen:
- it is Qwen's documented non-thinking profile;
- A made one identity error;
- D removes the protection against verbatim repetition, which was an observed failure.

The MLX runner was verified to honour `repeat_penalty`, `presence_penalty` and `frequency_penalty`.

**Thinking.** Six multi-step arithmetic, logic and code questions, two runs:

| Mode | Correct | Latency |
|---|---|---|
| Fast | 2/6 | 0.1–0.6 s |
| Thinking | **6/6** | 4.6–8.7 s |

Implemented as **Düşünme modu** with three settings: Hızlı, Otomatik (the default) and Derin.
"Otomatik" uses a strict detector (`app/inference/reasoning.py`) that fires only for:
- multi-step arithmetic,
- word problems,
- pasted code that needs debugging,
- explicit requests such as "adım adım düşün".

Thinking turns use T 0.6, top_p 0.95 and a 6,144-token budget, and the UI shows **DERİN DÜŞÜNÜYOR**.

## 4. Pronunciation layer

```
LLM text → screen / history / memory (unchanged)
         → to_speakable (markdown, code, URLs, citations removed; parentheses → pauses)
         → engine chosen → Pronouncer.apply(text, engine) → TTS
```

**Matching rules:**
- Turkish-aware word boundaries; apostrophe suffixes are kept ("backend'i" → "bek end'i").
- Longest match first (multi-word terms).
- Matching is case-insensitive by default; a rule can be made case-sensitive (API, SQL, Mac, TL).

**Protected:** URLs, e-mail addresses, file paths, `x=y` assignments and `func()` calls. Code blocks
never reach speech. Repeated punctuation collapses ("!!!" → "!", "?!" → "?").

**Rules:** user rules live in SQLite (schema v3, table `pronunciation_rules`). They override the
built-in rule for the same term and engine, can be scoped to one voice, and can be disabled.
**Overhead:** < 0.5 ms per sentence (tested).

**How the built-in respellings were chosen.** Every candidate was synthesised inside "Bugün X
hakkında konuştuk." with the real voice and transcribed by Whisper (Turkish, no hints). A pass (✓)
means Whisper recovered the English term.

| Term | Piper reads raw as | Chosen | Piper | Yelda |
|---|---|---|---|---|
| backend | "Bağcıkent" | bek end | ✓ | heard "bekend" (correct Turkish speech) |
| JavaScript | "Cava Sıcık'ta" | cava skript | ✓ | ✓ |
| TypeScript | "Tipes Cipta" | tayp skript | ✓ | ✓ |
| GitHub | ✓ | git hab | ✓ | ✓ (raw failed) |
| Python | ✓ | payton | ✓ | ✓ |
| FastAPI | "Fast AP" | fast ey pi ay / fest ey pi ay | ✓ | closest |
| API | "AP" | ey pi ay | ✓ | ✓ |
| SQL | "Sekyule" | es kü el | ✓ | ✓ |
| React | "Reacit" | riekt | ✓ | ✓ |
| Node.js | ✓ | nod cey es | ✓ | ✓ |
| npm | ✓ (read "nepeme") | en pi em | ✓ | ✓ |
| framework | "From the Org" | freymvörk (Piper only) | ✓ | raw ✓ |
| repository | "RepoStory" | repozitori (Piper only) | ✓ | raw ✓ |
| database | "de Tabase" | deytabeys | ✓ | ✓ |
| server | ✓ | sörvır / sörver | ✓ | closest |
| localhost | "Lojal Host" | lokal host | ✓ | ✓ |
| deployment | "Deptoyment" | diployment (Piper only) | closest | raw ✓ |
| Docker | "Doğucukar" | doker | — | — |
| JSON | "son" | ceysın | heard "Jason" | heard "Jason" |
| Mac / macOS / MacBook | "Maç" | Mek / mek o es / mekbuk | — | — |
| frontend, GitLab | ✓ | no rule needed | ✓ | ✓ |

Rows marked "—", "closest" or "heard …" are flagged **doğrulanmadı** in the UI. They use the
conventional Turkish pronunciation, which Whisper doesn't confirm, so **they need a human listening
check.** Abbreviations: vb. → ve benzeri, vs. → vesaire, örn. → örneğin, bkz. → bakınız,
TL → lira. Numbers, times and percentages are already read correctly by eSpeak ("3.5" → "üç nokta
beş", "15:30" → "on beş otuz", "1.250" → "bin iki yüz elli").

**Speech-recognition vocabulary.** Whisper receives the assistant's name, the creator's name and the
dictionary terms as `initial_prompt`. Turkish-accented technical sentences:

| Speaker voice | Terms recognised without hint | With hint |
|---|---|---|
| Yelda | 11/16 | **15/16** |
| Piper | 13/16 | **16/16** |

The hint costs about 3 ms. Silence and noise produced no new hallucinations, apart from a lone "!",
which is now discarded.

## 5. Before and after (real model, copy of the real database)

| User | Before | After |
|---|---|---|
| LAZOS, backend yine patladı. | "Backend'i tekrar boğdun mu? 😄 Hangi serviste çöktü…" | "Hangi servis çöktü? Logları veya hata mesajını at, nedenini bulalım." |
| Bugün hiç kod yazasım yok. | Web search → weather forecast, 7.3 s | "Olur öyle günler; zorlamaya gerek yok. Belki kısa bir yürüyüş…" |
| Sen kimsin? | "Ben LAZOS. … Sana yardımcı olmak için buradayım." | "Ben LAZOS; senin geliştirdiğin kişisel bir yapay zeka asistanıyım." |
| Seni kim geliştirdi? | "Göktürk Doğan tarafından geliştirdim. Kendimi tanıtmak için buradayım…" | "Beni Göktürk Doğan geliştirdi; yani sen." |
| Göktürk kim? *(voice)* | "Ben Göktürk Doğan'ın geliştirdiğim kişisel bir yapay zeka asistanıyım; yani seninle konuşan kişi benim…" | "Sen Göktürk'sün; beni geliştiren kişi." |
| Hangi modeli kullanıyorsun? *(voice)* | "Yerel olarak çalışan … asistanısın … bilmiyorum" | "qwen3.5:9b modelini Ollama üzerinden yerel olarak çalıştırıyorum…" |
| Teşekkürler. | "Ne demek, her zaman buradayım… 😊" | "Ne demek." |
| Az önce hangi projeden bahsediyorduk? | Right project, then "hafızamda kayıtlı değil…" | "React ile geliştirdiğin futbol uygulamasından bahsediyorduk." |
| Bundan sonra kısa cevap ver. | "Tamam, bundan sonra kısa cevap vereceğim." | "Tamam." |

**Latency** (medians across the scripted conversations; prompts identical before and after):

| Metric | Before | After |
|---|---|---|
| Time to first token, text | 731 ms | 471 ms |
| Time to first token, voice | 558 ms | 562 ms |
| Time to first audio, voice | 788 ms | 907 ms ¹ |
| Total, text | 1,554 ms | 1,041 ms |
| Total, voice | 2,149 ms | 1,080 ms |
| TTS per sentence, including normalisation | 80 ms | 58 ms |
| Explicit memory command, time to first token | 2,739 ms | 2,372 ms |
| Median answer length | 188 chars | 90 chars |

¹ First audio waits for the first complete sentence. In one after-run answer the first sentence was
long, which raised the median; normalisation itself costs under 0.5 ms.

**Memory:**
- Backend process: about 370 MB before Whisper is loaded (the benchmark harness, before → after:
  406 → 367 MB). With Whisper loaded, the earlier measurement was about 2.8 GB of physical footprint;
  V1.2 adds no models.
- Ollama after warm-up: 8.1 GB. After 4 chat turns: 8.6 GB. After one thinking turn: 8.8 GB.
- Long sessions with very different prompts can grow the MLX runner's cache, which reached 16 GB
  during these experiments. Unloading the model (the keep-alive timeout) resets it.

## 6. Remaining limitations

- The 9B model still makes occasional Turkish grammar slips, e.g. "Sen benin geliştiricisin",
  "paylaştırmak gerek", "ciddi konuştuk". Prompting reduces but cannot eliminate them.
- Pronunciation was chosen by machine listening. Several rules are flagged "doğrulanmadı", and a
  **human listening test is still required** for every rule.
- Explicit memory commands still wait about 1.5–2.5 s for the memory model before answering. This
  favours correctness, so LAZOS only confirms what was actually saved.
- Thinking adds about 5–8 s and is used only when the detector fires, or always in "Derin" mode.
