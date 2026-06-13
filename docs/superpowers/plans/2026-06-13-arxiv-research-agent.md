# arXiv research-agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4th always-on persona, `research-agent`, that pulls real SRE/ops
papers from arXiv (via the generated `arxiv-pp-cli`) and writes them into
`world_state` as a new `research.*` entity, with FIFO eviction at 10 entries.

**Architecture:** Pure-additive `research` entry in `ENTITY_SCHEMAS`
(`backend/app/world_schema.py`), a new deterministic script
`scripts/agents/research_agent.py` that reuses the HTTP/env helpers from
`scripts/agents/openrouter_agent.py`, a new per-persona secrets env file, and
a 4th staggered hourly cron line. `arxiv-pp-cli` (already generated and
installed at `/root/go/bin/arxiv-pp-cli`) is called via `subprocess.run` with
a fixed query string; its `--json` output wraps a raw Atom XML feed in a
`results` field, which `research_agent.py` parses with
`xml.etree.ElementTree`.

**Tech Stack:** Python 3 (`requests`, stdlib `xml.etree.ElementTree`), FastAPI
backend (pytest), `arxiv-pp-cli` (Go, already built), cron.

**Spec:** `docs/superpowers/specs/2026-06-13-arxiv-research-agent-design.md`

---

### Task 1: Add `research` entity to `ENTITY_SCHEMAS` + schema/domain tests

**Files:**
- Modify: `backend/app/world_schema.py:52-58`
- Test: `backend/tests/test_world_schema.py`
- Test: `backend/tests/test_domain_validation.py`

- [ ] **Step 1: Write failing tests in `test_world_schema.py`**

Append to `backend/tests/test_world_schema.py`:

```python
def test_parse_key_valid_research():
    assert parse_key("research.2506_01234.title") == KeyParts("research", "2506_01234", "title")


def test_get_field_spec_research_title():
    assert get_field_spec("research", "title") == {"type": "string"}


def test_get_field_spec_research_fetched_at():
    assert get_field_spec("research", "fetched_at") == {"type": "string"}
```

- [ ] **Step 2: Write failing tests in `test_domain_validation.py`**

Append to `backend/tests/test_domain_validation.py`:

```python
def test_set_research_title_valid():
    op = WorldOp(op="set", key="research.2506_01234.title", value="A Paper About SRE")
    assert check_domain_consistency(op, None) == (True, None)


def test_set_research_summary_valid():
    op = WorldOp(op="set", key="research.2506_01234.summary", value="An abstract.")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_research_unknown_field():
    op = WorldOp(op="set", key="research.2506_01234.unknown_field", value="x")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown field 'unknown_field' for entity 'research'"


def test_rejects_merge_on_research_title():
    op = WorldOp(op="merge", key="research.2506_01234.title", value={"x": 1})
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'merge' not allowed on field 'title' (type 'string')"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
cd /root/insidedcpulse-world-model/backend
.venv/bin/pytest tests/test_world_schema.py tests/test_domain_validation.py -v -k research
```
Expected: 4 tests FAIL — `test_get_field_spec_research_title` and
`test_get_field_spec_research_fetched_at` return `None` instead of the
expected dict; `test_parse_key_valid_research` returns `None` (unknown
entity); the two `check_domain_consistency` tests fail because
`research.*` is currently rejected as `"unknown key namespace"`.

- [ ] **Step 4: Add `research` entity to `ENTITY_SCHEMAS`**

In `backend/app/world_schema.py`, the `ENTITY_SCHEMAS` dict currently ends:

```python
    "alert": {
        "severity": {"type": "enum", "values": ["info", "warning", "critical"]},
        "status": {"type": "enum", "values": ["firing", "resolved"]},
        "source_service": {"type": "string"},
        "message": {"type": "object"},
    },
}
```

Change to:

```python
    "alert": {
        "severity": {"type": "enum", "values": ["info", "warning", "critical"]},
        "status": {"type": "enum", "values": ["firing", "resolved"]},
        "source_service": {"type": "string"},
        "message": {"type": "object"},
    },
    "research": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "topic": {"type": "string"},
        "published": {"type": "string"},
        "url": {"type": "string"},
        "fetched_at": {"type": "string"},
    },
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd /root/insidedcpulse-world-model/backend
.venv/bin/pytest tests/test_world_schema.py tests/test_domain_validation.py -v
```
Expected: PASS (all tests, including the new ones).

- [ ] **Step 6: Commit**

```bash
cd /root/insidedcpulse-world-model
git add backend/app/world_schema.py backend/tests/test_world_schema.py backend/tests/test_domain_validation.py
git commit -m "Add research entity to ENTITY_SCHEMAS (arXiv research-agent)"
```

---

### Task 2: Document `research.*` in `ENTITY_SCHEMA_TEXT`

**Files:**
- Modify: `scripts/agents/openrouter_agent.py:48-54`

- [ ] **Step 1: Add `research.*` rows to `ENTITY_SCHEMA_TEXT`**

Current text (lines 48-54):

```python
alert.<id>.severity             enum: info, warning, critical
alert.<id>.status               enum: firing, resolved
alert.<id>.source_service       string
alert.<id>.message              object

Valid ops: {"op": "set"|"increment"|"merge", "key": "<entity>.<id>.<field>", "value": ...}
"""
```

Change to:

```python
alert.<id>.severity             enum: info, warning, critical
alert.<id>.status               enum: firing, resolved
alert.<id>.source_service       string
alert.<id>.message              object

research.<id>.title             string
research.<id>.summary           string
research.<id>.topic             string
research.<id>.published         string
research.<id>.url               string
research.<id>.fetched_at        string

Valid ops: {"op": "set"|"increment"|"merge", "key": "<entity>.<id>.<field>", "value": ...}
"""
```

- [ ] **Step 2: Verify the file still parses**

