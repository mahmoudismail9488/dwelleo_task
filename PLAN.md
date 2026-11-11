# PLAN — Listing Inbox Normalizer

> Written with my coding agent **before implementation**. Per the brief, this file is kept as written; later changes of direction are recorded in `DECISIONS.md`, not here.

## 1. Goal

A small service that turns messy broker WhatsApp messages (English / Arabic / mixed) into structured, trustworthy listing data — exposed as a CLI and an HTTP API, backed by SQLite.

**Hard rule:** never invent data. Missing → `null` + review reason. Inferred → documented, marked, and flagged.

## 2. Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12+ |
| Env / deps | `uv` |
| HTTP | FastAPI + Uvicorn |
| Validation | Pydantic v2 (contract models, LLM output parsing) |
| Storage | SQLite via stdlib `sqlite3` (no ORM) |
| LLM | Qwen via the **OpenAI SDK** (`base_url`, `model`, `api_key` from env) — any OpenAI-compatible provider works |
| Tests | pytest (+ FastAPI `TestClient`), fully offline |

Commands (target):
- `make test` → `uv run pytest`
- `make serve` → `uv run uvicorn app.api:app`
- `uv run normalizer process messages.jsonl`

## 3. Architecture

```
raw message ─► pre-normalize text (Arabic-Indic digits → ASCII, whitespace)
            ─► Extractor (interface)
                 ├─ LlmExtractor     (used when LLM_API_KEY is set)
                 └─ RuleExtractor    (regex + dictionaries; fallback, always available)
            ─► Validate & ground     (schema check + every value must be traceable to the text)
            ─► Normalize             (prices, phones → E.164, enums)
            ─► Review flagging       (missing / contradictory / inferred)
            ─► Dedup                 (against stored listings)
            ─► Store + emit result
```

- **Extractor contract:** returns a raw, *unnormalized* candidate: classification, list of listing candidates, optional project info — each value ideally with the source snippet it came from.
- **Everything after the extractor is shared and deterministic.** That is where "never invent data" is enforced, so it is the most heavily tested part.
- **LLM failure handling:** timeout, API error, non-JSON, or schema-invalid output → log to stderr, fall back to `RuleExtractor` for that message, add review reason `llm_fallback_used`.

### Proposed layout
```
app/
  models.py        # Pydantic: contract output, extractor candidate models
  textnorm.py      # digits, price parsing, phone → E.164
  gazetteer.py     # district/city dictionary (EN + AR aliases)
  extractors/
    base.py        # Extractor protocol
    rules.py       # RuleExtractor
    llm.py         # LlmExtractor (OpenAI SDK, JSON mode, prompt)
  grounding.py     # does an extracted value appear in the text?
  review.py        # flag rules → review_reasons
  dedup.py         # fingerprint + similarity
  store.py         # SQLite repository
  pipeline.py      # process_message(msg, extractor, store) -> Result
  cli.py           # `normalizer process <file>`
  api.py           # FastAPI app
tests/
  fixtures/        # sample messages + canned LLM responses (good and malformed)
```

## 4. Output contract

The brief's contract, plus these additions (allowed by the brief):

- `listings[].land_area_sqm` — plot/land area when stated (`area_sqm` stays the built-up area, see §5).
- `listings[].price_per_sqm` — when the message prices per square metre (common for land).
- `listings[].price_candidates: [{amount, basis}]` — when price values conflict, every candidate (e.g. `stated_total`, `computed_from_per_sqm`) is kept for the human reviewer while `price.amount` stays `null`.
- `listings[].inferred_fields: [str]` — fields that were inferred rather than stated.
- `listings[].source: "llm" | "rules"` — which extractor produced it.
- `project: {...} | null` — for `classification = "project"`: `name`, `city`, `district`, `min_price`, `min_bedrooms`, `max_bedrooms`, `delivery_date`, `payment_terms`, `contact_phone`. **`listings` is `[]` for projects.**

IDs: deterministic `"{message_id}-{n}"` (e.g. `m-010-2`) so output is reproducible.

## 5. Field policies (the judgment calls)

| Situation | Policy |
|---|---|
| Price `3.2M`, `950 ألف`, `3,200,000`, `85,000/yr`, `35K` | Normalize to a number. `M`/`مليون` ×1e6, `K`/`ألف` ×1e3. |
| Price period | Sale → `total`. Rent with `/yr`, `سنوي`, "2 payments" → `yearly`; `/month`, `شهري` → `monthly`. Rent with no period stated → `period: null` + `price_period_missing`. |
| Price on request (`السعر عند التواصل`) | `amount: null` + `price_missing`. |
| Land (m-007) | Stays a normal **listing** with `property_type: land` (one concrete property — unlike a project, which is many units with ranges). |
| Price contradiction (m-007: 900 × 2,500 = 2.25M vs "total 2.1M") | `amount: null` + `price_contradiction`; `price_per_sqm: 2500`; `price_candidates` holds both 2,250,000 (computed) and 2,100,000 (stated) so the reviewer can pick. A wrong price is worse than a missing one. |
| Per-sqm price with no stated total | `price_per_sqm` filled; `amount` computed as per-sqm × area **only if both are stated**, with `"price.amount"` in `inferred_fields` + `price_computed`. |
| Areas (m-001: land 450 / BUA 380) | `area_sqm` = **built-up area (BUA)**; `land_area_sqm` = land/plot area. For `property_type: land` there is no BUA, so `area_sqm` = land area. If only one unlabeled area is given for a building, use it as `area_sqm`. |
| City missing but district known | Infer city **only** if the district is unambiguous in our gazetteer → add `"city"` to `inferred_fields` + review reason `city_inferred`. Ambiguous district (e.g. Al Olaya: Riyadh *and* Khobar) → `city: null` + `city_missing`. |
| No phone in text | Fall back to the `sender` number (the broker who posted it) → `"contact_phone"` in `inferred_fields` + `contact_phone_from_sender`. |
| Studio | `property_type: studio`. If the message states a bedroom count, use it. Otherwise `bedrooms: 0` by convention, `"bedrooms"` in `inferred_fields`, no review flag. |
| Phone normalization | Accept `05XXXXXXXX`, `5XXXXXXXX`, `9665XXXXXXXX`, `+966 5X XXX XXXX`, `00966…`, Arabic digits → `+9665XXXXXXXX`. Anything else → `null` + `contact_phone_invalid`. |
| Buyer requests / irrelevant | `listings: []`, never extract — even if they contain prices (m-005). |

