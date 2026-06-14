# agent-architect (7th persona) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 7th always-on persona, `agent-architect`, that researches Google's Agent2Agent (A2A) protocol via arXiv and writes persona proposals into a new `proposal.*` world_state entity.

**Architecture:** Pure-additive, same pattern as the `vulnerability` (threat-intel-agent) and `finding` (ai-research-agent) entity additions: one new entry in `ENTITY_SCHEMAS` (generic validation engine needs zero changes), one new LLM-based agent script reusing `openrouter_agent.py`/`research_agent.py` helpers, docs updates, a new hourly cron slot.

**Tech Stack:** Python 3.12, FastAPI/pydantic backend (`backend/app/world_schema.py`, `backend/app/validation.py`), `arxiv-pp-cli` via `research_agent.search_arxiv`, OpenRouter LLM API via `openrouter_agent.call_openrouter`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-14-agent-architect-design.md`

---

## Task 1: `proposal` entity in `ENTITY_SCHEMAS` + world_schema tests

**Files:**
- Modify: `backend/app/world_schema.py:77-88` (insert new entity before closing `}` of `ENTITY_SCHEMAS`)
- Test: `backend/tests/test_world_schema.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_world_schema.py`:

```python
def test_parse_key_valid_proposal():
    assert parse_key("proposal.2506_01234.status") == KeyParts(
        "proposal", "2506_01234", "status"
    )


def test_get_field_spec_proposal_title():
    assert get_field_spec("proposal", "title") == {"type": "string"}


def test_get_field_spec_proposal_relevance_score():
    assert get_field_spec("proposal", "relevance_score") == {
        "type": "number",
        "min": 0,
        "max": 1,
    }


def test_get_field_spec_proposal_status():
    assert get_field_spec("proposal", "status") == {
        "type": "enum",
        "values": ["proposed", "reviewed", "accepted", "rejected"],
    }


def test_get_field_spec_proposal_context():
    assert get_field_spec("proposal", "context") == {"type": "object"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_world_schema.py -v -k proposal`
Expected: 5 FAIL with `AssertionError` (all return `None` — `"proposal"` not in `ENTITY_SCHEMAS`)

- [ ] **Step 3: Add the `proposal` entity to `ENTITY_SCHEMAS`**

In `backend/app/world_schema.py`, insert before the closing `}` at line 88 (after the `"vulnerability"` block):

```python
    "proposal": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "target_capability": {"type": "string"},
        "source_paper_title": {"type": "string"},
        "source_paper_url": {"type": "string"},
        "relevance_score": {"type": "number", "min": 0, "max": 1},
        "status": {"type": "enum", "values": ["proposed", "reviewed", "accepted", "rejected"]},
        "context": {"type": "object"},
        "fetched_at": {"type": "string"},
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_world_schema.py -v -k proposal`
Expected: 5 PASS

- [ ] **Step 5: Run full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 226 passed (221 existing + 5 new)

- [ ] **Step 6: Commit**

```bash
git add backend/app/world_schema.py backend/tests/test_world_schema.py
git commit -m "feat: add proposal entity to world schema"
```

---

## Task 2: Domain validation tests for `proposal`

**Files:**
- Test: `backend/tests/test_domain_validation.py`

No production code changes — `check_domain_consistency` is fully generic over `ENTITY_SCHEMAS` (already true for `vulnerability`/`finding`). These tests verify the new entity is validated correctly.

- [ ] **Step 1: Write the tests**

Append to `backend/tests/test_domain_validation.py`:

```python
def test_set_proposal_title_valid():
    op = WorldOp(op="set", key="proposal.2506_01234.title", value="capability-broker-agent")
    assert check_domain_consistency(op, None) == (True, None)


def test_set_proposal_status_valid():
    op = WorldOp(op="set", key="proposal.2506_01234.status", value="proposed")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_proposal_invalid_status():
    op = WorldOp(op="set", key="proposal.2506_01234.status", value="implemented")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "'status' must be one of ['proposed', 'reviewed', 'accepted', 'rejected'], got 'implemented'"


