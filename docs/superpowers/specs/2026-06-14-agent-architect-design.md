# agent-architect (7th persona) — design spec

Date: 2026-06-14

## Summary

A 7th always-on persona, `agent-architect` — LLM-based (OpenRouter, same
class as `ai-research-agent`). Its job is to research Google's
**Agent2Agent (A2A) protocol** via arXiv and propose new InsideDCPulse agent
personas that would implement or apply A2A concepts to this system. Each run
writes at most one `proposal.<id>.*` entry to `world_state` (new entity,
FIFO-evicted at 10) describing a candidate persona: what capability it would
add, which paper inspired it, and why it's relevant.

This is the first persona whose output is about the *agent-persona system
itself* (meta: agents proposing new agents), rather than about external
infrastructure/research feeds. It closes a loop with `ai-research-agent` and
`research-agent`: those personas already populate `finding.*`/`research.*`
with general AI-systems papers; `agent-architect` searches specifically for
A2A-protocol material and cross-references the existing `finding.*`/
`research.*` entries as context for its proposals.

"A2A" here means Google's **Agent2Agent protocol** specifically (agent
discovery/capability advertisement/inter-agent task delegation), not generic
multi-agent coordination.

## Data source

Same `arxiv-pp-cli` / `search_arxiv()` helper already used by
`research_agent.py` / `ai_research_agent.py` (reused via import, no new CLI
work). Query: `search_arxiv("Agent2Agent protocol", max_results=10)`. Tested
live during brainstorming — returns real recent papers, e.g. "Agent
Communications toward Agentic AI at Edge -- A Case Study of the Agent2Agent
Protocol" and "Capability Advertisement as a Market for Lemons".

Unlike `ai-research-agent` (6-topic rotation), `agent-architect` always
searches the same fixed query — A2A protocol is a narrow, specific topic, not
one that benefits from rotation.

## World schema: new `proposal` entity

Added to `ENTITY_SCHEMAS` in `backend/app/world_schema.py`, key pattern
`proposal.<id>.<field>` (id = sanitized arXiv id, same `sanitize_id()` as
`research`/`finding`):

| Field | Type | Source / derivation |
|---|---|---|
| `title` | string | LLM-proposed persona name/title (e.g. `"capability-broker-agent"`) |
| `summary` | string | LLM-proposed short description of what the persona would do |
| `target_capability` | string | Short label for the A2A concept it implements (e.g. `"capability advertisement"`, `"task delegation"`) |
| `source_paper_title` | string | Title of the arXiv paper that inspired the proposal |
| `source_paper_url` | string | arXiv link for that paper |
| `relevance_score` | number 0-1 | LLM-assigned relevance of the proposal |
| `status` | enum `[proposed, reviewed, accepted, rejected]` | Agent always writes `"proposed"` — other values are for future human/agent review, never written by this agent |
| `context` | object | `{"consulted": [<finding/research ids>], "rationale": <string>}` — free-form, not a `REFERENCE_FIELDS` member |
| `fetched_at` | string | ISO 8601 UTC timestamp, same as other personas |

`PROPOSAL_FIELDS` constant lists these 9 fields, used for FIFO-eviction
delete ops (`MAX_PROPOSAL_ENTRIES = 10`), same pattern as
`FINDING_FIELDS`/`MAX_FINDING_ENTRIES` in `ai_research_agent.py`.

**No graph `REFERENCES` edges for `proposal.*`** (explicit scope decision):
`context.consulted` is a free-form list of ids inside a `merge` object field,
not a top-level string field matching `REFERENCE_FIELDS`
(`affected_service`/`affected_region`/`source_service`/`target_service`).
Adding a new reference-field convention for this is YAGNI — the
`finding.*`/`research.*` cross-reference is informational context for the
LLM prompt, not a graph relationship.

## Agent script: `scripts/agents/agent_architect.py`

