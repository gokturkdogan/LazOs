# Web research engine (V1.2.2)

LAZOS answers time-sensitive questions by searching the web locally: SearXNG first, DuckDuckGo
as a fallback. No paid API is used and no extra model or LLM call is added. This document
describes the pipeline, how answers are verified, how to debug it, and the measured results.

## Why it was rebuilt: root cause

The reproduced failure was *"Şu an Galatasaray ligde kaçıncı sırada?"*. The pipeline was traced
stage by stage with `evals/research/trace_v121.py` and `trace_turn.py`. The answer was available,
but it was lost before reaching the model:

| Stage | Finding |
|---|---|
| Search results | **The answer was there.** A Wikipedia snippet had `1, Galatasaray, 7, 5, 1, 1, 16, 11, +5, 16`. Another snippet said "Galatasaray … puan durumunda 1. sırada ve 16 puanı var". |
| Query | `Galatasaray ligde kaçıncı sırada 16 Ekim 2026`. The date and the word "ligde" pulled in Champions League news (3 of 8 results). |
| Extraction | trafilatura ran with `include_tables=False`. Mackolik's table (20 mentions of Galatasaray) became 0 mentions, leaving only the cookie banner. beIN's standings live in embedded Next.js JSON, so nothing was extracted. |
| Context | `body = excerpt or snippet`: a useless excerpt (cookie text, "PUAN DURUMU / SONRAKİ HAFTA") **replaced** the snippet that held the answer. |
| Model | Behaved honestly: "bulamadım" (3/3 runs). With the same question and the real table as context, it answered correctly **3/3** (`evals/research/control.py`). |

**Root cause:** a pipeline defect, not the model. Structured data was discarded, the one place
it survived (snippets) was overwritten, and the query was polluted. No timeout or async problem
was involved.

## Pipeline

```
request ─▶ intent ─▶ targeted query ─▶ SearXNG ─▶ rank & filter ─▶ snippets enough? ──yes──┐
                                                                     │ no                     │
                                                    fetch selected pages (≤3 parallel, 4 s)   │
                                                    stop as soon as verified                  │
                                                                     ▼                        │
                                          text + HTML tables + embedded-JSON tables           │
                                                                     ▼                        │
                                          evidence (deterministic) + cross-check ◀────────────┘
                                                                     ▼
                                          one fallback query if still no evidence
                                                                     ▼
                                 context: rules → evidence → sources (snippet + relevant excerpt)
```

### 1. Intent (`app/research/intent.py`)
Deterministic classification of the *whole request*:
- **standings**: with competition, season, team and wanted value (rank, points, leader, table);
- **weather**, **exchange** and **version**: version questions also capture the product name;
- **current** ("şu anki CEO'su kim?"), **news** and **general**.

Seasons follow the football calendar: 16 Oct 2026 is in **2026-27**, and "geçen sezon" means
**2025-26**. Elliptical follow-ups inherit the previous intent: "Peki Fenerbahçe?" and
"Ya lider kim?" both work.

### 2. Routing (`app/research/router.py`)
Recognised live-data intents always search. The V1.2 rules still apply: a word like "bugün",
"şimdi", "son" or "yeni" alone triggers nothing, and personal questions with a date
("2031'de hangi telefonu alacağım?") don't search. Identity and episodic questions are answered
from memory. General questions ("Python nedir?") answer directly.

### 3. Query
| Intent | Query |
|---|---|
| standings | `Trendyol Süper Lig 2026-27 puan durumu`. A league table answers every team's rank, points and the leader; a date would pull in match news. |
| weather | `İstanbul hava durumu`. Forecast pages carry temperatures; dated queries returned news without numbers. |
| version | `Python latest version`, `Node.js LTS latest version`. Release pages state the version in their snippet. |
| others | Unchanged V1.2 builder (command words stripped, relative dates anchored). |

### 4. Ranking and filtering (`service._rank`)
Every result gets a score and a reason, which the diagnostics panel shows:
- **Authority per intent:** for example tff.org, uefa.com, mackolik, beIN, Wikipedia for
  standings; TCMB, doviz.com for exchange rates; mgm.gov.tr for weather; python.org,
  nodejs.org, endoflife.date for versions.