def test_rejects_proposal_relevance_score_out_of_range():
    op = WorldOp(op="set", key="proposal.2506_01234.relevance_score", value=1.5)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "value 1.5 for 'relevance_score' above maximum 1"


def test_merge_on_proposal_context_valid():
    op = WorldOp(
        op="merge",
        key="proposal.2506_01234.context",
        value={"consulted": ["finding.2601_03236"], "rationale": "extends agent memory work"},
    )
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_proposal_unknown_field():
    op = WorldOp(op="set", key="proposal.2506_01234.unknown_field", value="x")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown field 'unknown_field' for entity 'proposal'"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_domain_validation.py -v -k proposal`
Expected: 6 PASS (no production code needed — generic engine already handles it)

- [ ] **Step 3: Run full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 232 passed (226 from Task 1 + 6 new)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_domain_validation.py
git commit -m "test: add domain validation coverage for proposal entity"
```

---

## Task 3: `proposal.*` block in `ENTITY_SCHEMA_TEXT`

**Files:**
- Modify: `scripts/agents/openrouter_agent.py:60-69`

This is the LLM-facing schema description used by the 3 LLM personas (`sre-agent`/`deploy-agent`/`alert-agent` via `openrouter_agent.py`, and `ai-research-agent`/`agent-architect` which import `ENTITY_SCHEMA_TEXT` indirectly through the same module) so they're aware of the new namespace.

- [ ] **Step 1: Add the `proposal.*` block**

In `scripts/agents/openrouter_agent.py`, after the `vulnerability.<id>.fetched_at  string` line (line 68) and before the blank line + `Valid ops:` line (line 70-71), insert:

```
proposal.<id>.title                 string
proposal.<id>.summary               string
proposal.<id>.target_capability     string
proposal.<id>.source_paper_title    string
proposal.<id>.source_paper_url      string
proposal.<id>.relevance_score       number 0-1
proposal.<id>.status                enum: proposed, reviewed, accepted, rejected
proposal.<id>.context               object
proposal.<id>.fetched_at            string
```

So the file reads (around lines 65-72):

```python
vulnerability.<id>.affected_service  string
vulnerability.<id>.url               string
vulnerability.<id>.fetched_at        string

proposal.<id>.title                 string
proposal.<id>.summary               string
proposal.<id>.target_capability     string
proposal.<id>.source_paper_title    string
proposal.<id>.source_paper_url      string
proposal.<id>.relevance_score       number 0-1
proposal.<id>.status                enum: proposed, reviewed, accepted, rejected
proposal.<id>.context               object
proposal.<id>.fetched_at            string

Valid ops: {"op": "set"|"increment"|"merge", "key": "<entity>.<id>.<field>", "value": ...}
"""
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `cd /root/insidedcpulse-world-model && python3 -c "import sys; sys.path.insert(0, 'scripts/agents'); from openrouter_agent import ENTITY_SCHEMA_TEXT; assert 'proposal.<id>.status' in ENTITY_SCHEMA_TEXT; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/openrouter_agent.py
git commit -m "docs: add proposal entity to ENTITY_SCHEMA_TEXT"
```

---

## Task 4: `scripts/agents/agent_architect.py`

**Files:**
- Create: `scripts/agents/agent_architect.py`

Mirrors `scripts/agents/ai_research_agent.py` structure. Not unit-tested (same as `ai_research_agent.py`/`threat_intel_agent.py`) — verified via live runs in Task 7/8.

- [ ] **Step 1: Write the file**

```python
#!/usr/bin/env python3
"""LLM-based agent-architect: researches Google's Agent2Agent (A2A) protocol
via arXiv and proposes new InsideDCPulse agent personas that would implement
or apply A2A concepts, writing proposal.* world_state entries."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from openrouter_agent import (
    DEFAULT_MODEL,
    call_openrouter,
    ensure_agent,
    evaluate_vision,
    get_world_state,
    load_env,
    propose_vision,
)
from research_agent import sanitize_id, search_arxiv

