# Listing Inbox Normalizer

Take-home for dwelleo: a service that turns messy broker WhatsApp messages (English/Arabic/mixed) into structured, trustworthy property listings, exposed as a CLI and an HTTP API. The brief is `task.md`; the agreed plan is `PLAN.md`.

## Working agreement (read first)

**You don't make decisions. The user does.** Propose, then wait for explicit approval before acting on any of these:
- anything not already specified in `PLAN.md` or `DECISIONS.md`
- field policies, output contract fields, review-reason names, dedup rules or thresholds
- new dependencies, a new module or file layout change, a schema or table change
- deviating from `PLAN.md`, even when you think it's better

How to propose: give 2–3 options, your recommendation, and the trade-off, then **stop**. Once approved, draft the `DECISIONS.md` entry for the user to review.

- Commit only when the user asks. Show the message first. Small, single-purpose commits (the graders read the history). Never push.
- When the user corrects you, offer an entry for `AI_LOG.md`. Agent mistakes also go in `DECISIONS.md`, marked **[Agent mistake]**.
- If something in `task.md` and `PLAN.md` conflict, stop and ask.

## Stack

- Python 3.12+, `uv` for env/deps
- FastAPI + Uvicorn (HTTP), Pydantic v2 (contract + LLM-output validation)
- SQLite via stdlib `sqlite3`, no ORM, no external services
- LLM: Qwen via the **OpenAI SDK** (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` from env)
- pytest + FastAPI `TestClient`

## Commands

Created by the scaffold step. Each one is required by `task.md`: one command runs the tests and one starts the service.
```bash
uv sync                                     # install
make test                                   # run the full test suite (offline, no API key)
make serve                                  # start the HTTP API
uv run normalizer process messages.jsonl    # CLI: one JSON result per message on stdout
```

## Architecture

```
text → pre-normalize → Extractor (LlmExtractor | RuleExtractor) → validate+ground → normalize → review flags → dedup → store
```
- `app/extractors/`: `LlmExtractor` is used when `LLM_API_KEY` is set. `RuleExtractor` is the always-available fallback, and also takes over for a message when the LLM fails (`llm_fallback_used`).
- Everything **after** the extractor is shared and deterministic. That is where "never invent data" is enforced (`grounding.py`, `review.py`). Never put trust logic inside an extractor.
- `app/pipeline.py: process_message()` is the single entry point used by both `cli.py` and `api.py`.
- Field policies (areas, studio, city inference, price conflicts, sender phone) are in `PLAN.md` §5. Follow them exactly.

## Do / Don't

- ❌ **Never invent data.** A value not in the message is `null` plus a review reason. A wrong price is worse than a missing one.
- ✅ Every inferred value is listed in `inferred_fields` **and** gets a review reason (`city_inferred`, `price_computed`, `contact_phone_from_sender`…).
- ✅ Every LLM-returned value must pass the grounding check (it appears in the digit-normalized text). If it doesn't, it becomes `null` + `<field>_unverified`.
- ❌ Never print anything but result JSON to **stdout** in the CLI. Logs and errors go to stderr. A malformed input line must not crash the run.
- ✅ `json.dumps(..., ensure_ascii=False)` and `encoding="utf-8"` everywhere Arabic text flows.
- ❌ Tests never hit the network or need a key. Use the fake LLM client and the fixtures in `tests/fixtures/`.
- ✅ Tests assert **behaviour**: exact output values and review reasons, not "it ran".
- ❌ Buyer requests and irrelevant messages return `listings: []` even when they contain prices (m-005). Projects return `listings: []` plus a `project` object.
- ❌ Don't edit `PLAN.md`. It's frozen as the pre-implementation plan. Record changes in `DECISIONS.md`.
- ❌ Never commit `.env` or secrets. New env vars go in `.env.example`.

## Gotchas

- Arabic-Indic digits (`٠-٩`) and Persian digits (`۰-۹`) must be converted before any regex or grounding check.
- Saudi mobiles normalize to `+9665XXXXXXXX` from `05…`, `5…`, `966…`, `00966…`, `+966 5X XXX XXXX`.
- The CLI uses an in-memory SQLite per run by default (dedup within the file). `--db path` persists.

## More detail

- Brief and output contract: `task.md`
- Plan, field policies, review-reason vocabulary, dedup rules: `PLAN.md`
- Decisions and agent mistakes: `DECISIONS.md`
- Prompt history: `AI_LOG.md`