- **Answer-bearing snippets:** a snippet that already contains a version number, a rate or a
  temperature scores +3. This beats a famous homepage that doesn't state the answer.
- **Rejected:** duplicates (normalised URL), social and video pages, malformed hosts (SearXNG
  occasionally returns `www.x.comwww.x.com`), and a different or outdated season in the title or
  snippet.
- **Demoted:** a different competition (Champions League vs Süper Lig), and stale dates per
  intent (news > 30 days, exchange rates > 7 days, weather > 3 days).
- At most one result per domain; `max_sources` (default 3) results are selected.

### 5. Extraction (`app/research/extract.py`)
- **HTML tables** keep rows and columns. Each table gets a **label**: its caption or the nearest
  short heading, such as "Trendyol Süper Lig" vs "Şampiyonlar Ligi" on the same page. Responsive
  duplicate cells ("Galatasaray Galatasaray", "1 1") are undone without breaking "77". Layout
  tables that wrap other tables are skipped, so the data table inside is used (tff.org).
- **Embedded JSON** (`__NEXT_DATA__`, `application/json`): lists of records with a rank, points
  and a team name become tables. Home and away splits are skipped and the overall table is
  preferred, as on beIN.
- **Text** comes from trafilatura with cookie, navigation and subscription boilerplate removed.
  **Dates** come from page meta tags, then htmldate.
- **Encoding:** the header charset, then `<meta charset>`, then UTF-8, then Windows-1254 (older
  Turkish sites; tff.org is windows-1254).
- No headless browser is used. Every page in the failing case had its data in the HTML or the
  embedded JSON, so a browser wasn't needed. Pages are fetched with a normal GET (SSRF guard
  kept), and access restrictions aren't bypassed.

### 6. Evidence (`app/research/evidence.py`)
For standings, values are parsed **deterministically**:
- Columns are mapped from Turkish or English headers (`O G B M Av P` / `MP W D L GD Pts`). An
  unnamed team column is inferred from its cells, and a rank prefix in the team cell
  ("1.GALATASARAY A.Ş.") is split off.
- A table counts only if it looks like a league table: at least 4 rows, and points never rise
  down the table.
- A table is rejected if its label or the page title names **another competition** or **another
  season**.
- Snippet rows count too: the Wikipedia-style comma row, and sentences such as
  "… 1. sırada ve 16 puanı var".

**Verification:**
- **Freshness first:** more matches played means a later snapshot, so older snapshots are noted,
  not mixed in.
- **Agreement:** values are grouped by (rank, points). The group backed by the most independent
  domains wins, then authority, then real tables over snippets.
- **Status:** `verified` (≥ 2 independent domains agree), `single_source`, `conflict` (the
  alternative is reported to the model and in diagnostics), or `insufficient` (the model is told
  there is no evidence and must not invent numbers).
- For a **past season**, the evidence says so explicitly: "2025-26 GEÇMİŞ sezondur (şu anki sezon
  2026-27) … sezonun şampiyonu …".

