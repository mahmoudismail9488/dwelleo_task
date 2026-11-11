# Listing Inbox Normalizer

Turns messy broker WhatsApp messages (English, Arabic or both) into structured, reviewable property listings. Each message is classified (`listing`, `project`, `buyer_request`, `irrelevant`), its fields are extracted and normalized, it is deduplicated against what's already stored, and anything missing, contradictory or inferred is flagged for human review with a readable reason.

**The hard rule is enforced in code: never invent data.** Every value an extractor reports must be traceable to the message text, or it is dropped and flagged. Every inference is listed in `inferred_fields` and gets a review reason.

| Document | What it holds |
|---|---|
| [`PLAN.md`](PLAN.md) | The plan written with the agent **before** implementation (left as written) |
| [`DECISIONS.md`](DECISIONS.md) | 14 decisions, including **6 agent mistakes** (D3, D5, D9, D12, D13, D14) and where I overrode the plan |
| [`messages.jsonl`](messages.jsonl) | 50 test messages beyond the brief's samples, each with a `note` on the expected result |
| [`AI_LOG.md`](AI_LOG.md) | Key agent sessions and the prompts that mattered |
| [`CLAUDE.md`](CLAUDE.md) | The agent setup used throughout (working agreement and project rules) |

---

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install
make test                     # run the test suite (offline, no API key)
make serve                    # start the HTTP API on http://127.0.0.1:8000 (docs at /docs)
```

### Optional: the LLM extractor

```bash
cp .env.example .env          # then set LLM_API_KEY (and optionally model + prices)
```

| Situation | Extractor used |
|---|---|
| No `.env`, or `LLM_API_KEY` empty | **Rule extractor**: deterministic, offline, free |
| `LLM_API_KEY` set | **LLM** (Qwen via the OpenAI SDK; any OpenAI-compatible endpoint), with the rule extractor as an automatic fallback |

The CLI, the API and the LLM read `.env` themselves. Values exported in the shell win over the file. **The test suite never reads `.env`.**

---

## How to run

### CLI

```bash
uv run normalizer process tests/fixtures/messages.jsonl            # the brief's 10 samples
uv run normalizer process messages.jsonl                           # 50 broader test messages (see their `note` fields)
uv run normalizer process messages.jsonl --db data/listings.db     # also keep the results
```

- **stdout** carries only result JSON, one line per valid input line. **stderr** carries everything else: the extractor in use, skipped malformed lines (`line 7: skipped …`), a summary and, in LLM mode, a cost and latency line.
- A malformed line never stops the run. The exit code is `0` (`2` only if the input file can't be read).
- By default the database is in-memory for the run, so dedup works within the file and nothing is kept.

### HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /` | Liveness check: `{"status": "success"}` |
| `POST /messages` | Ingest one raw message (`{"id", "text", "sender", "received_at"}`) and return its result. Re-posting an id returns the stored result |
| `GET /listings?needs_review=true\|false` | Stored listings (filter optional), plus `message_id` and `approved_at` |
| `POST /listings/{id}/approve` | Mark a listing approved (`needs_review: false`; review reasons are kept as history). `404` if unknown |
| `GET /llm-runs?message_id=` | Every LLM call with tokens, cost and latency |

The database file is `DB_PATH` (default `./data/listings.db`), created on the first request.

---

## Output

The brief's contract, plus these additions:

| Field | Meaning |
|---|---|
| `land_area_sqm` | Plot area. `area_sqm` is the **built-up area** (for `land`, the plot area) |
| `price_per_sqm` | When the message prices per m² |
| `price_candidates` | When prices conflict, every candidate with its basis, while `price.amount` stays `null` (D2) |
| `inferred_fields` | Fields that were inferred, not stated |
| `source` | `"llm"` or `"rules"` |
| `project` | For `classification: "project"`: name, city, min price, min/max bedrooms, delivery date, payment terms, contact phone (same sender fallback as listings, D12), and its own `review_reasons` / `needs_review` / `inferred_fields` (D7). `listings` is `[]` |

`needs_review` is calculated from `review_reasons` and can't be set by hand. Review reasons come from a fixed vocabulary (`app/review.py: REVIEW_REASONS`), for example `price_missing`, `price_contradiction`, `city_inferred`, `contact_phone_from_sender`, `purpose_inferred`, `possible_duplicate`, `llm_fallback_used`, `price_unverified`, `price_interpreted`, `llm_listing_count_suspicious`.

### Key policies (full list: PLAN.md §5 and DECISIONS.md)

- **Conflicting prices** (m-007: 900 × 2,500 = 2.25M vs "total 2.1M") → `amount: null`, both candidates kept, **no tolerance** (D2).
- **City from district** only when the district belongs to exactly one city in our list, and then flagged `city_inferred`. "Al Olaya" (Riyadh *and* Khobar) gives `city: null`.
- **No phone in the text** → the sender's number, flagged `contact_phone_from_sender` (listings and projects).
- **Studio** without a bedroom count → `bedrooms: 0` (inferred, no flag). **Yearly or monthly price** without "for rent" → `purpose: rent`, flagged.
- **Prices:** `2.5M` = 2,500,000, `2.500` = 2,500, `2.5` with no suffix = ambiguous → `null` (D4).
- **Dedup:** exact matches only, and conflicting stated facts block a match (D8). m-003 → `duplicate_of: m-001-1`.

