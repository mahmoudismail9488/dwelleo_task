# AI_LOG

Key agent sessions, in order. Tool: Claude Code (VS Code extension). Prompts are quoted as I typed them; the agent's answers are summarized.

---

## Session 1: Understanding the brief and planning (before any code)

**Goal:** understand the task and the trade-offs before planning, then write PLAN.md together with the agent.

### Prompts that mattered

1. > Read "./task.md" file ... I want to discuss and learn some points from you before we start planning

   The agent summarized what's being graded: the agent workflow, PLAN before code, honest decisions, small commits. It walked through the traps in the 10 sample messages: m-001/m-003 duplicate, m-004 ambiguous "Al Olaya", m-005 buyer request containing a price, m-006 ranges and "from" prices, m-007 price contradiction, m-008 price on request, m-010 several listings in one message.

2. > how can I make it offline if I will use llm in the workflow? ... the test suite he mentioned means which test suite? ... what are stdin, stdout, stderr and how can I make CLI setup

   Learned: put the LLM behind an interface and use a fake client in tests. "Test suite" means **my** tests; the hidden set is their evaluation of my CLI. stdout should carry only JSON lines, and logs go to stderr so `> out.jsonl` stays clean. The agent also pointed out a risk I hadn't thought of: the graders may run the hidden set **without an API key**.

3. > so if I made interface and two extractors (llm, regex) that will fix both problems? test offline and no API key?

   Confirmed, with a distinction: the fake extractor exists only in tests, while the regex extractor is a real production fallback. The shared validation layer is what actually enforces "never invent data". I chose **option 2: LLM primary, minimal honest regex fallback** → [D1](DECISIONS.md).

4. > perfect let's plan ... add our 1st decision

   The agent asked about stack, LLM provider and project output. My choices: Python + FastAPI; **Qwen via the OpenAI SDK** (I changed the agent's suggested provider); projects return `listings: []` plus a separate `project` object with name, city, min price, min/max bedrooms, delivery date and payment terms (I changed the agent's suggestion of one flagged listing record).

### Where I overrode or corrected the agent

- **Studio bedrooms:** the agent proposed `null`. I chose `0`. The agent pointed out that my reason ("studios sometimes have one bedroom") argued for using a stated count when there is one, so the final rule is: stated count wins, otherwise `0`, marked as inferred.
- **Area:** the agent made `area_sqm` depend on context. I set it to always mean built-up area, with a separate `land_area_sqm`. Land is the exception, since it has no built-up area.
- **m-007 (land, price contradiction):** I asked whether land should be its own object like projects. The agent argued it should stay a listing (one concrete property). I then questioned nulling the price when the message states "total 2.1M". After seeing both options, I chose to null it and keep both candidates → [D2](DECISIONS.md).
- Confirmed `price_computed` (total = per-sqm × area only when both are stated, flagged), city inference only for unambiguous districts, and the sender's phone as fallback when the text has no phone.

---

## Session 2: Agent setup, scaffold and `textnorm`

**Goal:** set up CLAUDE.md, scaffold the project, and build the normalization helpers (step 2 of PLAN.md §13).

### Prompts that mattered

1. > let's work on claude.md — it mustn't take any decisions until I review — it must comply with the task.md file regarding how the project will be run and how the tests will be done

   The agent used the `claude-md-architect` skill. The result is a "Working agreement" section (the agent proposes 2–3 options with a recommendation and then stops; it commits only when asked; it never pushes), plus the task.md rules: one command for tests, one to start the service, offline behavioural tests, stdout carrying only JSON. This rule shaped the rest of the session: every policy question below came back to me instead of being decided silently.

2. > okay use httpx, I know some libraries are basic like "requests" and others support fingerprints and impersonating etc, can you explain

   A detour to learn about HTTP clients (requests/httpx/aiohttp) and TLS/HTTP2 fingerprinting and impersonation (curl_cffi). Conclusion: not relevant here. `httpx` is only a test dependency for FastAPI's `TestClient`.

3. Step 2, `textnorm`: Arabic-Indic and Persian digits, amount parsing, Saudi mobile numbers to E.164.

### Where the agent was wrong or I overrode it

- **[Agent mistake] Phone finder** → [D3](DECISIONS.md). All tests passed on the first run. The agent then probed with a number right after the phone and found `"Call 0551234567 5 BR"` lost the phone completely. Fixed with a pattern shaped like a Saudi mobile number, plus regression tests.
- **Reading the dot in prices: I overrode the agent twice** → [D4](DECISIONS.md).
  - Agent v1: a single dot is always a decimal, so `SAR 2.500` = 2.5 riyals.
  - Agent v2: bare `X.YYY` is ambiguous, so it becomes `None`.
  - My rule: *with a suffix (M/K/مليون) the dot is a decimal; without one, a dot followed by groups of 3 digits is a thousands separator.* My reasoning: `2.500` meaning 2.5 riyals is impossible for a price, and v2 threw away good data. The agent agreed and added the edge cases (`2.5` with no suffix → `None`, `3,200,000.50`, broken grouping).
- Approved: `httpx` as a dev dependency, `qwen-plus` on the international endpoint as the `.env.example` default, and accepting mobile numbers only (landlines → `contact_phone_invalid`).