Mirrors `ai_research_agent.py` structure (imports from `openrouter_agent.py`
+ `research_agent.py`):

1. Load env (`/root/insidedcpulse-secrets/agents/agent-architect.env`),
   `ensure_agent` (self-registers `agent-architect-xxxxxx` on first run).
2. `get_world_state`, collect existing `proposal.<id>` ids + `fetched_at`
   (for FIFO, same as `finding.*`).
3. `search_arxiv("Agent2Agent protocol", max_results=10)`.
4. Filter candidates: drop any whose `sanitize_id(paper["id"])` is already in
   `proposal.*`. If none remain, print "no new A2A papers" and exit 0 (same
   no-op pattern as `ai_research_agent`).
5. Read up to 5 `finding.*`/`research.*` entries with the most recent
   `fetched_at` from `world_state` (same `state` dict already fetched in step
   2) — their `title`/`topic`/`topics` fields passed into the prompt as
   "existing research context" so the LLM can reference them in
   `context.consulted` if relevant (and avoid proposing something already
   covered).
6. Build prompt (system prompt below) with the candidate papers + existing
   context; LLM call returns ONE JSON object:
   `{"chosen_index": <int>|null, "title": <string>, "summary": <string>,
   "target_capability": <string>, "relevance_score": <number 0-1>,
   "rationale": <string>, "consulted": [<ids>]}`.
7. If `chosen_index` is `null` or out of range -> "no sufficiently relevant
   A2A paper" -> no-op, exit 0 (same as `ai_research_agent`).
8. If `relevance_score < RELEVANCE_THRESHOLD` (0.3, same threshold as
   `ai-research-agent`) -> no-op, exit 0.
9. Build `ops`: 8 `set` ops for the 8 scalar `proposal.<id>.*` fields, plus 1
   `merge` op for `context = {"consulted": [...], "rationale": ...}`. Field
   sources:
    - `title`, `summary`, `target_capability`, `relevance_score` — from the
      LLM response (`llm["title"]`, etc).
    - `source_paper_title`, `source_paper_url` — from the **chosen candidate
      paper** (`candidates[chosen_index]["title"]`/`["link"]`), not the LLM.
    - `status` — hardcoded `"proposed"`.
    - `fetched_at` — `datetime.now(timezone.utc)`, same format as other
      personas.
    - `context.consulted` — `llm["consulted"]`; `context.rationale` —
      `llm["rationale"]`.
10. If `len(existing) + 1 > MAX_PROPOSAL_ENTRIES`, append `delete` ops for
    the oldest entry's 9 `PROPOSAL_FIELDS` (oldest = min `fetched_at`, same
    pattern as `ai_research_agent`).
11. `evaluate_vision` -> if `would_accept`, `propose_vision`; else print and
    no-op.

`event_type: "vision"`, `metadata: {"source": "agent-architect", "kind":
"persona_proposal", "target_capability": ..., "relevance_score": ...}`.

### System prompt (draft)

```
You are the Agent Architect persona for InsideDCPulse, an event-sourced
world model for multi-LLM agents. InsideDCPulse currently runs 6 always-on
agent personas: sre-agent, deploy-agent, alert-agent, research-agent,
ai-research-agent, threat-intel-agent.

Your job is to study Google's Agent2Agent (A2A) protocol via the arXiv
candidates below, and propose ONE new agent persona that would implement or
apply an A2A concept (e.g. capability advertisement, agent discovery, task
delegation, negotiation between agents) within InsideDCPulse's existing
world_state + knowledge graph architecture.

You may also see recent research/finding entries already known to the system
— avoid proposing something that duplicates them, and reference relevant
ones by id in "consulted" if they inform your proposal.

Respond with ONLY a JSON object:
{"chosen_index": <int>|null, "title": <string>, "summary": <string>,
"target_capability": <string>, "relevance_score": <number 0-1>,
"rationale": <string>, "consulted": [<string ids>]}
Set "chosen_index" to null if none of the candidate papers describe an A2A
concept applicable to this system. No prose, no markdown fences.
```

