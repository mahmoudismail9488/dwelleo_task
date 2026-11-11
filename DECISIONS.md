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
