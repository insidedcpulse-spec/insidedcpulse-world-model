# ai-research-agent (5th persona) — Design

## Why

The existing `research-agent` persona is deterministic, no-LLM, and pulls one
SRE/ops-relevant arXiv paper per hour into `research.*` (rotating through 5
fixed SRE/ops topics). The user wants a **second, complementary** research
feed focused on **AI systems research** — papers that could inform
InsideDCPulse's own architecture (event-sourced systems, multi-agent
coordination, agent memory, planning, tool-use agents, world models) — with
LLM-driven relevance scoring, an explanation of why each paper matters for
InsideDCPulse, and one architectural "insight" per run.

This is **additive**: `research-agent` (SRE/ops, deterministic, `research.*`,
`:50` cron) is unchanged. `ai-research-agent` is a new 5th persona, new
entity `finding.*`, new `:40` cron slot.

## Scope

- New script: `scripts/agents/ai_research_agent.py`
- New world_state entity: `finding` (pure-additive to `ENTITY_SCHEMAS`)
- New cron job: `:40 * * * *`
- New secret file: `/root/insidedcpulse-secrets/agents/ai-research-agent.env`
- Docs: README schema table + personas section, `llms.txt` scenario description

**Non-goals**: no changes to `research-agent`, `research.*`, or the existing
3 LLM personas (sre/deploy/alert-agent). No new MCP tool — `finding.*` is
readable via the existing `get_world_state` / `get_world_memory` tools.

## world_state schema: `finding` entity

New entry in `backend/app/world_schema.py::ENTITY_SCHEMAS`, id regex
`^[a-z0-9_]{1,32}$` (same `sanitize_id` as `research-agent`, derived from the
arXiv id):

```python
"finding": {
    "title": {"type": "string"},
    "summary": {"type": "string"},
    "url": {"type": "string"},
    "topics": {"type": "string"},
    "relevance_score": {"type": "number", "min": 0, "max": 1},
    "why_it_matters": {"type": "string"},
    "source": {"type": "string"},
    "fetched_at": {"type": "string"},
    "notes": {"type": "object"},
},
```

- `topics`: comma-separated string (e.g. `"agent memory, world models"`) —
  arrays aren't a supported field type, so the LLM's `topics: [...]` list is
  joined with `", "` before being set.
- `notes`: object, holds the run's mandatory insight as
  `{"insight": "<text>"}` via a `merge` op — same pattern as
  `incident.<id>.notes`.
- `source`: always `"arxiv"`.

FIFO eviction: same as `research.*` — when adding a new `finding.<id>` would
push the count above **10**, delete all fields of the oldest entry (by
`fetched_at`).

### `event_type` / `metadata` mapping

The server requires `propose_vision`'s top-level `event_type` to be the
literal `"vision"` (existing constraint, unchanged). The user's requested
`"research_paper_found"` shape is preserved inside `metadata`:

```json
{
  "description": "Add arXiv finding '<title>' (<id>) — relevance 0.82",
  "ops": [ ... ],
  "metadata": {
    "source": "ai-research-agent",
    "kind": "research_paper_found",
    "topic": "<rotated topic>",
    "relevance_score": 0.82
  }
}
```

## Script design: `scripts/agents/ai_research_agent.py`

New file, analogous in shape to `research_agent.py` but with an LLM step
between arXiv search and `ops` construction. The LLM **never produces `ops`
directly** — it only scores/filters candidates and writes prose fields; the
script deterministically maps those fields onto `finding.<id>.*` ops. This
avoids the LLM hallucinating invalid op shapes/schema.

**Reused from `openrouter_agent.py`**: `load_env`, `save_env`, `ensure_agent`,
`call_openrouter`, `_parse_json_content`, `evaluate_vision`, `propose_vision`.

**Reused from `research_agent.py`**: `sanitize_id`, `search_arxiv` (and its
`ATOM_NS` Atom-parsing logic).

### Topics (rotating, `TOPIC_INDEX` in env — same pattern as research-agent)

```python
TOPICS = [
    "event-sourced AI systems",
    "multi-agent coordination",
    "agent memory architectures",
    "LLM planning systems",
    "autonomous tool-using agents",
    "world models for artificial intelligence",
]
```

### Flow

1. Load env, `ensure_agent` (self-registers `ai-research-agent` on first
   run, same as other personas).
2. Rotate `TOPIC_INDEX` (mod 6), pick `topic`.
3. `get_world_state` → collect existing `finding.<id>.fetched_at` keys into
   `existing_ids: dict[str, str]` (id -> fetched_at), same shape/fix as
   `research_agent.py`'s `existing_ids[parts[1]] = value["value"]`.
4. `search_arxiv(topic, max_results=10)`.
5. Filter out candidates whose `sanitize_id` is already in `existing_ids`.
6. If no candidates remain after filtering: print
   `"All returned papers already in finding.* — no-op this cycle."` and
   return (no LLM call, no-op — same shape as research-agent's existing
   no-op path).
7. Build LLM prompt (system + user messages) with the remaining candidates
   (`title`, `summary` truncated to ~500 chars, `published`, arXiv `link`,
   indexed 0..N-1) plus the filter rules below. Call `call_openrouter`.
8. Parse LLM JSON response (shape below). If `chosen_index` is `null` OR
   `relevance_score < 0.3`: print
   `"LLM found no sufficiently relevant candidate — no-op this cycle."` and
   return (no-op, no proposal).
9. Build `ops` deterministically from the chosen candidate + LLM fields (see
   "ops construction" below).
10. If `len(existing_ids) + 1 > 10`: append delete ops for the oldest
    `finding.<id>` (by `fetched_at`), same FIFO pattern as research-agent.
11. `evaluate_vision` → if `would_accept`, `propose_vision`. Same
    print/early-return shape as both existing agent scripts.