ENV_PATH = Path("/root/insidedcpulse-secrets/agents/agent-architect.env")
PROPOSAL_FIELDS = [
    "title", "summary", "target_capability", "source_paper_title",
    "source_paper_url", "relevance_score", "status", "context", "fetched_at",
]
MAX_PROPOSAL_ENTRIES = 10
RELEVANCE_THRESHOLD = 0.3
A2A_QUERY = "Agent2Agent protocol"
CONTEXT_ENTITIES = ("finding", "research")
MAX_CONTEXT_ENTRIES = 5

SYSTEM_PROMPT = """You are the Agent Architect persona for InsideDCPulse, an
event-sourced world model for multi-LLM agents. InsideDCPulse currently runs
6 always-on agent personas: sre-agent, deploy-agent, alert-agent,
research-agent, ai-research-agent, threat-intel-agent.

Your job is to study Google's Agent2Agent (A2A) protocol via the arXiv
candidates below, and propose ONE new agent persona that would implement or
apply an A2A concept (e.g. capability advertisement, agent discovery, task
delegation, negotiation between agents) within InsideDCPulse's existing
world_state + knowledge graph architecture.

You may also see recent research/finding entries already known to the
system — avoid proposing something that duplicates them, and reference
relevant ones by id in "consulted" if they inform your proposal.

Respond with ONLY a JSON object:
{"chosen_index": <int> | null, "title": <string>, "summary": <string>, "target_capability": <string>, "relevance_score": <number 0-1>, "rationale": <string>, "consulted": [<string ids>]}
Set "chosen_index" to null if none of the candidate papers describe an A2A
concept applicable to this system. No prose, no markdown fences."""


def _collect_context_entries(state: dict, entity: str) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for key, value in state.items():
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != entity:
            continue
        entry = entries.setdefault(parts[1], {})
        if parts[2] in ("title", "topic", "topics", "fetched_at"):
            entry[parts[2]] = value["value"]
    return entries


def build_context_lines(state: dict) -> list[str]:
    combined = []
    for entity in CONTEXT_ENTITIES:
        for entity_id, entry in _collect_context_entries(state, entity).items():
            if "fetched_at" not in entry or "title" not in entry:
                continue
            combined.append((entry["fetched_at"], f"{entity}.{entity_id}", entry))
    combined.sort(key=lambda item: item[0], reverse=True)
    lines = []
    for _, full_id, entry in combined[:MAX_CONTEXT_ENTRIES]:
        topic = entry.get("topic") or entry.get("topics") or ""
        lines.append(f"- {full_id}: {entry['title']} (topic: {topic})")
    return lines


def build_user_prompt(candidates: list[dict], context_lines: list[str]) -> str:
    lines = ["Candidate A2A papers:"]
    for idx, paper in enumerate(candidates):
        lines.append(
            f"{idx}. title: {paper['title']}\n"
            f"   published: {paper['published']}\n"
            f"   url: {paper['link']}\n"
            f"   summary: {paper['summary'][:500]}"
        )
    lines.append("")
    if context_lines:
        lines.append("Existing research/finding entries already known to the system:")
        lines.extend(context_lines)
    else:
        lines.append("No existing research/finding entries yet.")
    lines.append("")
    lines.append("Respond with the JSON object described in the system prompt.")
    return "\n".join(lines)


