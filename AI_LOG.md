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
