# DECISIONS

Each decision: what we chose, the alternatives, and why. Places where the coding agent got something wrong are marked **[Agent mistake]**: how I caught it and what I changed.

---

## D1 — LLM-first extraction with a rule-based fallback behind one interface

**Decision.** Extraction sits behind an `Extractor` interface with two real implementations:
- `LlmExtractor`: Qwen through the OpenAI SDK (`base_url`, `model` and key come from env, so any OpenAI-compatible provider works). Used when `LLM_API_KEY` is set.
- `RuleExtractor`: regex plus a small gazetteer. Always available, needs no network.

Both feed the same deterministic layer (normalization → grounding → review flags → dedup), which enforces the "never invent data" rule whichever extractor ran. If the LLM fails (timeout, error, non-JSON, schema-invalid), that message falls back to `RuleExtractor` and gets the review reason `llm_fallback_used`.

**Alternatives considered.**
1. *Rules only*: deterministic, free and easy to test, but brittle on messy and unseen messages, and it skips the LLM-validation problem the brief cares about.
2. *LLM only*: best extraction quality, but the CLI produces nothing without an API key. The reviewers may run the hidden set without one.
3. *Rules primary, LLM optional*: safe, but puts most of the time budget into the weaker extractor.

**Why.**
- **Offline tests:** the rule extractor is tested directly, and the LLM path is tested with a fake client that returns canned and deliberately broken responses. No key or network needed.
- **Hidden set without a key:** the CLI still works. The fallback is allowed to be modest, because when it can't extract a field it returns `null` plus a review reason, which follows the hard rule.
- **Trust lives in one place:** because validation and grounding are shared, an LLM hallucination and a regex bug get caught by the same checks.
- **Reuse:** the normalization helpers (Arabic digits, `950 ألف`, E.164 phones) are needed by the grounding check anyway, so the fallback costs little extra.

**Trade-off accepted.** Two extractors to maintain, and lower quality without a key. Fallback output will carry more review flags.

---

## D2 — Conflicting prices: amount stays `null`, every candidate is kept for review

**Decision.** When a message has price figures that disagree, `price.amount` is `null` and the listing is flagged `price_contradiction`. Every candidate is kept in `price_candidates` with its basis, so the reviewer can choose.

Example m-007: *"900 sqm, SAR 2,500/sqm (total 2.1M)"*. 900 × 2,500 = **2,250,000**, but the stated total is **2,100,000**. Output: `amount: null`, `price_per_sqm: 2500`, `price_candidates: [{2100000, stated_total}, {2250000, computed_from_per_sqm}]`.

**Alternative considered.** Trust the explicit total (2.1M) and still flag it. Brokers' totals are usually the real asking price, while per-sqm figures are often rounded.

**Why.** We can't tell from the message which number is wrong: the per-sqm figure could be rounded, or the total could be a typo. The brief's hard rule says *a wrong price is worse than a missing one*, so we don't guess. Nothing is lost, because both numbers stay visible to the reviewer, and approval is the point where a human settles the price.

**Trade-off accepted.** These listings can't be priced or sorted by price until someone reviews them.

**Addendum (step 5).** *No tolerance:* the stated total has to equal per-sqm × area exactly. The agent proposed a 1% allowance for broker rounding (875 × 2,500 = 2,187,500 against "total 2.2M"). I rejected it: any gap means two numbers disagree, and a human should look. The same treatment applies to two different stated prices in one listing ("price 3.2M, now 2.9M"): both are kept and flagged `price_contradiction`.

---

## D3 — [Agent mistake] Phone finder swallowed neighbouring numbers

**What happened.** The agent's first `find_saudi_mobiles` used a loose pattern (`\d[\d\s\-()]{7,}\d`) so that it could accept spaced formats like `+966 55 123 4567`. All 44 tests passed on the first run.

**How it was caught.** Everything passing on the first try was a reason for suspicion, not confidence. Before committing, the agent was run against adversarial inputs where a number sits right after the phone. `"Call 0551234567 5 BR villa"` returned `[]`: the pattern merged `0551234567 5` into an 11-digit candidate, which was then rejected. On real messages that means a lost phone, so either `contact_phone_missing` or a wrong fallback to the sender's number. The original tests never put a number next to a phone.

**What changed.** The pattern is now shaped like a Saudi mobile: optional `+966`/`00966`/`966`/`0`, then `5X`, then exactly 7 digits, with digit boundaries on both sides. Regression tests cover adjacent numbers, spaced and parenthesised formats, and numbers embedded in longer digit strings.

**Lesson.** Green tests only prove what they test. Each parser now gets at least one "hostile neighbour" case.

