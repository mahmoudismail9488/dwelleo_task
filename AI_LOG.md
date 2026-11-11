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