## Cron

New staggered slot **`:30`** (verified free — busy minutes currently
5,15,20,35,40,50):

```
30 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/agent_architect.py /root/insidedcpulse-secrets/agents/agent-architect.env >> /root/insidedcpulse-secrets/agents/logs/agent-architect.log 2>&1
```

## Env file

New `/root/insidedcpulse-secrets/agents/agent-architect.env` (chmod 600, not
committed), same shape as `ai-research-agent.env`: `AGENT_NAME=agent-architect`,
reuses existing `OPENROUTER_API_KEY`/`OPENROUTER_MODEL` (default
`openai/gpt-oss-120b:free`, same as the other 3 LLM personas after the
2026-06-12 model-deviation). Self-registers `agent_id`/`agent_api_key` on
first run (written back to the same env file by `ensure_agent`/`save_env`).

## Docs updates (pure-additive, same pattern as prior entity expansions)

- `backend/app/world_schema.py`: add `"proposal"` to `ENTITY_SCHEMAS` (9
  fields per table above).
- `scripts/agents/openrouter_agent.py`: add `proposal.<id>.*` block to
  `ENTITY_SCHEMA_TEXT`.
- `README.md`: add `proposal` row to the world state schema table + add
  `agent-architect` to the persona list (7 personas total), cron table gets
  `:30`.
- `docker/nginx/static/llms.txt`: add `proposal.*` to the rotating-feeds
  description alongside `research.*`/`finding.*`/`vulnerability.*`.

## Testing

Same precedent as `vulnerability`/`finding` entity additions
(`test_world_schema.py` + `test_domain_validation.py`, pure-additive, zero
validation-engine changes since `ENTITY_SCHEMAS` is fully generic):

- `test_world_schema.py`: assert `"proposal"` in `ENTITY_SCHEMAS` with the 9
  fields/types/enum above (incl. `status` enum values and `relevance_score`
  0-1 bounds).
- `test_domain_validation.py`: valid `set`/`merge` ops for each field type
  (string/enum/number/object), invalid `status` enum value rejected,
  `relevance_score` out-of-range (e.g. 1.5) rejected.

`scripts/agents/agent_architect.py` itself is **not** unit-tested (same as
`ai_research_agent.py`/`threat_intel_agent.py` — no `backend/tests` coverage
for `scripts/agents/`). Verified via live runs instead:

1. Run manually once, registers `agent-architect-xxxxxx`, proposes 1
   `proposal.*` entry (or no-ops if `chosen_index: null` /
   `relevance_score < 0.3` — both valid outcomes, re-run if needed to get a
   positive case for verification).
2. Verify via `get_world_state`: 9 fields present, correct types,
   `status == "proposed"`, `context.consulted` is a list.
3. Re-run enough times to exercise FIFO eviction at 10 entries (watch for the
   `min(existing_ids, key=...)` dict-vs-value bug class already fixed once in
   `research_agent.py` — `agent_architect.py` must store `value["value"]`,
   not the whole dict, in `existing_ids`, same as the fixed `ai_research_agent.py`).

## Out of scope / explicitly not doing

- Do NOT modify `ai_research_agent.py`'s or `research_agent.py`'s `TOPICS`
  lists — `agent-architect`'s own fixed A2A query is the "agents working
  together" mechanism, via shared `world_state` reads (cross-referencing
  `finding.*`/`research.*` as context).
- No graph `REFERENCES` edges for `proposal.*` (see World schema section).
- No automated implementation of proposed personas — `proposal.*` is purely
  informational/advisory, `status` stays `"proposed"` forever from this
  agent's perspective (future human or agent review could update it, but
  that's out of scope here).
- No new arXiv CLI work — reuses existing `arxiv-pp-cli`/`search_arxiv`.