---

## D4 — Reading the dot in prices depends on whether there's a suffix

**Decision.** In `parse_amount` (prices only):
- **With a suffix** (`K`, `M`, `million`, `ألف`, `مليون`): the dot is a decimal point. `2.5M` and `2.500M` both give 2,500,000.
- **Without a suffix:** commas are thousands separators, and a dot followed by exact groups of 3 digits is also a thousands separator. `2.500` gives 2,500 and `1.200.000` gives 1,200,000. `3,200,000.50` gives 3,200,000.5 (commas for thousands, dot for decimal).
- **Anything else is ambiguous and returns `None`:** `2.5` and `2.75` with no suffix (probably a missing "M"), and broken grouping like `2.50.000`.

**Alternatives considered.**
1. *A single dot is always a decimal* (the agent's first version). `SAR 2.500` became 2.5 riyals.
2. *Bare `X.YYY` is always ambiguous and becomes `None`* (the agent's second proposal). Safe, but it throws away prices that are almost certainly 2,500.

**Why (my call).** In a price, `2.500` meaning 2.5 riyals is practically impossible, so the thousands reading is the realistic one. Option 1 produced a 1000× wrong price, and option 2 discarded good data. The rule still returns `None` wherever a guess would be needed (`2.5` with no suffix), so it stays within "never invent data".

**Trade-off accepted.** A writer who really meant 2.5 by `2.500` is misread, which is implausible for a price. The rule applies to prices only; areas like `165.5 م²` are parsed with a normal decimal point.

---

## D5 — [Agent mistake] Arabic comma treated as a letter, so 950 ألف became 950 SAR

**What happened.** For word boundaries in Arabic text, the agent used the whole Arabic Unicode block (`؀-ۿ`) as "letters", in both the district/city list and the price-suffix and area-unit patterns. That block also contains punctuation: the Arabic comma `،`, the question mark `؟` and the semicolon `؛`. So in `السعر ٩٥٠ ألف، للتواصل…`, the comma right after `ألف` made the suffix look like part of a longer word. The suffix was dropped and the price was read as **950 SAR instead of 950,000**. In the same message, `الرياض،` wasn't recognized as a city.

**How it was caught.** A new test, `حي النرجس، الرياض`, failed for the district list. The extractor had passed sample m-002 anyway, because the fallback `حي X` pattern happened to rescue the district, and that masked the bug in the sample. Following the failure into `rules.py` turned up the much worse price case, which was then confirmed with a targeted probe before any fix.

**What changed.** Boundaries now use Arabic *letters* only (`AR_LETTERS = ء-يٮ-ۓ`, shared from `gazetteer.py`). Regression tests cover a price followed by `،`, cities followed by `،` and `؟`, and districts followed by `،`.

**Lesson.** Sample messages that pass can hide a bug when a fallback path covers for the main one. Arabic punctuation needs its own test cases, the same way hostile neighbours did for D3.

---

## D6 — The LLM may give a price number, but it must cite it

**Decision.** Each price an extractor reports is a `PriceMention {quote, amount}`: the exact text from the message and the extractor's reading of it. Grounding resolves it:

| Case | Result |
|---|---|
| Quote not in the message | dropped, `price_unverified` |
| Our parser reads the quote and it matches `amount` (or `amount` is empty, as with the rules extractor) | accepted |
| Our parser reads the quote but the LLM's `amount` differs (cites "3.2M", says 2,300,000) | **our parsed value is used**, `price_llm_mismatch` |
| Our parser can't read the quote ("ثلاثة ملايين", "3 million and a half") and an amount is given | **the LLM's amount is kept**, `price.amount` listed in `inferred_fields`, `price_interpreted` |
| Our parser can't read it and no amount is given | dropped, `price_unverified` |

The same applies to `price_per_sqm` and a project's `min_price`.

**Alternative considered.** The agent's first design: the extractor returns only the raw text, and anything it can't quote verbatim, or that our parser can't read, is rejected.

**Why (my call).** The LLM is most useful exactly where regex fails: prices written in words, or unusual formats. A required citation keeps it honest. The quote must exist in the message, so the number can't be made up from nothing, and wherever our parser *can* read the quote, our deterministic value wins. Every case where we rely on the LLM's reading is flagged, so a human confirms it, and the reviewer sees the quote next to the number.

**Trade-off accepted.** An LLM that misreads a price written in words gets through to review with a wrong number attached, but it is always flagged `price_interpreted` and never silently trusted.

---

## D7 — Projects carry their own review reasons

**Decision.** `Project` has `review_reasons` and a calculated `needs_review`, the same as listings: `<field>_unverified` when grounding drops a value, plus `project_name_missing`, `city_missing` and `price_missing` when those core fields are absent.

**Alternative considered.** The agent's first version silently dropped ungrounded project fields, because the brief's contract defines review reasons only for listings.

**Why (my call).** "Every flag gives a reason a human can read" should hold for everything we output. A project with an invented or missing starting price is as untrustworthy as a listing with one.

---

## D8 — Dedup uses exact matches, and conflicting facts block a match

**Decision.** This amends PLAN.md §8:
- **Strong match** (`duplicate_of` set, no flag): same contact phone, property type and district, **and the exact same price or the exact same area**.
- **Weak match** (`duplicate_of` set plus `possible_duplicate`): a different or unknown phone, but the same purpose, type, district, bedrooms **and exact price**. This covers a different broker reposting.
- **Conflicts block:** if both listings state `purpose`, `bedrooms`, `bathrooms`, `area_sqm` or `land_area_sqm` and the values differ, they are never duplicates. A field missing on one side doesn't block.
- `duplicate_of` always points to the earliest original (the root of the chain). Listings from the same message are never compared with each other.

**Alternatives considered.** PLAN.md §8 allowed price within 2% *or* area within 5%, and had no conflict check. The agent recommended keeping the tolerances because reposts often round prices.

**Why (my call).** *Exact only:* this is consistent with D2. Any numeric gap means the listings disagree, and I'd rather miss a rounded repost than merge two different properties. *Conflicts block:* the same broker often posts several similar units in one district. Without the check, two Al Malqa villas at 3.2M (5BR and 6BR) would be merged.

**Trade-off accepted.** Reposts with a rounded price *and* no matching area won't be detected and will appear as separate listings.

---

## D9 — [Agent mistake] Grounding accepted the sender's number as "stated in the message"

**What happened.** The agent's `phone_is_grounded` counted a phone as grounded if it appeared in the text *or* matched the sender. Using the sender is an inference (PLAN.md §5: "fall back to the sender" means `contact_phone_from_sender`). So if the LLM ignored the prompt and copied the sender into `contact_phone_text` for a message with no phone (m-004), the output had the sender's number with **no review reason and nothing in `inferred_fields`**. That's exactly the kind of silent inference the hard rule forbids. The rules extractor never triggered it, because it only reads phones from the text, so no existing test failed.

**How it was caught.** While writing the LLM tests, the agent noticed that one of its own tests (`test_llm_using_sender_as_contact_is_not_trusted_as_stated`) asserted something trivially true and didn't test what its name claimed. Writing the real version (LLM copies the sender for m-004) and running it through the pipeline showed `contact_phone: +966541112222` with reasons `['city_missing', 'area_missing']`, so the inference was unflagged.

**What changed.** `phone_is_grounded` now accepts only phones written in the message text. A copied sender number is rejected (`contact_phone_unverified`), and `review.py` then applies the normal sender fallback with `contact_phone_from_sender` and `"contact_phone"` in `inferred_fields`. The result is the same number, honestly labelled. Regression tests are in `test_grounding.py` and `test_llm_extractor.py`.

**Lesson.** A test whose name promises more than its assertion checks is worse than no test. And "grounded" has to mean "in the text", with no convenient exceptions, because every exception is a path for unflagged inference.

---

## D10 — Settings come from `.env` when present; without it, the rule extractor is used

**Decision.** The CLI, the API and `scripts/measure_llm.py` load `.env` themselves (`app/config.py`, using `python-dotenv`). Values already exported in the shell win over the file. With no `.env`, or an empty `LLM_API_KEY`, everything runs on the rule extractor. The test suite never reads `.env`: `conftest.py` points `ENV_FILE` at a missing file and clears the LLM variables, and subprocesses started by tests inherit that.

**Alternatives considered.**
1. *Makefile `--env-file .env`* (the agent's step-8 version): only `make serve` saw the file, while the CLI and the script silently ignored it.
2. *Plain exported variables only*: consistent, but every run needs `export` first.
3. *Our own `.env` parser* instead of `python-dotenv`: no new dependency, but the edge cases (quotes, comments) would be ours to get right.

**Why (my call).** One behaviour everywhere: `cp .env.example .env`, fill in the key, and every entry point uses the LLM. A fresh clone with no `.env` works out of the box on rules, which is also what the graders get when they run the hidden set. Letting the shell win allows a one-off override (`LLM_MODEL=qwen-turbo uv run normalizer …`).

**Safety detail.** Without the `ENV_FILE` redirect, running `make test` on a developer machine would load the real key and call the paid API from the test suite. `tests/test_config.py` asserts that this can't happen.