---

## Architecture

```
message → Extractor (LLM | rules) → grounding → review/policies → dedup → SQLite
              │  fallback to rules on timeout / API error / malformed or unusable output
              └─ rules also run as a cross-check of the listing count
```

| Module | Role |
|---|---|
| `app/extractors/llm.py` | Qwen via the OpenAI SDK, JSON mode, temperature 0, a citation prompt, strict parsing, metrics per call |
| `app/extractors/rules.py` + `app/gazetteer.py` | Regex, keyword lists and a small city/district list (EN + AR) |
| `app/grounding.py` | **The anti-hallucination layer.** Every value must appear in the message, otherwise `null` + `<field>_unverified`. Prices are **cited** (`{quote, amount}`): the quote must be in the text and our parser checks the number (D6) |
| `app/review.py` | Field policies, inferences, review reasons |
| `app/dedup.py`, `app/store.py` | Duplicate detection; SQLite (`messages`, `listings`, `llm_runs`) |
| `app/pipeline.py` | `process_message()`, the single entry point for the CLI and the API |

**How malformed or invented LLM output is caught:** non-JSON, wrong types or an invalid classification make the message fall back to rules (`llm_fallback_used`). Values that aren't in the message are removed (`*_unverified`). A city derived from a district is rejected. A sender's number passed off as "stated" is rejected (D9). A listing count that disagrees with the rules is flagged. All of this is tested offline with a fake client in `tests/test_llm_extractor.py`.

---

## LLM cost and latency per message

Measured on the 10 sample messages with **qwen3.8-max**, timeout 60s (International region, official list price **$2 / $6 per 1M input / output tokens**, [Alibaba Cloud pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing), checked 2026-11-11). Source: the `llm_runs` table.

| Metric | Value |
|---|---|
| Calls | **10 / 10 ok**, 0 fallbacks |
| Tokens per message | ≈ 1,010 input (mostly the system prompt) + ≈ 570 output (77–2,032) |
| **Cost per message** | **≈ $0.0055** (range $0.0024–$0.0143) → ≈ **$5.5 per 1,000 messages** |
| **Latency per message** | **mean ≈ 17 s**, median ≈ 11 s, range 5–54 s |

- **The hard messages cost the most.** m-010 (3 listings in one message) took 54s and $0.014; m-007 (the price contradiction) took 17s. With the earlier 20s timeout, both timed out (≈ 41s with the retry) and fell back to rules. Hence the 60s default.
- The rule extractor costs nothing and takes milliseconds. In LLM mode it also runs on every message, as the cross-check and the fallback.
- **Cheaper option:** `qwen-plus` is listed at $0.4 / $1.2 (non-thinking), about **5× cheaper** and likely faster. It's not measured here.
- See your own numbers: the CLI prints a summary to stderr, and `GET /llm-runs` (or `--db` plus the `llm_runs` table) has them per message. Set `LLM_INPUT_PRICE_PER_M` and `LLM_OUTPUT_PRICE_PER_M`; the API returns tokens, not cost.

---

## Known limitations

- **The LLM is slow on the hardest messages.** qwen3.8-max needs up to ~54s for a multi-listing message, so the timeout is 60s. A batch CLI run is sequential, so 1,000 messages take hours. A faster model or concurrency would fix it (see next steps).
- **Enum grounding is keyword-based (D13).** On the LLM path, a purpose or type stated in words our lists don't know goes to review instead of being trusted.
- **Small gazetteer:** 6 cities and 13 districts. Unknown districts are captured from the text but never used to infer a city, and dedup compares them by raw spelling.
- **The rule extractor is pattern-based.** It misses number words for rooms and areas (`خمس غرف`), the bare English unit `m` (`600 m`), and unusual layouts. It flags rather than guesses, so these become review items, not wrong data.
- **Citations cover prices only.** Areas and bedrooms are grounded by "the number appears in the text", so they can't be interpreted from words.
- **Dedup is exact-only** (D8). A repost with a rounded price and no matching area is missed. A strong duplicate isn't flagged for review; it's marked with `duplicate_of`.
- **Approval can't correct data.** A reviewer can approve, but can't fix a price or pick a `price_candidate`.
- **The listing-count cross-check** can flag cases where the LLM is right and the rules are wrong.
- No auth, no pagination, single process (SQLite with a lock).

## What I'd do next with another day

1. **A labelled evaluation set** (50–100 messages, including Arabic-heavy and messy ones) with a script scoring rules vs LLM per field. Today the quality claims rest on the 10 samples plus targeted tests.
2. **Pick the model from data:** measure `qwen-plus` against `qwen3.8-max` on that set (quality per field vs. cost and latency).
3. **A real district dataset** (Saudi districts with Arabic/English names and their cities) instead of the hand list.
4. **Reviewer actions:** `PATCH /listings/{id}` to correct fields or choose a price candidate on approval, keeping an audit trail.
5. **Extend citations** to areas and bedrooms, and parse Arabic number words.
6. **Throughput:** async LLM calls with concurrency limits for batch CLI runs, plus caching of identical texts (reposts).