The model receives one explicit line, for example
`Trendyol Süper Lig 2026-27 · Galatasaray: 1. sıra, 16 puan, 7 maç (5G 1B 1M), averaj +5 [1][2]`,
plus only the relevant table rows (top 3 and the team's neighbours). Every extra row costs about
2 ms of prefill before the first token.

Other intents (exchange rate, weather, version, news) have no deterministic extractor. The
model gets each source's **snippet (always)** plus the relevant passages and, if one matches, the
relevant table with rows intact.

### 7. Answer rules (`RESEARCH_RULES`, in the tool result, not the system prompt)
- If there is an evidence block, it is the answer: say it directly and briefly with the source
  number.
- Don't repeat the question, give a long introduction or summarise the research.
- Don't say "bulamadım" when evidence exists, and don't change or add numbers.
- Don't present dated information as current, and don't mix seasons, competitions or dates.
- If the sources really aren't enough, say so in one sentence.

### 8. Staged strategy and limits
1. Search (one query).
2. If two independent snippets agree, including the number of matches played, no page is
   opened.
3. Otherwise fetch the selected pages, at most 3 in parallel with 4 s each, and **stop as soon
   as the value is verified**. Remaining fetches are cancelled.
4. If a structured question is still unanswered, **one** fallback query is tried (for example
   `Galatasaray Trendyol Süper Lig 2026-27 puan durumu`). There is no loop.
5. The whole research step is capped at 14 s. A slow site times out without blocking the others.

## Developer diagnostics

**Ayarlar › Web araştırması › Araştırma tanılaması (geliştirici)** is off by default. When it's
on, research answers show a **tanılama** button under the sources. The panel shows:
- the request and the detected intent;
- the queries;
- timings per stage;
- every search result with its score and why it was selected or rejected;
- every fetch (status, ms, size, tables found, rejected tables and why);
- the extracted evidence, with status, agreeing sources and notes;
- the exact research content passed to the model;
- the final answer;
- fallbacks and errors.

*JSON kopyala* copies the trace. A trace contains **only** the request and web data, with no
conversation history, memories or credentials. E-mail addresses and token-like strings in the
request are redacted. Traces are kept **in memory only** (the last 30, never written to disk),
and turning the setting off discards them.

API (token-protected): `GET /api/research/traces`, `GET /api/research/traces/{message_id}`,
`DELETE /api/research/traces`.

Command-line tools in `services/ai-backend/evals/research/`:
- `trace_v121.py`: stage-by-stage trace of the old pipeline (kept for reproduction);
- `trace_turn.py "<soru>" N`: full real turn, showing what the model saw;
- `control.py`: the model with correct evidence;
- `bench.py`: the benchmark below.

## Benchmark (real SearXNG, real pages, real qwen3.5:9b; 17 questions × 2 runs)

Truth is fetched fresh at run time by the harness's own parser and is never hard-coded:
- Süper Lig standings from tr.wikipedia's standings template and Mackolik;
- software versions from endoflife.date, respecting LTS start dates.

Run it with `python -m evals.research.bench --label after --runs 2`.

| Metric | Before (V1.2.1) | After (V1.2.2) |
|---|---|---|
| Correct answers (checkable) | 18/34 | **29/34** |
| Standings (rank, points, leader, follow-up, last season) | 4/14 | **14/14** |
| "Bulamadım"-type refusals with research | 3 | **0** |
| Researched answers with a number not in the sources | 5 / 24 | 3 / 28 |
| Unnecessary searches (general/casual questions) | 0 | 0 |
| Research time, mean / p95 (matched questions) | 2117 / 3439 ms | **1792 / 3313 ms** |
| Total turn time, median (matched) | 7718 ms | **6534 ms** |
| Total turn time, median, standings | 7402 ms | **5072 ms** |
| Time to first token, median (matched) | 4962 ms | 5559 ms |
| Time to first token, median, standings | 4720 ms | **4308 ms** |
| Research context, median | 2569 chars | 2777 chars |
| Answer length, median | 241 chars | **134 chars** |

Notes:
- The follow-up "Peki Fenerbahçe?" didn't search at all before; now it does.
- Time to first token on non-standings questions rose because their context now carries
  relevant tables and snippets (exchange-rate and version pages). Total time still fell,
  because answers are shorter.
- The scorer is deliberately simple and was applied identically to both runs. Two harness bugs
  were fixed before comparing: "26" matched inside "2026", and Node 26's *future* LTS date was
  treated as current. A last-season answer that calls the finished season "unfinished" counts as
  wrong.
- The remaining misses are exchange-rate answers with numbers averaged or rounded by the model,
  and a CEO answer without a citation number.

## Known limitations

- Deterministic extraction and verification exist **only for league standings**. Exchange
  rates, weather, versions and news rely on the model reading snippets, passages and tables;
  it occasionally averages or rounds values (3 of 28 answers).
- Authority lists and club names are hand-maintained. A team or competition outside them still
  works through generic table parsing, but without the entity shortcut.
- Pages that render data only in the browser, with no HTML table and no embedded JSON, can't be
  read. No such case was observed, so no headless browser was added.
- Snippets can lag the live table. The freshness rule (most matches played) handles a snapshot
  that is one round behind, but it can't detect a source that is entirely stale with no
  match-count column.
- The result depends on what SearXNG's upstream engines return on that day. A missing source
  falls back to one more query, then honestly reports "no evidence".