Run:
```bash
cd /root/insidedcpulse-world-model
python3 -c "import ast; ast.parse(open('scripts/agents/openrouter_agent.py').read())"
```
Expected: no output (no `SyntaxError`).

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/openrouter_agent.py
git commit -m "Document research.* namespace in ENTITY_SCHEMA_TEXT"
```

---

### Task 3: Create `scripts/agents/research_agent.py`

**Files:**
- Create: `scripts/agents/research_agent.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Deterministic research-agent: pulls arXiv papers into world_state research.*."""

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from openrouter_agent import (
    ensure_agent,
    evaluate_vision,
    get_world_state,
    load_env,
    propose_vision,
    save_env,
)

ENV_PATH = Path("/root/insidedcpulse-secrets/agents/research-agent.env")
ARXIV_CLI = "/root/go/bin/arxiv-pp-cli"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
RESEARCH_FIELDS = ["title", "summary", "topic", "published", "url", "fetched_at"]
MAX_RESEARCH_ENTRIES = 10

TOPICS = [
    "site reliability engineering",
    "anomaly detection time series",
    "incident response automation",
    "distributed systems fault tolerance",
    "chaos engineering",
]


def sanitize_id(arxiv_id: str) -> str:
    """'http://arxiv.org/abs/2506.01234v2' -> '2506_01234'."""
    tail = arxiv_id.rsplit("/abs/", 1)[-1]
    tail = re.sub(r"v\d+$", "", tail)
    tail = tail.lower().replace(".", "_").replace("/", "_")
    return tail[:32]


def search_arxiv(topic: str, max_results: int = 5) -> list[dict]:
    result = subprocess.run(
        [
            ARXIV_CLI,
            "query",
            "--search-query",
            f'all:"{topic}"',
            "--max-results",
            str(max_results),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"arxiv-pp-cli failed: {result.returncode}")
        print(result.stderr)
        sys.exit(1)

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"failed to parse arxiv-pp-cli output as JSON: {exc}")
        print(result.stdout)
        sys.exit(1)

    try:
        root = ET.fromstring(envelope["results"])
    except (KeyError, ET.ParseError) as exc:
        print(f"failed to parse arXiv Atom feed: {exc}")
        print(envelope)
        sys.exit(1)

    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        arxiv_id = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
        title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
        published = (entry.findtext("atom:published", default="", namespaces=ATOM_NS) or "").strip()
        link = arxiv_id
        for link_el in entry.findall("atom:link", ATOM_NS):
            if link_el.get("rel") == "alternate":
                link = link_el.get("href", link)
                break
        papers.append(
            {
                "id": arxiv_id,
                "title": title,
                "summary": summary,
                "published": published,
                "link": link,
            }
        )
    return papers


def main() -> None:
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ENV_PATH
    env = load_env(env_path)

    agent_id, agent_api_key = ensure_agent(env, env_path)
    print(f"== agent: {agent_id} ==")

    world_state = get_world_state(agent_api_key)
    state = world_state["state"]

    existing_ids: dict[str, str] = {}
    for key, value in state.items():
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "research" and parts[2] == "fetched_at":
            existing_ids[parts[1]] = value

    topic_index = int(env.get("TOPIC_INDEX", "0"))
    topic = TOPICS[topic_index % len(TOPICS)]
    env["TOPIC_INDEX"] = str((topic_index + 1) % len(TOPICS))
    save_env(env_path, env)
    print(f"== topic: {topic} ==")

    papers = search_arxiv(topic)

    chosen = None
    for paper in papers:
        candidate_id = sanitize_id(paper["id"])
        if candidate_id not in existing_ids:
            chosen = (candidate_id, paper)
            break

    if chosen is None:
        print("All returned papers already in research.* — no-op this cycle.")
        return

    new_id, paper = chosen
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ops = [
        {"op": "set", "key": f"research.{new_id}.title", "value": paper["title"]},
        {"op": "set", "key": f"research.{new_id}.summary", "value": paper["summary"][:500]},
        {"op": "set", "key": f"research.{new_id}.topic", "value": topic},
        {"op": "set", "key": f"research.{new_id}.published", "value": paper["published"]},
        {"op": "set", "key": f"research.{new_id}.url", "value": paper["link"]},
        {"op": "set", "key": f"research.{new_id}.fetched_at", "value": now},
    ]

    if len(existing_ids) + 1 > MAX_RESEARCH_ENTRIES:
        oldest_id = min(existing_ids, key=lambda k: existing_ids[k])
        for field in RESEARCH_FIELDS:
            ops.append({"op": "delete", "key": f"research.{oldest_id}.{field}"})

    payload = {
        "description": f"Add arXiv paper '{paper['title']}' ({new_id}) on {topic}",
        "ops": ops,
        "metadata": {"source": "research-agent", "topic": topic},
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

- [ ] **Step 2: Verify the file parses**

Run:
```bash
cd /root/insidedcpulse-world-model
python3 -m py_compile scripts/agents/research_agent.py
```
Expected: no output (no `SyntaxError`).

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/research_agent.py
git commit -m "Add research_agent.py: deterministic arXiv -> world_state persona"
```

---

### Task 4: Create secrets env file, run manually, verify against live API

> **Note:** This step self-registers a new agent and, if accepted, calls
> `POST /api/v1/world/vision` against the **live production** API
> (`https://insidedcpulse.com`), the same way the existing
> sre-agent/deploy-agent/alert-agent personas were verified. It writes a real
> `research.<id>.*` entry into the live `world_state`.

**Files:**
- Create: `/root/insidedcpulse-secrets/agents/research-agent.env` (outside repo, chmod 600)

- [ ] **Step 1: Create the env file**

```bash
cat > /root/insidedcpulse-secrets/agents/research-agent.env <<'EOF'
AGENT_NAME=research-agent
AGENT_ID=
AGENT_API_KEY=
TOPIC_INDEX=0
EOF
chmod 600 /root/insidedcpulse-secrets/agents/research-agent.env
```

- [ ] **Step 2: Run the script once**

```bash
cd /root/insidedcpulse-world-model/scripts/agents
python3 research_agent.py /root/insidedcpulse-secrets/agents/research-agent.env
```

Expected:
- `== agent: research-agent-XXXXXX ==` printed, and
  `/root/insidedcpulse-secrets/agents/research-agent.env` now has
  `AGENT_ID=research-agent-XXXXXX` and a non-empty `AGENT_API_KEY` filled in
  (via `save_env`), plus `TOPIC_INDEX=1`.
- `== topic: site reliability engineering ==`
- `== evaluate ==` shows `"would_accept": true`.
- `== propose_vision ==` shows a successful queue response.

If `would_accept` is `false` (e.g. the chosen paper's `id` collides with an
existing key for an unrelated reason), the script exits 0 after printing the
evaluation — re-run; `TOPIC_INDEX` has already advanced so the next run uses
a different topic/paper.

- [ ] **Step 3: Confirm the new `research.<id>.*` keys are live**

```bash
source /root/insidedcpulse-secrets/agents/research-agent.env
curl -s https://insidedcpulse.com/api/v1/world/state -H "X-API-Key: $AGENT_API_KEY" \
  | python3 -c "import json,sys; s=json.load(sys.stdin)['state']; print({k:v for k,v in s.items() if k.startswith('research.')})"
```
Expected: a dict with 6 keys (`research.<id>.title`, `.summary`, `.topic`,
`.published`, `.url`, `.fetched_at`) for the paper chosen in Step 2.

- [ ] **Step 4: FIFO eviction check**

Run the script 10 more times (each run rotates `TOPIC_INDEX` and picks the
next not-yet-seen paper for that run's topic):

```bash
cd /root/insidedcpulse-world-model/scripts/agents
for i in $(seq 1 10); do
  python3 research_agent.py /root/insidedcpulse-secrets/agents/research-agent.env
  sleep 3
done
```

Then re-check the world state:
```bash
source /root/insidedcpulse-secrets/agents/research-agent.env
curl -s https://insidedcpulse.com/api/v1/world/state -H "X-API-Key: $AGENT_API_KEY" \
  | python3 -c "import json,sys; s=json.load(sys.stdin)['state']; ids={k.split('.')[1] for k in s if k.startswith('research.')}; print(len(ids), sorted(ids))"
```
Expected: `10` distinct `research.<id>` entries (the FIFO eviction in Step 7
of the script removed the oldest entry once the 11th was added). If fewer
than 11 total runs succeeded (some were no-ops because all 5 results for a
topic were already present), re-run a couple more times until 11 successful
proposals have happened, then re-check.

---

### Task 5: Install the 4th cron slot

**Files:**
- Modify: root crontab (not a repo file)

- [ ] **Step 1: Capture the current crontab**

```bash
crontab -l > /tmp/crontab.bak
cat /tmp/crontab.bak
```
Expected: the existing 3 lines (`:05` sre-agent, `:20` deploy-agent, `:35`
alert-agent).

- [ ] **Step 2: Append the research-agent line and install**

```bash
cat /tmp/crontab.bak > /tmp/crontab.new
echo '50 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/research_agent.py /root/insidedcpulse-secrets/agents/research-agent.env >> /root/insidedcpulse-secrets/agents/logs/research-agent.log 2>&1' >> /tmp/crontab.new
crontab /tmp/crontab.new
```

- [ ] **Step 3: Verify**

```bash
crontab -l
```
Expected: 4 lines total — `:05` sre-agent, `:20` deploy-agent, `:35`
alert-agent, `:50` research-agent.

---

### Task 6: Update `README.md`

**Files:**
- Modify: `README.md:90-97` (world state schema table)
- Modify: `README.md:282-298` (Test agents section)

- [ ] **Step 1: Add `research` row to the schema table**

Current table (lines 90-97):

```markdown
| Entity | `id` | Fields |
|---|---|---|
| `region` | `^[a-z0-9_]{1,32}$` | `capacity_forecast` (number, >=0), `population` (integer, >=0), `status` (enum: `stable`\|`growing`\|`declining`\|`critical`), `notes` (object) |
| `service` | `^[a-z0-9_]{1,32}$` | `status` (enum: `healthy`\|`degraded`\|`down`), `load` (number, 0-100), `version` (string), `capacity` (number, >=0) |
| `incident` | `^[a-z0-9_]{1,32}$` | `severity` (enum: `low`\|`medium`\|`high`\|`critical`), `status` (enum: `open`\|`mitigated`\|`resolved`), `affected_service` (string), `affected_region` (string), `notes` (object) |
| `deployment` | `^[a-z0-9_]{1,32}$` | `status` (enum: `pending`\|`in_progress`\|`done`\|`failed`\|`rolled_back`), `version` (string), `target_service` (string), `progress` (number, 0-100) |
| `team` | `^[a-z0-9_]{1,32}$` | `on_call` (enum: `active`\|`off`), `headcount` (integer, >=0), `owned_services` (object) |
| `alert` | `^[a-z0-9_]{1,32}$` | `severity` (enum: `info`\|`warning`\|`critical`), `status` (enum: `firing`\|`resolved`), `source_service` (string), `message` (object) |
```

Add a row after the `alert` row:

```markdown
| `research` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `topic` (string), `published` (string), `url` (string), `fetched_at` (string) |
```

- [ ] **Step 2: Extend the "Test agents" section**

Current section (lines 282-298):

```markdown
## Test agents

`scripts/agents/openrouter_agent.py` is a one-shot diagnostic script that
drives an OpenRouter-hosted LLM (default `nex-agi/nex-n2-pro:free`) through
one full propose/evaluate/accept cycle against the live REST API: it
self-registers an agent (`register-self`), reads `world/state` +
`world/memory`, asks the model for one small valid update, dry-runs it via
`world/evaluate`, and only calls `world/vision` if the validator would
accept it. Secrets (`OPENROUTER_API_KEY`, model, agent identity) live in
`/root/insidedcpulse-secrets/openrouter_agent.env` (gitignored, not in repo).
Spec: `docs/superpowers/specs/2026-06-12-openrouter-test-agent-design.md`.

```bash
python3 scripts/agents/openrouter_agent.py
```

---
```

Append a new subsection after the existing `python3 scripts/agents/openrouter_agent.py`
code block, before the `---`:

```markdown

### Always-on personas

Four hourly cron jobs each run one propose/evaluate/accept cycle against the
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
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document research entity and always-on personas in README"
```

---

### Task 7: Update `llms.txt`

**Files:**
- Modify: `docker/nginx/static/llms.txt:52-72` (Current scenario section)

- [ ] **Step 1: Mention the `research.*` namespace**

In the "Current scenario" section, after the existing bullet list (ending
`... Feel free to extend the scenario with new \`incident\`/\`deployment\`/\`alert\`
entries too.`), add a new paragraph:

```markdown

In addition to the incident scenario above, `research.*` holds a rotating
feed of SRE/ops-relevant arXiv papers (`research.<id>.title`, `.summary`,
`.topic`, `.published`, `.url`, `.fetched_at`), refreshed hourly by the
deterministic `research-agent` persona — up to 10 entries, oldest evicted
first.
```

- [ ] **Step 2: Commit**

```bash
git add docker/nginx/static/llms.txt
git commit -m "Document research.* namespace in llms.txt"
```

---

### Task 8: Deploy

**Files:** none (deploy via existing webhook auto-deploy)

- [ ] **Step 1: Push the branch / merge to trigger deploy**

Follow the project's existing webhook auto-deploy path (push to the deployed
branch). Confirm `docker compose ps` on the VPS shows healthy containers
after the deploy completes.

- [ ] **Step 2: Smoke test on production**

```bash
curl -s https://insidedcpulse.com/api/v1/world/state -H "X-API-Key: <demo-key>" \
  | python3 -c "import json,sys; s=json.load(sys.stdin)['state']; print([k for k in s if k.startswith('research.')])"
```
Expected: the `research.<id>.*` keys written during Task 4's manual
verification are present and served by the deployed backend (schema change
doesn't require new backend code paths, only the additive `ENTITY_SCHEMAS`
entry, so no migration is needed).