### LLM prompt

System message:

```
You are the AI Research Intelligence persona for InsideDCPulse, an
event-sourced world model for multi-LLM agents.

Your job is NOT to summarize random papers. Your job is to find research
that could improve InsideDCPulse's own architecture: event-sourced AI
systems, multi-agent coordination, agent memory architectures, planning
systems for LLMs, autonomous tool-using agents, and world models for AI.

Mandatory filters:
- Ignore papers that are purely biomedical/clinical.
- Ignore science unrelated to AI systems.
- Prioritize system-level AI papers over narrow ML benchmarks.
- Prioritize recent research (last 2-3 years).
- Prioritize topics like autonomous agents, agent memory, planning,
  multi-agent orchestration.

Never include medical or clinical interpretations. Focus exclusively on AI
systems design. Always prioritize architectural impact over descriptive
summary.

Respond with ONLY a JSON object:
{
  "chosen_index": <int, index into the candidate list> | null,
  "topics": [<string>, ...],
  "relevance_score": <number 0-1>,
  "why_it_matters_for_inside_dcpulse": <string>,
  "insight": <string>
}
Set "chosen_index" to null if none of the candidates meaningfully match the
filters above. No prose, no markdown fences.
```

User message: numbered list of candidates (`index`, `title`, `summary`,
`published`, `url`), plus the current rotated `topic` as a hint.

`insight` is the mandatory final insight: "how this paper could improve
InsideDCPulse's architecture, OR which system component it inspires (agent,
memory, validation, coordination, etc.)" — generated for the *chosen* paper.
If `chosen_index` is `null`, `insight` is ignored (no proposal happens).

### `ops` construction (deterministic, in script)

```python
chosen = candidates[llm["chosen_index"]]
new_id = sanitize_id(chosen["id"])
topics_str = ", ".join(llm["topics"])

ops = [
    {"op": "set", "key": f"finding.{new_id}.title", "value": chosen["title"]},
    {"op": "set", "key": f"finding.{new_id}.summary", "value": chosen["summary"][:500]},
    {"op": "set", "key": f"finding.{new_id}.url", "value": chosen["link"]},
    {"op": "set", "key": f"finding.{new_id}.topics", "value": topics_str},
    {"op": "set", "key": f"finding.{new_id}.relevance_score", "value": llm["relevance_score"]},
    {"op": "set", "key": f"finding.{new_id}.why_it_matters", "value": llm["why_it_matters_for_inside_dcpulse"]},
    {"op": "set", "key": f"finding.{new_id}.source", "value": "arxiv"},
    {"op": "set", "key": f"finding.{new_id}.fetched_at", "value": now},
    {"op": "merge", "key": f"finding.{new_id}.notes", "value": {"insight": llm["insight"]}},
]
```

(plus FIFO delete ops for the oldest entry, if applicable)

## Cron + secrets

New crontab line:

```
40 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/ai_research_agent.py /root/insidedcpulse-secrets/agents/ai-research-agent.env >> /root/insidedcpulse-secrets/agents/logs/ai-research-agent.log 2>&1
```

New secret file `/root/insidedcpulse-secrets/agents/ai-research-agent.env`
(chmod 600, gitignored, not in repo) — same shape as `sre-agent.env` etc:

```
OPENROUTER_API_KEY=<copy from another persona's env or shared key>
OPENROUTER_MODEL=<same default as other personas, e.g. nex-agi/nex-n2-pro:free>
AGENT_NAME=ai-research-agent
TOPIC_INDEX=0
```

(`AGENT_ID` / `AGENT_API_KEY` are populated on first run by `ensure_agent`,
same as all other personas.)

## Tests

Pure-additive, following the existing `research` entity test pattern:

- `backend/tests/test_world_schema.py`: `parse_key("finding.<id>.title")`,
  `get_field_spec("finding", "relevance_score") == {"type":"number","min":0,"max":1}`,
  `get_field_spec("finding", "notes") == {"type":"object"}`.
- `backend/tests/test_domain_validation.py`:
  - valid `set` on `finding.<id>.title`, `.relevance_score` (in range),
    `.topics` (string)
  - reject `set finding.<id>.relevance_score` with value `1.5` (out of
    range)
  - reject unknown field `finding.<id>.unknown_field`
  - reject `merge` on `finding.<id>.title` (enum/scalar-like field — merge
    only valid on `notes`)
  - valid `merge` on `finding.<id>.notes`

## Docs updates

- `README.md`: add `finding` row to the world state schema table (same
  format as the `research` row); add `ai-research-agent` (`:40`) to the
  "Always-on personas" section, describing it as the LLM-based AI-systems
  counterpart to the deterministic SRE/ops `research-agent`.
- `docker/nginx/static/llms.txt`: add a paragraph alongside the existing
  `research.*` description, describing `finding.*` (`finding.<id>.title`,
  `.summary`, `.url`, `.topics`, `.relevance_score`, `.why_it_matters`,
  `.source`, `.fetched_at`, `.notes.insight`) as the AI-systems-research
  counterpart feed, also FIFO-capped at 10.

## Open risks / notes for implementer

- The LLM may return `chosen_index` pointing at a filtered-out index if it
  miscounts — script should bounds-check `chosen_index` against
  `len(candidates)` and treat out-of-range as `null` (no-op), not crash.
- `relevance_score` from the LLM could come back as a string (`"0.8"`) —
  coerce with `float()` before the range check / op value, matching how
  numeric JSON fields are sometimes stringified by LLMs in
  `response_format: json_object` mode.
- Reuse the **fixed** `existing_ids[parts[1]] = value["value"]` pattern from
  `research_agent.py` (not the original buggy `= value` — see
  `project_insidedcpulse` memory for why).