---

## Session 3: Extractor, grounding, dedup, pipeline, API, LLM (steps 4–10)

**Goal:** build the rest of PLAN.md §13. The working agreement held throughout: before each step, the agent listed the open decisions (usually 2–4, with a recommendation) and stopped. I answered, and the agent built, tested, probed, and drafted the DECISIONS entry before proposing commits.

### Prompts that mattered

1. **Rule extractor decisions** (asked before coding): infer rent from a yearly price + flag; shared phone/purpose in multi-listing messages applies to all with no flag; capture unknown districts from "in X" / "حي X"; precedence buyer > project > listing. All four were the agent's recommendations, which I accepted.
2. > where is "or best offer"

   I questioned a test input that wasn't in the brief. The agent explained it was a made-up hostile probe for "two different prices in one listing". That led to the `price_texts` list, later `PriceMention`, so both prices are kept for review instead of being dropped.
3. > okay, now the limitation of cities and districts is because we're in the rules layer not the llm, right?

   Only partly. The LLM removes the *recognition* limit, but the gazetteer stays the single controlled source for *inference* (a city from a district) and for canonical names used in dedup, so the LLM can't fill in cities from world knowledge.
4. > yes add review reason to the project, I need no tolerance to be honest regarding the llm I believe it can help us more if we allowed to get the number but cited

   This turned into D6 (cited prices), D7 (project review reasons) and the D2 addendum (no tolerance). It's the most important design input I gave: the LLM may return numbers, but every price must carry a verbatim quote that our parser checks.
5. > now if the user downloads the repo from github it will be with no .env file, how can he run the script and what will happen

   The agent simulated a fresh clone in a scratch folder instead of answering from memory. Everything worked on rules except the measurement script, which crashed with a raw `KeyError`; that was fixed. Then:
   > now I want everything read secrets from the ".env" but if not exists so we depend on rule extractor

   → D10 (python-dotenv, the shell wins, tests isolated from `.env` via `ENV_FILE`).
6. > why don't we make a table for llm runs, add to it every run "msg_id" is a must and add cost(input, output) and latency … in the llm extractor … without any measuring scripts

   followed by
   > is the cost in the llm response?

   (No: the response has tokens only, so cost = tokens × price from env.) → D11, the `llm_runs` table, and the script was deleted.

### Where the agent was wrong or I overrode it

- **[Agent mistake] Arabic comma** → [D5](DECISIONS.md). `٩٥٠ ألف،` was read as 950 SAR. A failing district test led to it, and the sample m-002 had masked it.
- **[Agent mistake] Sender accepted as a grounded phone** → [D9](DECISIONS.md). Found because one of the agent's own tests was empty and didn't check what its name said.
- **Agent bug, caught by the live run:** a failed LLM call reported the *previous* message's tokens and latency (m-007 showed m-006's exact numbers). Fixed with a fresh `LlmRun` per call and a regression test.
- **Overrides of the agent's recommendations:** no 1% price tolerance (D2); exact-only dedup instead of the plan's 2%/5% (D8); cited LLM numbers instead of raw-text-only prices (D6); review reasons on projects (D7); `.env` read by every entry point instead of only the Makefile (D10); an `llm_runs` table instead of a benchmark script (D11).
- **Agent bug, caught by running the README:** before finishing the docs, the agent ran each documented command on a simulated fresh clone. `normalizer process … --db data/listings.db` crashed (`sqlite3.OperationalError`), because SQLite won't create a missing folder and only the API path created `data/`. Fixed in `Store` with a regression test.
- **Process slip:** the deletion of `tests/test_cli_args.py` was staged early and landed in the pipeline commit instead of the CLI commit. Left as is.

### Live LLM result

First run, qwen3.8-max at a 20s timeout: 8/10 ok, ≈ $0.0042 and ≈ 12s per message, no `_unverified` flags (the citation prompt held). m-007 and m-010 timed out and fell back to rules.

> i update the timeout to 60 and model still the same

Second run at 60s: **10/10 ok**, ≈ $0.0055 and ≈ 17s per message (m-010: 54s, $0.014). The LLM now handles the contradiction and the multi-listing message. The defaults moved to 60s.

- **[Agent mistake] LLM-inferred purpose passed grounding** → [D13](DECISIONS.md). Reading the second run line by line, m-010 had `purpose: rent` with no `purpose_inferred`, while the rules path flagged it. Grounding only checked that enum values were *valid*, not *stated*. Fixed with keyword cues per value.
- **[Agent mistake] Projects ignored the sender-phone policy** → [D12](DECISIONS.md). **Caught by me**, posting m-006 through `/docs` and seeing `contact_phone: null` on both paths.

> create messages.jsonl so I can test, it must contain 50 test messages that handle all the cases and expected hidden messages from the interview panel

- The agent wrote 50 messages, each with a `note` on the expected result, and diffed the rules output against the notes. → [D14](DECISIONS.md): 6 bugs, the worst being a **UAE number read as a Saudi one** (wrong contact phone). I approved three extraction changes (a bare `60,000` counts as a price, "for sale" needs a real-estate signal, buyer cues) and rejected a fourth (no bedrooms flag for commercial units).