def main() -> None:
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ENV_PATH
    env = load_env(env_path)

    openrouter_key = env.get("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        print(f"OPENROUTER_API_KEY missing from {env_path}")
        sys.exit(1)
    model = env.get("OPENROUTER_MODEL") or DEFAULT_MODEL

    agent_id, agent_api_key = ensure_agent(env, env_path)
    print(f"== agent: {agent_id} ==")

    world_state = get_world_state(agent_api_key)
    state = world_state["state"]

    existing_ids: dict[str, str] = {}
    for key, value in state.items():
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "proposal" and parts[2] == "fetched_at":
            existing_ids[parts[1]] = value["value"]

    papers = search_arxiv(A2A_QUERY, max_results=10)

    candidates = []
    for paper in papers:
        candidate_id = sanitize_id(paper["id"])
        if candidate_id not in existing_ids:
            candidates.append(paper)

    if not candidates:
        print("All returned A2A papers already in proposal.* — no-op this cycle.")
        return

    context_lines = build_context_lines(state)
    system_msg = SYSTEM_PROMPT
    user_msg = build_user_prompt(candidates, context_lines)

    print("== OpenRouter response ==")
    llm = call_openrouter(openrouter_key, model, system_msg, user_msg)
    print(json.dumps(llm, indent=2))

    chosen_index = llm.get("chosen_index")
    if not isinstance(chosen_index, int) or not (0 <= chosen_index < len(candidates)):
        print("LLM found no sufficiently relevant A2A paper — no-op this cycle.")
        return

    relevance_score = float(llm.get("relevance_score", 0))
    if relevance_score < RELEVANCE_THRESHOLD:
        print(f"Relevance score {relevance_score} below threshold {RELEVANCE_THRESHOLD} — no-op this cycle.")
        return

    chosen = candidates[chosen_index]
    new_id = sanitize_id(chosen["id"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = llm.get("title", "")
    summary = llm.get("summary", "")
    target_capability = llm.get("target_capability", "")
    rationale = llm.get("rationale", "")
    consulted = llm.get("consulted") or []

    ops = [
        {"op": "set", "key": f"proposal.{new_id}.title", "value": title},
        {"op": "set", "key": f"proposal.{new_id}.summary", "value": summary},
        {"op": "set", "key": f"proposal.{new_id}.target_capability", "value": target_capability},
        {"op": "set", "key": f"proposal.{new_id}.source_paper_title", "value": chosen["title"]},
        {"op": "set", "key": f"proposal.{new_id}.source_paper_url", "value": chosen["link"]},
        {"op": "set", "key": f"proposal.{new_id}.relevance_score", "value": relevance_score},
        {"op": "set", "key": f"proposal.{new_id}.status", "value": "proposed"},
        {"op": "set", "key": f"proposal.{new_id}.fetched_at", "value": now},
        {"op": "merge", "key": f"proposal.{new_id}.context", "value": {"consulted": consulted, "rationale": rationale}},
    ]

    if len(existing_ids) + 1 > MAX_PROPOSAL_ENTRIES:
        oldest_id = min(existing_ids, key=lambda k: existing_ids[k])
        for field in PROPOSAL_FIELDS:
            ops.append({"op": "delete", "key": f"proposal.{oldest_id}.{field}"})

    payload = {
        "description": f"Propose new persona '{title}' from A2A paper '{chosen['title']}' ({new_id})",
        "ops": ops,
        "metadata": {
            "source": "agent-architect",
            "kind": "persona_proposal",
            "target_capability": target_capability,
            "relevance_score": relevance_score,
        },
    }

    print("== evaluate ==")
    evaluation = evaluate_vision(agent_api_key, payload)
    print(json.dumps(evaluation, indent=2))

    if not evaluation.get("would_accept"):
        print("Validator would reject this vision — not proposing.")
        return

    print("== propose_vision ==")
    result = propose_vision(agent_api_key, payload)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable and verify it compiles**

Run: `chmod +x scripts/agents/agent_architect.py && python3 -m py_compile scripts/agents/agent_architect.py && echo ok`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/agent_architect.py
git commit -m "feat: add agent-architect persona (A2A protocol research)"
```

---

## Task 5: README.md updates

**Files:**
- Modify: `README.md:113` (schema table)
- Modify: `README.md:147-149` (graph node types list)
- Modify: `README.md:375-382` (Always-on personas list)

- [ ] **Step 1: Add `proposal` row to the world state schema table**

In `README.md`, after line 113 (`| vulnerability | ... |`), add:

```markdown
| `proposal` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `target_capability` (string), `source_paper_title` (string), `source_paper_url` (string), `relevance_score` (number, 0-1), `status` (enum: `proposed`\|`reviewed`\|`accepted`\|`rejected`), `context` (object), `fetched_at` (string) |
```

- [ ] **Step 2: Add `proposal` to the graph node types list**

In `README.md` line 147-149, change:

```markdown
- **Node types**: `agent`, `event`, plus one per `world_state` entity
  (`region`, `service`, `incident`, `deployment`, `team`, `alert`,
  `research`, `finding`, `vulnerability`).
```

to:

```markdown
- **Node types**: `agent`, `event`, plus one per `world_state` entity
  (`region`, `service`, `incident`, `deployment`, `team`, `alert`,
  `research`, `finding`, `vulnerability`, `proposal`).
```

- [ ] **Step 3: Add `agent-architect` to the Always-on personas list**

In `README.md`, change the section heading and intro at lines 350-355 from "Six hourly cron jobs" to "Seven hourly cron jobs", and append a new bullet after the `threat-intel-agent` bullet (after line 382):

```markdown
- `agent-architect` (`:30`) — OpenRouter LLM persona. Searches arXiv for
  "Agent2Agent protocol" papers and proposes one new InsideDCPulse persona
  per run into `proposal.*` (title, summary, target capability, source
  paper, relevance score, rationale + consulted `finding`/`research` ids in
  `context`), evicting the oldest entry once more than 10 are present.
  `status` always starts `"proposed"` (future review states are reserved for
  human/agent triage, not written by this agent). Spec:
  `docs/superpowers/specs/2026-06-14-agent-architect-design.md`.
```

Also update line 352 `Six hourly cron jobs` -> `Seven hourly cron jobs`.

- [ ] **Step 4: Verify markdown table row count / grep sanity**

Run: `grep -c '^| \`' README.md && grep -c 'agent-architect' README.md`
Expected: schema table row count increased by 1 vs before this task; `agent-architect` count >= 1

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document agent-architect persona and proposal entity"
```

---

## Task 6: `llms.txt` rotating-feeds section

**Files:**
- Modify: `docker/nginx/static/llms.txt:112-120` (after the `vulnerability.*` paragraph)

- [ ] **Step 1: Add the `proposal.*` paragraph**

In `docker/nginx/static/llms.txt`, after the `vulnerability.*` paragraph (ends at line 120 with "Also capped at 10 entries, oldest evicted first."), insert:

```markdown

`proposal.*` holds a fourth rotating feed: candidate new agent personas
implementing concepts from Google's Agent2Agent (A2A) protocol, refreshed
hourly by the LLM-based `agent-architect` persona —
`proposal.<id>.title`, `.summary`, `.target_capability`,
`.source_paper_title`, `.source_paper_url`, `.relevance_score` (0-1),
`.status` (`proposed`|`reviewed`|`accepted`|`rejected`, always `proposed`
from this agent), `.context` (`{consulted: [...], rationale: ...}`), and
`.fetched_at`. Also capped at 10 entries, oldest evicted first.
```

- [ ] **Step 2: Verify**

Run: `grep -c "proposal.<id>" docker/nginx/static/llms.txt`
Expected: output >= 1

- [ ] **Step 3: Commit**

```bash
git add docker/nginx/static/llms.txt
git commit -m "docs: add proposal.* feed to llms.txt"
```

---

## Task 7: Merge, push, deploy

**Files:** none (git/ops only)

- [ ] **Step 1: Merge feature branch to `main`**

If working in a worktree on branch `feature/agent-architect-persona`, fast-forward merge to local `main` (no PR needed if `main` has no new commits since the branch diverged — check with `git log main..feature/agent-architect-persona --oneline` and `git log feature/agent-architect-persona..main --oneline`; if the latter is non-empty, rebase first).

```bash
git checkout main
git merge --ff-only feature/agent-architect-persona
```

- [ ] **Step 2: Verify full backend suite on `main`**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 232 passed

- [ ] **Step 3: Push to `origin/main`**

Get a fresh GitHub PAT from the user if needed (verify via `curl -H "Authorization: Bearer $TOK" https://api.github.com/user` first — see memory notes on PAT churn). Push:

```bash
git -c credential.helper= push -u "https://x-access-token:<TOKEN>@github.com/insidedcpulse-spec/insidedcpulse-world-model.git" main
git config --local branch.main.remote origin
```

- [ ] **Step 4: Verify webhook deploy + smoke**

Wait ~10s for the webhook to fire, then:

```bash
curl -s localhost:9001/smoke | python3 -m json.tool
```

Expected: all 6 checks `"ok": true`.

---

## Task 8: Env file, cron entry, and live verification

**Files:**
- Create: `/root/insidedcpulse-secrets/agents/agent-architect.env` (gitignored, not in repo)
- Modify: root crontab (`crontab -e` / `crontab -l` + `crontab -`)

- [ ] **Step 1: Create the env file**

```bash
cat > /root/insidedcpulse-secrets/agents/agent-architect.env <<'EOF'
AGENT_NAME=agent-architect
OPENROUTER_API_KEY=<copy value from /root/insidedcpulse-secrets/agents/ai-research-agent.env>
OPENROUTER_MODEL=openai/gpt-oss-120b:free
EOF
chmod 600 /root/insidedcpulse-secrets/agents/agent-architect.env
```

(Copy the actual `OPENROUTER_API_KEY` value from an existing persona env file, e.g.
`grep OPENROUTER_API_KEY /root/insidedcpulse-secrets/agents/ai-research-agent.env`.)

- [ ] **Step 2: Run the agent once manually**

```bash
cd /root/insidedcpulse-world-model/scripts/agents
python3 agent_architect.py /root/insidedcpulse-secrets/agents/agent-architect.env
```

Expected: prints `== agent: agent-architect-xxxxxx ==` (self-registration), then either:
- a no-op message ("no A2A papers"/"no sufficiently relevant"/"below threshold"), or
- `== evaluate ==` + `== propose_vision ==` with `would_accept: true` and a queued event id.

Either outcome is valid — re-run up to 3 times if every run is a no-op, to get at least one
positive case for Step 3.

- [ ] **Step 3: Verify `proposal.*` in world_state (if a proposal was accepted)**

```bash
source /root/insidedcpulse-secrets/agents/agent-architect.env
curl -s -H "X-API-Key: $AGENT_API_KEY" https://insidedcpulse.com/api/v1/world/state | python3 -c "
import json,sys
state = json.load(sys.stdin)['state']
for k,v in sorted(state.items()):
    if k.startswith('proposal.'):
        print(k, '=', v['value'])
"
```

Expected: 9 `proposal.<id>.*` keys, `status == proposed`, `relevance_score` between 0 and 1,
`context` is a dict with `consulted` (list) and `rationale` (string).

- [ ] **Step 4: Add the `:30` cron entry**

```bash
(crontab -l; echo "30 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/agent_architect.py /root/insidedcpulse-secrets/agents/agent-architect.env >> /root/insidedcpulse-secrets/agents/logs/agent-architect.log 2>&1") | crontab -
```

- [ ] **Step 5: Verify cron entry**

Run: `crontab -l | grep agent-architect`
Expected: one line, minute field `30`

- [ ] **Step 6: Verify graph projection picked up the new entity (if a proposal was accepted in Step 2)**

```bash
source /root/insidedcpulse-secrets/agents/agent-architect.env
curl -s -H "X-API-Key: $AGENT_API_KEY" "https://insidedcpulse.com/api/v1/graph/node/proposal.<id>" | python3 -m json.tool
```

(Replace `<id>` with the proposal id from Step 3.) Expected: 200, `node.type == "proposal"`,
incoming `PROPOSED`/`AFFECTED` edges present (same as any other entity — confirms the graph
projection's generic entity-node handling covers `proposal` with zero code changes, as
designed).

No further steps — this task has no git commit (env file is gitignored, crontab is host
state, not repo state).
