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
