# ai-research-agent (5th persona) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 5th always-on persona, `ai-research-agent`, that pulls AI-systems-research arXiv papers, scores their relevance to InsideDCPulse's own architecture via LLM, and writes `finding.*` world_state entries with a per-run architectural insight.

**Architecture:** Pure-additive `finding` entity in `ENTITY_SCHEMAS` (backend), plus a new standalone script `scripts/agents/ai_research_agent.py` reusing `openrouter_agent.py` helpers (LLM call, agent registration, evaluate/propose) and `research_agent.py` helpers (`sanitize_id`, `search_arxiv`). The LLM only scores/filters candidates and writes prose fields — the script deterministically builds `ops`. New `:40` hourly cron slot.

**Tech Stack:** Python 3, FastAPI/Pydantic backend (pytest), `arxiv-pp-cli`, OpenRouter API, cron.

---

## File Structure

- Modify: `backend/app/world_schema.py` — add `finding` entity to `ENTITY_SCHEMAS`
- Modify: `backend/tests/test_world_schema.py` — schema-level tests for `finding`
- Modify: `backend/tests/test_domain_validation.py` — op-validation tests for `finding`
- Modify: `README.md` — schema table row + personas section
- Modify: `docker/nginx/static/llms.txt` — `finding.*` feed description
- Create: `scripts/agents/ai_research_agent.py` — new persona script
- Create (outside repo, gitignored): `/root/insidedcpulse-secrets/agents/ai-research-agent.env`
- System: root crontab — new `:40` line

---

### Task 1: `finding` entity schema + validation tests

**Files:**
- Modify: `backend/app/world_schema.py:58-66`
- Test: `backend/tests/test_world_schema.py`
- Test: `backend/tests/test_domain_validation.py`

- [ ] **Step 1: Write failing schema tests**

Append to `backend/tests/test_world_schema.py` (end of file, after `test_get_field_spec_research_fetched_at`):

```python
def test_parse_key_valid_finding():
    assert parse_key("finding.2506_01234.title") == KeyParts("finding", "2506_01234", "title")


def test_get_field_spec_finding_relevance_score():
    assert get_field_spec("finding", "relevance_score") == {"type": "number", "min": 0, "max": 1}


def test_get_field_spec_finding_notes():
    assert get_field_spec("finding", "notes") == {"type": "object"}
```

- [ ] **Step 2: Write failing domain-validation tests**

Append to `backend/tests/test_domain_validation.py` (end of file, after `test_rejects_merge_on_research_title`):

```python
def test_set_finding_title_valid():
    op = WorldOp(op="set", key="finding.2506_01234.title", value="A Paper About Agent Memory")
    assert check_domain_consistency(op, None) == (True, None)


def test_set_finding_relevance_score_valid():
    op = WorldOp(op="set", key="finding.2506_01234.relevance_score", value=0.82)
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_finding_relevance_score_above_max():
    op = WorldOp(op="set", key="finding.2506_01234.relevance_score", value=1.5)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "value 1.5 for 'relevance_score' above maximum 1"


def test_rejects_finding_unknown_field():
    op = WorldOp(op="set", key="finding.2506_01234.unknown_field", value="x")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown field 'unknown_field' for entity 'finding'"


def test_rejects_merge_on_finding_title():
    op = WorldOp(op="merge", key="finding.2506_01234.title", value={"x": 1})
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'merge' not allowed on field 'title' (type 'string')"


def test_merge_on_finding_notes_valid():
    op = WorldOp(op="merge", key="finding.2506_01234.notes", value={"insight": "use event sourcing for agent memory"})
    assert check_domain_consistency(op, None) == (True, None)
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
cd backend
.venv/bin/pytest tests/test_world_schema.py tests/test_domain_validation.py -v -k finding
```
Expected: `FAILED` for all `finding`-named tests — `parse_key` returns `None` / `get_field_spec` returns `None` because `finding` is not yet in `ENTITY_SCHEMAS`, so `check_domain_consistency` returns `(False, "unknown key namespace 'finding...'")` for every case.

- [ ] **Step 4: Add the `finding` entity to `ENTITY_SCHEMAS`**

In `backend/app/world_schema.py`, after the `"research"` entry (which ends at line 65 with `},`) and before the closing `}` of `ENTITY_SCHEMAS` (line 66), insert:

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

So the end of `ENTITY_SCHEMAS` reads:

```python
    "research": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "topic": {"type": "string"},
        "published": {"type": "string"},
        "url": {"type": "string"},
        "fetched_at": {"type": "string"},
    },
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
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd backend
.venv/bin/pytest tests/test_world_schema.py tests/test_domain_validation.py -v
```
Expected: all tests `PASSED` (including the pre-existing `research`/`alert`/etc. tests — confirms the addition is purely additive).

- [ ] **Step 6: Commit**

```bash
git add backend/app/world_schema.py backend/tests/test_world_schema.py backend/tests/test_domain_validation.py
git commit -m "feat: add finding entity schema for ai-research-agent"
```

---

### Task 2: README schema table + personas section

**Files:**
- Modify: `README.md:98` (schema table)
- Modify: `README.md` (Always-on personas section, ~line 299-314)

- [ ] **Step 1: Add `finding` row to the world state schema table**

In `README.md`, after the `research` row (line 98):

```markdown
| `research` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `topic` (string), `published` (string), `url` (string), `fetched_at` (string) |
```

add immediately below it:

```markdown
| `finding` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `url` (string), `topics` (string), `relevance_score` (number, 0-1), `why_it_matters` (string), `source` (string), `fetched_at` (string), `notes` (object) |
```

- [ ] **Step 2: Document the `ai-research-agent` persona**

In `README.md`, the "### Always-on personas" section currently ends with:

```markdown
- `research-agent` (`:50`) — deterministic, no LLM. Pulls one new SRE/ops
  paper per run from arXiv (via `arxiv-pp-cli`, rotating through a fixed
  topic list) into `research.*`, evicting the oldest entry once more than 10
  are present. Spec:
  `docs/superpowers/specs/2026-06-13-arxiv-research-agent-design.md`.
```

Also update the intro sentence "Four hourly cron jobs..." to "Five hourly cron jobs...". Append a new bullet after the `research-agent` bullet:

```markdown
- `ai-research-agent` (`:40`) — OpenRouter LLM persona, the AI-systems-research
  counterpart to `research-agent`. Rotates through 6 AI-systems topics
  (event-sourced AI, multi-agent coordination, agent memory, LLM planning,
  tool-use agents, world models), pulls arXiv candidates via `arxiv-pp-cli`,
  has the LLM pick the most architecturally relevant one (or none), and
  writes it to `finding.*` with `relevance_score`, `why_it_matters`, and an
  `insight` in `notes`. Evicts the oldest entry once more than 10 are
  present. Spec:
  `docs/superpowers/specs/2026-06-13-ai-research-agent-design.md`.
```

The full section should read:

```markdown
### Always-on personas

Five hourly cron jobs each run one propose/evaluate/accept cycle against the
live REST API, using `openrouter_agent.py`'s self-registration and
evaluate/propose flow. Per-persona secrets live in
`/root/insidedcpulse-secrets/agents/*.env` (gitignored, not in repo):

- `sre-agent` (`:05`), `deploy-agent` (`:20`), `alert-agent` (`:35`) — OpenRouter
  LLM personas focused on `team`/`incident`, `deployment`/`service`, and
  `alert`/`region` respectively. Spec:
  `docs/superpowers/specs/2026-06-12-specialized-agent-personas-design.md`.
- `research-agent` (`:50`) — deterministic, no LLM. Pulls one new SRE/ops
  paper per run from arXiv (via `arxiv-pp-cli`, rotating through a fixed
  topic list) into `research.*`, evicting the oldest entry once more than 10
  are present. Spec:
  `docs/superpowers/specs/2026-06-13-arxiv-research-agent-design.md`.
- `ai-research-agent` (`:40`) — OpenRouter LLM persona, the AI-systems-research
  counterpart to `research-agent`. Rotates through 6 AI-systems topics
  (event-sourced AI, multi-agent coordination, agent memory, LLM planning,
  tool-use agents, world models), pulls arXiv candidates via `arxiv-pp-cli`,
  has the LLM pick the most architecturally relevant one (or none), and
  writes it to `finding.*` with `relevance_score`, `why_it_matters`, and an
  `insight` in `notes`. Evicts the oldest entry once more than 10 are
  present. Spec:
  `docs/superpowers/specs/2026-06-13-ai-research-agent-design.md`.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document finding entity and ai-research-agent persona"
```

---

### Task 3: `llms.txt` — describe the `finding.*` feed

**Files:**
- Modify: `docker/nginx/static/llms.txt`

- [ ] **Step 1: Add a paragraph describing `finding.*`**

In `docker/nginx/static/llms.txt`, after the existing paragraph (which ends with):

```markdown
In addition to the incident scenario above, `research.*` holds a rotating
feed of SRE/ops-relevant arXiv papers (`research.<id>.title`, `.summary`,
`.topic`, `.published`, `.url`, `.fetched_at`), refreshed hourly by the
deterministic `research-agent` persona — up to 10 entries, oldest evicted
first.
```

and before `## Get your own agent identity`, insert a new paragraph:

```markdown

`finding.*` holds a second, complementary rotating feed: AI-systems-research
arXiv papers relevant to InsideDCPulse's own architecture (event-sourced
systems, multi-agent coordination, agent memory, LLM planning, tool-use
agents, world models), refreshed hourly by the LLM-based `ai-research-agent`
persona — `finding.<id>.title`, `.summary`, `.url`, `.topics`,
`.relevance_score` (0-1), `.why_it_matters`, `.source`, `.fetched_at`, and
`.notes.insight` (one architectural insight per finding). Also capped at 10
entries, oldest evicted first.
```

- [ ] **Step 2: Commit**

```bash
git add docker/nginx/static/llms.txt
git commit -m "docs: describe finding.* feed in llms.txt"
```

---

### Task 4: `ai_research_agent.py` script

**Files:**
- Create: `scripts/agents/ai_research_agent.py`

- [ ] **Step 1: Write the script**

Create `scripts/agents/ai_research_agent.py`:

```python
#!/usr/bin/env python3
"""LLM-based ai-research-agent: finds AI-systems-research arXiv papers
relevant to InsideDCPulse's own architecture, scores relevance via LLM, and
writes finding.* world_state entries with a per-run architectural insight."""

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
    save_env,
)
from research_agent import sanitize_id, search_arxiv

ENV_PATH = Path("/root/insidedcpulse-secrets/agents/ai-research-agent.env")
FINDING_FIELDS = [
    "title", "summary", "url", "topics", "relevance_score",
    "why_it_matters", "source", "fetched_at", "notes",
]
MAX_FINDING_ENTRIES = 10
RELEVANCE_THRESHOLD = 0.3

TOPICS = [
    "event-sourced AI systems",
    "multi-agent coordination",
    "agent memory architectures",
    "LLM planning systems",
    "autonomous tool-using agents",
    "world models for artificial intelligence",
]

SYSTEM_PROMPT = """You are the AI Research Intelligence persona for InsideDCPulse, an
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
{"chosen_index": <int> | null, "topics": [<string>, ...], "relevance_score": <number 0-1>, "why_it_matters_for_inside_dcpulse": <string>, "insight": <string>}
Set "chosen_index" to null if none of the candidates meaningfully match the
filters above. No prose, no markdown fences."""


def build_user_prompt(topic: str, candidates: list[dict]) -> str:
    lines = [f"Rotated topic hint for this run: {topic}", "", "Candidates:"]
    for idx, paper in enumerate(candidates):
        lines.append(
            f"{idx}. title: {paper['title']}\n"
            f"   published: {paper['published']}\n"
            f"   url: {paper['link']}\n"
            f"   summary: {paper['summary'][:500]}"
        )
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
        if len(parts) == 3 and parts[0] == "finding" and parts[2] == "fetched_at":
            existing_ids[parts[1]] = value["value"]

    topic_index = int(env.get("TOPIC_INDEX", "0"))
    topic = TOPICS[topic_index % len(TOPICS)]
    env["TOPIC_INDEX"] = str((topic_index + 1) % len(TOPICS))
    save_env(env_path, env)
    print(f"== topic: {topic} ==")

    papers = search_arxiv(topic, max_results=10)

    candidates = []
    for paper in papers:
        candidate_id = sanitize_id(paper["id"])
        if candidate_id not in existing_ids:
            candidates.append(paper)

    if not candidates:
        print("All returned papers already in finding.* — no-op this cycle.")
        return

    system_msg = SYSTEM_PROMPT
    user_msg = build_user_prompt(topic, candidates)

    print("== OpenRouter response ==")
    llm = call_openrouter(openrouter_key, model, system_msg, user_msg)
    print(json.dumps(llm, indent=2))

    chosen_index = llm.get("chosen_index")
    if not isinstance(chosen_index, int) or not (0 <= chosen_index < len(candidates)):
        print("LLM found no sufficiently relevant candidate — no-op this cycle.")
        return

    relevance_score = float(llm.get("relevance_score", 0))
    if relevance_score < RELEVANCE_THRESHOLD:
        print(f"Relevance score {relevance_score} below threshold {RELEVANCE_THRESHOLD} — no-op this cycle.")
        return

    chosen = candidates[chosen_index]
    new_id = sanitize_id(chosen["id"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    topics_str = ", ".join(llm.get("topics") or [])
    why_it_matters = llm.get("why_it_matters_for_inside_dcpulse", "")
    insight = llm.get("insight", "")

    ops = [
        {"op": "set", "key": f"finding.{new_id}.title", "value": chosen["title"]},
        {"op": "set", "key": f"finding.{new_id}.summary", "value": chosen["summary"][:500]},
        {"op": "set", "key": f"finding.{new_id}.url", "value": chosen["link"]},
        {"op": "set", "key": f"finding.{new_id}.topics", "value": topics_str},
        {"op": "set", "key": f"finding.{new_id}.relevance_score", "value": relevance_score},
        {"op": "set", "key": f"finding.{new_id}.why_it_matters", "value": why_it_matters},
        {"op": "set", "key": f"finding.{new_id}.source", "value": "arxiv"},
        {"op": "set", "key": f"finding.{new_id}.fetched_at", "value": now},
        {"op": "merge", "key": f"finding.{new_id}.notes", "value": {"insight": insight}},
    ]

    if len(existing_ids) + 1 > MAX_FINDING_ENTRIES:
        oldest_id = min(existing_ids, key=lambda k: existing_ids[k])
        for field in FINDING_FIELDS:
            ops.append({"op": "delete", "key": f"finding.{oldest_id}.{field}"})

    payload = {
        "description": f"Add arXiv finding '{chosen['title']}' ({new_id}) — relevance {relevance_score}",
        "ops": ops,
        "metadata": {
            "source": "ai-research-agent",
            "kind": "research_paper_found",
            "topic": topic,
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

- [ ] **Step 2: Make it executable and syntax-check it**

Run:
```bash
chmod +x scripts/agents/ai_research_agent.py
python3 -m py_compile scripts/agents/ai_research_agent.py
```
Expected: no output (compiles cleanly).

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/ai_research_agent.py
git commit -m "feat: add ai-research-agent persona script"
```

---

### Task 5: Secrets env file for `ai-research-agent`

**Files:**
- Create (outside repo, not committed): `/root/insidedcpulse-secrets/agents/ai-research-agent.env`

- [ ] **Step 1: Create the env file by copying credentials from an existing persona**

```bash
mkdir -p /root/insidedcpulse-secrets/agents/logs
SRC=/root/insidedcpulse-secrets/agents/sre-agent.env
OPENROUTER_KEY=$(grep '^OPENROUTER_API_KEY=' "$SRC" | cut -d= -f2-)
OPENROUTER_MODEL_VAL=$(grep '^OPENROUTER_MODEL=' "$SRC" | cut -d= -f2-)
cat > /root/insidedcpulse-secrets/agents/ai-research-agent.env <<EOF
OPENROUTER_API_KEY=${OPENROUTER_KEY}
OPENROUTER_MODEL=${OPENROUTER_MODEL_VAL}
AGENT_NAME=ai-research-agent
TOPIC_INDEX=0
EOF
chmod 600 /root/insidedcpulse-secrets/agents/ai-research-agent.env
```

- [ ] **Step 2: Verify the file**

Run:
```bash
ls -l /root/insidedcpulse-secrets/agents/ai-research-agent.env
grep -v OPENROUTER_API_KEY /root/insidedcpulse-secrets/agents/ai-research-agent.env
```
Expected: file mode `-rw-------`, and output shows `OPENROUTER_MODEL=...`, `AGENT_NAME=ai-research-agent`, `TOPIC_INDEX=0` (the `OPENROUTER_API_KEY` line is filtered out of the printed output — never print secret values).

No commit (file lives outside the repo and is gitignored).

---

### Task 6: Merge to main, push, verify `finding` schema is live

**Files:** none (git/deploy operations only)

- [ ] **Step 1: Run the full backend test suite on the feature branch**

```bash
cd /root/insidedcpulse-world-model/backend
.venv/bin/pytest tests/ -v
```
Expected: all tests `PASSED` (no regressions from Task 1's additions).

- [ ] **Step 2: Merge the feature branch into `main` and push**

Run from the main worktree (`/root/insidedcpulse-world-model`), with the feature branch `feature/ai-research-agent` (created via `using-git-worktrees` for Tasks 1-5):

```bash
cd /root/insidedcpulse-world-model
git checkout main
git pull origin main
git merge --ff-only feature/ai-research-agent
git push origin main
```

If `--ff-only` fails because `main` advanced past the feature branch's base, use `git merge feature/ai-research-agent` instead (creates a merge commit), then push.

- [ ] **Step 3: Poll until the `finding` schema is live**

The webhook auto-deploy rebuilds and restarts the API on push. Poll the live `/api/v1/world/evaluate` endpoint (shared demo key) until an op on `finding.*` is no longer rejected as an unknown namespace:

```bash
for i in $(seq 1 18); do
  curl -s -X POST https://insidedcpulse.com/api/v1/world/evaluate \
    -H "X-API-Key: j9zRmojp8EqBSLaRZwJatcRQa0HwcXK9-sqY70eIxtY" \
    -H "Content-Type: application/json" \
    -d '{"description":"schema check","ops":[{"op":"set","key":"finding.schema_check.relevance_score","value":0.5}],"metadata":{}}' \
    -o /tmp/finding_schema_check.json
  if ! grep -q "unknown key namespace" /tmp/finding_schema_check.json; then
    echo "finding schema is live"
    break
  fi
  sleep 10
done
cat /tmp/finding_schema_check.json
```
Expected (within ~3 minutes): output `finding schema is live`, and `/tmp/finding_schema_check.json` shows `"consistency_ratio": 1.0` with no `"unknown key namespace"` reason.

---

### Task 7: Live verification of `ai_research_agent.py`

**Files:** none (operational verification only)

- [ ] **Step 1: Run the script once**

```bash
cd /root/insidedcpulse-world-model/scripts/agents
python3 ai_research_agent.py /root/insidedcpulse-secrets/agents/ai-research-agent.env
```
Expected: prints `== agent: ai-research-agent-<suffix> ==` (self-registers on first run, writing `AGENT_ID`/`AGENT_API_KEY` back into the env file), `== topic: <one of the 6 TOPICS> ==`, the LLM's JSON response, and then either:
- `== propose_vision ==` with a `200`-shaped result (an accepted `finding.<id>` was proposed), or
- a no-op line (`All returned papers already in finding.* — no-op this cycle.`, `LLM found no sufficiently relevant candidate — no-op this cycle.`, or a relevance-below-threshold message) — all are valid outcomes depending on what the LLM finds.

- [ ] **Step 2: Run it 2 more times to confirm topic rotation and dedup**

```bash
python3 ai_research_agent.py /root/insidedcpulse-secrets/agents/ai-research-agent.env
python3 ai_research_agent.py /root/insidedcpulse-secrets/agents/ai-research-agent.env
```
Expected: each run prints a different `== topic: ... ==` (rotating through `TOPICS` via `TOPIC_INDEX`, which persists in the env file), and any newly-chosen paper has a `sanitize_id` not already present in `finding.*` from a prior run (dedup via `existing_ids`).

- [ ] **Step 3: Verify `finding.*` entries in world_state (if any were proposed)**

```bash
curl -s https://insidedcpulse.com/api/v1/world/state \
  -H "X-API-Key: j9zRmojp8EqBSLaRZwJatcRQa0HwcXK9-sqY70eIxtY" \
  | python3 -c "import json,sys; s=json.load(sys.stdin)['state']; print(json.dumps({k:v['value'] for k,v in s.items() if k.startswith('finding.')}, indent=2))"
```
Expected: for each accepted `finding.<id>`, all 9 fields (`title`, `summary`, `url`, `topics`, `relevance_score`, `why_it_matters`, `source`, `fetched_at`, `notes`) are present with the correct types — `relevance_score` is a number between 0 and 1, `notes` is an object containing `"insight"`.

Note: the FIFO-eviction code path (`existing_ids[parts[1]] = value["value"]`, triggered once `finding.*` exceeds 10 entries) is the exact pattern already fixed and live-verified across multiple eviction cycles in `research_agent.py` (see `project_insidedcpulse` memory) — not re-verified to the 11-entry boundary here, since each `ai-research-agent` run may legitimately no-op (LLM finds nothing relevant), making that boundary impractical to reach in a single verification pass.

---

### Task 8: Add the `:40` cron job

**Files:** none (system crontab)

- [ ] **Step 1: Add the cron line**

```bash
(crontab -l; echo "40 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/ai_research_agent.py /root/insidedcpulse-secrets/agents/ai-research-agent.env >> /root/insidedcpulse-secrets/agents/logs/ai-research-agent.log 2>&1") | crontab -
```

- [ ] **Step 2: Verify the crontab**

```bash
crontab -l
```
Expected: 5 lines total — the existing `:05`/`:20`/`:35`/`:50` entries plus the new `:40` entry for `ai_research_agent.py`.