## 6. Grounding check (catching invented LLM output)

For every value the LLM returns:
- **Numbers** (price, area, bedrooms, bathrooms): the number (or its K/M/ألف/مليون form) must appear in the digit-normalized text. Otherwise → set `null` + `<field>_unverified`.
- **District / city:** must appear in the text, directly or via a gazetteer alias found in the text.
- **Phone:** must appear in the text (after digit/space normalization) or equal the sender.
- **Enums** (purpose, property_type, period, classification): must be valid enum values; otherwise → `null` + flag. Classification invalid → fall back to rules.
- **Listing count sanity:** more listings than the text plausibly contains → flag `llm_listing_count_suspicious`.

## 7. Review reasons (fixed vocabulary)

`price_missing`, `price_contradiction`, `price_computed`, `price_period_missing`, `purpose_missing`, `property_type_missing`, `city_missing`, `city_inferred`, `district_missing`, `bedrooms_missing`, `area_missing`, `contact_phone_missing`, `contact_phone_invalid`, `contact_phone_from_sender`, `<field>_unverified`, `llm_fallback_used`, `possible_duplicate`.

`needs_review = len(review_reasons) > 0`.

## 8. Deduplication

A new listing is a duplicate of a stored one when:
1. **Same normalized contact phone** AND same `property_type` AND same `district` (via gazetteer canonical name) AND (price within 2% OR area within 5%) — strong match → `duplicate_of = <canonical id>`.
2. **No phone match** but same `purpose`, `property_type`, `district`, `bedrooms`, and price within 2% → also a duplicate but add `possible_duplicate` for review (a different broker reposting).
- Duplicates are still stored; `duplicate_of` points to the **earliest** (canonical) listing.
- Listings from the same message are never deduplicated against each other.
- Expected: m-003 → duplicate of m-001-1.

## 9. Persistence & API

- SQLite tables: `messages` (raw + result JSON), `listings` (flattened fields + `needs_review`, `approved_at`, `duplicate_of`).
- `POST /messages` → run pipeline, store, return result. Re-posting the same `message_id` → return the stored result (idempotent).
- `GET /listings?needs_review=true|false` → stored listings.
- `POST /listings/{id}/approve` → set `approved_at`, `needs_review=false`. 404 if unknown. (Allowed whether or not it was flagged.)
- CLI uses an **in-memory** SQLite per run by default (deterministic output, dedup within the file); `--db path` to persist.

## 10. CLI behaviour

- `stdout`: exactly one JSON line per valid input line, `ensure_ascii=False`.
- `stderr`: logs, warnings, malformed-line errors. A bad line never crashes the run.

## 11. Testing strategy (offline, behavioural)

- **Unit:** digit conversion, price parsing table, phone normalization table, grounding check, review rules, dedup matcher.
- **LLM path with a fake client:** valid canned response; non-JSON; schema-invalid; invented price not in text; invented district; wrong enum; timeout → each asserts the *resulting output* (null + flag / fallback).
- **Golden tests:** the 10 sample messages through `RuleExtractor` → assert key fields + reasons (m-003 dup of m-001, m-005 buyer_request, m-007 contradiction, m-010 three listings…).
- **API:** ingest → list → approve flow via `TestClient`.
- **CLI:** run on sample file, assert every stdout line is valid JSON matching the contract and stderr has errors for a malformed line.

## 12. Agent setup

- `CLAUDE.md`: project rules (never-invent rule, stdout/stderr discipline, test command, layout, "update DECISIONS.md when overriding the agent").
- A `/test`-style command and a reviewer sub-agent for diff review before commits.
- `AI_LOG.md` kept during the work: the prompts that mattered, and where the agent was wrong.

## 13. Build order (small commits, ~5h)

1. Scaffold: uv project, CLAUDE.md, `.env.example`, Makefile, empty test.
2. `textnorm`: digits, price, phone — with tests.
3. Models (contract + candidate) — with tests.
4. `RuleExtractor` (classification + extraction + multi-listing split) — golden tests.
5. Review flagging + grounding — tests.
6. SQLite store + dedup — tests.
7. Pipeline + CLI — CLI tests.
8. FastAPI endpoints — API tests.
9. `LlmExtractor` + fake-client tests for malformed/invented output.
10. README (setup, run, cost/latency per message, limitations, next steps), DECISIONS.md, AI_LOG.md.

## 14. Out of scope (deliberately)

Auth, pagination, an admin UI, a full Saudi district database, async/queueing, Docker.

## 15. Open risks

- Rule extractor quality on the hidden set (messages unlike the samples) — mitigated by flagging rather than guessing.
- Gazetteer coverage is small → more `city_missing` flags.
- LLM cost/latency depends on the chosen Qwen model; measure on the samples and report in README.
