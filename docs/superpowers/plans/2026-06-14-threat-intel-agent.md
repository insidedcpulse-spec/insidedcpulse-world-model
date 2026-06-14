# threat-intel-agent (6th persona) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 6th always-on persona, `threat-intel-agent` — a deterministic
(no-LLM) agent that pulls actively-exploited CVEs from CISA's Known Exploited
Vulnerabilities (KEV) catalog into a new `vulnerability.*` entity in
`world_state` and the knowledge graph, with a best-effort link to
InsideDCPulse's own stack via the existing `affected_service`
(`REFERENCE_FIELDS`) mechanism.

**Architecture:** Pure-additive entity-schema expansion (`vulnerability` added
to `ENTITY_SCHEMAS`, fully generic validation — zero engine changes) plus a
new deterministic agent script `scripts/agents/threat_intel_agent.py` that
mirrors `scripts/agents/research_agent.py`'s fetch → pick-new → build-ops →
FIFO-evict → evaluate → propose flow, but sources CISA KEV JSON instead of
arXiv and adds a hand-maintained stack-keyword match table.

**Tech Stack:** Python 3 (`requests`), pytest, existing
`scripts/agents/openrouter_agent.py` helpers
(`load_env`/`ensure_agent`/`get_world_state`/`evaluate_vision`/`propose_vision`),
cron.

Spec: `docs/superpowers/specs/2026-06-14-threat-intel-agent-design.md`

---

### Task 1: Create feature branch

**Files:** none (git only)

- [ ] **Step 1: Create and switch to the feature branch**

```bash
cd /root/insidedcpulse-world-model
git checkout -b feature/threat-intel-agent
```

Expected: `Switched to a new branch 'feature/threat-intel-agent'`

---

### Task 2: `vulnerability` entity in `world_schema.py` (TDD)

**Files:**
- Modify: `backend/app/world_schema.py:76-77`
- Test: `backend/tests/test_world_schema.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_world_schema.py`:

```python
def test_parse_key_valid_vulnerability():
    assert parse_key("vulnerability.cve_2026_35273.severity") == KeyParts(
        "vulnerability", "cve_2026_35273", "severity"
    )


def test_get_field_spec_vulnerability_cve_id():
    assert get_field_spec("vulnerability", "cve_id") == {"type": "string"}


def test_get_field_spec_vulnerability_severity():
    assert get_field_spec("vulnerability", "severity") == {
        "type": "enum",
        "values": ["high", "critical"],
    }


def test_get_field_spec_vulnerability_affected_service():
    assert get_field_spec("vulnerability", "affected_service") == {"type": "string"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/insidedcpulse-world-model/backend && python3 -m pytest tests/test_world_schema.py -v -k vulnerability`

Expected: 4 FAILED — `parse_key(...)` returns `None` (assertion against
`KeyParts(...)` fails) and `get_field_spec(...)` returns `None` (assertion
against the expected dict fails), because `"vulnerability"` is not yet in
`ENTITY_SCHEMAS`.

- [ ] **Step 3: Add the `vulnerability` entity to `ENTITY_SCHEMAS`**

In `backend/app/world_schema.py`, insert a new entry between the `"finding"`
block (ending at line 76 with `    },`) and the closing `}` at line 77:

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
    "vulnerability": {
        "cve_id": {"type": "string"},
        "product": {"type": "string"},
        "summary": {"type": "string"},
        "severity": {"type": "enum", "values": ["high", "critical"]},
        "date_added": {"type": "string"},
        "stack_match": {"type": "string"},
        "affected_service": {"type": "string"},
        "url": {"type": "string"},
        "fetched_at": {"type": "string"},
    },
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/insidedcpulse-world-model/backend && python3 -m pytest tests/test_world_schema.py -v -k vulnerability`

Expected: 4 PASSED

- [ ] **Step 5: Run the full schema test file**

Run: `cd /root/insidedcpulse-world-model/backend && python3 -m pytest tests/test_world_schema.py -v`

Expected: all PASSED (no regressions on existing entities)

- [ ] **Step 6: Commit**

```bash
cd /root/insidedcpulse-world-model
git add backend/app/world_schema.py backend/tests/test_world_schema.py
git commit -m "feat: add vulnerability entity to world_schema"
```

---

### Task 3: Domain consistency tests for `vulnerability`

**Files:**
- Test: `backend/tests/test_domain_validation.py`

No production code changes in this task — `check_domain_consistency` is
fully generic over `ENTITY_SCHEMAS`, so these tests exercise the schema added
in Task 2 and should pass immediately. This mirrors the "World schema entity
expansion" precedent for `research`/`finding`.

- [ ] **Step 1: Write the new test cases**

Append to `backend/tests/test_domain_validation.py`:

```python
def test_set_vulnerability_cve_id_valid():
    op = WorldOp(op="set", key="vulnerability.cve_2026_35273.cve_id", value="CVE-2026-35273")
    assert check_domain_consistency(op, None) == (True, None)


def test_set_vulnerability_severity_valid():
    op = WorldOp(op="set", key="vulnerability.cve_2026_35273.severity", value="critical")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_vulnerability_invalid_severity():
    op = WorldOp(op="set", key="vulnerability.cve_2026_35273.severity", value="medium")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "'severity' must be one of ['high', 'critical'], got 'medium'"


def test_rejects_vulnerability_unknown_field():
    op = WorldOp(op="set", key="vulnerability.cve_2026_35273.unknown_field", value="x")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown field 'unknown_field' for entity 'vulnerability'"


def test_rejects_merge_on_vulnerability_summary():
    op = WorldOp(op="merge", key="vulnerability.cve_2026_35273.summary", value={"x": 1})
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'merge' not allowed on field 'summary' (type 'string')"


def test_set_vulnerability_affected_service_empty_string_valid():
    op = WorldOp(op="set", key="vulnerability.cve_2026_35273.affected_service", value="")
    assert check_domain_consistency(op, None) == (True, None)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /root/insidedcpulse-world-model/backend && python3 -m pytest tests/test_domain_validation.py -v -k vulnerability`

Expected: 6 PASSED

- [ ] **Step 3: Run the full backend test suite**

Run: `cd /root/insidedcpulse-world-model/backend && python3 -m pytest -q`

Expected: all tests PASSED (no regressions)

- [ ] **Step 4: Commit**

```bash
cd /root/insidedcpulse-world-model
git add backend/tests/test_domain_validation.py
git commit -m "test: add domain consistency tests for vulnerability entity"
```

---

### Task 4: Add `vulnerability` to `ENTITY_SCHEMA_TEXT`

**Files:**
- Modify: `scripts/agents/openrouter_agent.py:53-61`

The LLM-based personas (sre/deploy/alert/ai-research) read `ENTITY_SCHEMA_TEXT`
to know which world-state namespaces exist. `threat-intel-agent` itself does
not use an LLM, but per the "World schema entity expansion" precedent the new
namespace is documented here too so the LLM personas are aware of it.

- [ ] **Step 1: Insert the `vulnerability` block before the `research` block**

In `scripts/agents/openrouter_agent.py`, the current text (lines 53-61) is:

```python
research.<id>.title             string
research.<id>.summary           string
research.<id>.topic             string
research.<id>.published         string
research.<id>.url               string
research.<id>.fetched_at        string

Valid ops: {"op": "set"|"increment"|"merge", "key": "<entity>.<id>.<field>", "value": ...}
"""
```

Replace it with:

```python
research.<id>.title             string
research.<id>.summary           string
research.<id>.topic             string
research.<id>.published         string
research.<id>.url               string
research.<id>.fetched_at        string

vulnerability.<id>.cve_id            string
vulnerability.<id>.product           string
vulnerability.<id>.summary           string
vulnerability.<id>.severity          enum: high, critical
vulnerability.<id>.date_added        string
vulnerability.<id>.stack_match       string
vulnerability.<id>.affected_service  string
vulnerability.<id>.url               string
vulnerability.<id>.fetched_at        string

Valid ops: {"op": "set"|"increment"|"merge", "key": "<entity>.<id>.<field>", "value": ...}
"""
```

- [ ] **Step 2: Verify the constant still parses and contains the new block**

Run: `cd /root/insidedcpulse-world-model && python3 -c "from scripts.agents.openrouter_agent import ENTITY_SCHEMA_TEXT as t; assert 'vulnerability.<id>.severity' in t; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Run the full backend test suite (sanity check, no backend code touched)**

Run: `cd /root/insidedcpulse-world-model/backend && python3 -m pytest -q`

Expected: all tests PASSED

- [ ] **Step 4: Commit**

```bash
cd /root/insidedcpulse-world-model
git add scripts/agents/openrouter_agent.py
git commit -m "docs: add vulnerability to ENTITY_SCHEMA_TEXT"
```

---

### Task 5: `scripts/agents/threat_intel_agent.py`

**Files:**
- Create: `scripts/agents/threat_intel_agent.py`

This script is not unit-tested (same as `research_agent.py` /
`ai_research_agent.py` — no `backend/tests` coverage for `scripts/agents/`).
It is verified via live runs in Task 9. This task writes the full script and
checks it compiles/imports cleanly.

- [ ] **Step 1: Write the script**

Create `scripts/agents/threat_intel_agent.py`:

```python
#!/usr/bin/env python3
"""Deterministic threat-intel-agent: pulls CISA KEV entries into world_state vulnerability.*."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from openrouter_agent import (
    ensure_agent,
    evaluate_vision,
    get_world_state,
    load_env,
    propose_vision,
)

ENV_PATH = Path("/root/insidedcpulse-secrets/agents/threat-intel-agent.env")
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
VULNERABILITY_FIELDS = [
    "cve_id",
    "product",
    "summary",
    "severity",
    "date_added",
    "stack_match",
    "affected_service",
    "url",
    "fetched_at",
]
MAX_VULNERABILITY_ENTRIES = 10

# (keywords, stack_match, affected_service) — case-insensitive substring scan,
# first match wins. Hand-maintained best-effort map, not a real CPE/SBOM match.
STACK_MATCHES: list[tuple[list[str], str, str]] = [
    (["nginx"], "nginx:1.27-alpine", "service.checkout"),
    (["postgres", "postgresql"], "postgres:16-alpine", "service.payments_db"),
    (["redis"], "redis:7-alpine", "service.checkout"),
    (["grafana"], "grafana:13.0.2", "team.sre"),
    (["prometheus"], "prometheus:v3.12.0", "team.sre"),
    (["certbot", "let's encrypt", "acme"], "certbot:v5.6.0", "team.sre"),
    (["fastapi", "starlette", "uvicorn", "mcp"], "fastapi/starlette/mcp", "team.sre"),
]


def sanitize_cve_id(cve_id: str) -> str:
    """'CVE-2026-35273' -> 'cve_2026_35273'."""
    return cve_id.strip().lower().replace("-", "_")


def match_stack(vendor: str, product: str, name: str, description: str) -> tuple[str, str]:
    haystack = f"{vendor} {product} {name} {description}".lower()
    for keywords, stack_match, affected_service in STACK_MATCHES:
        if any(keyword in haystack for keyword in keywords):
            return stack_match, affected_service
    return "", ""


def fetch_kev() -> list[dict]:
    resp = requests.get(KEV_URL, timeout=30)
    if resp.status_code >= 300:
        print(f"CISA KEV fetch failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()["vulnerabilities"]


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
        if len(parts) == 3 and parts[0] == "vulnerability" and parts[2] == "fetched_at":
            existing_ids[parts[1]] = value["value"]

    vulnerabilities = fetch_kev()
    vulnerabilities.sort(key=lambda v: v.get("dateAdded", ""), reverse=True)

    chosen = None
    for vuln in vulnerabilities:
        candidate_id = sanitize_cve_id(vuln["cveID"])
        if candidate_id not in existing_ids:
            chosen = (candidate_id, vuln)
            break

    if chosen is None:
        print("All returned CVEs already in vulnerability.* — no-op this cycle.")
        return

    new_id, vuln = chosen
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    vendor = vuln.get("vendorProject", "").strip()
    product = vuln.get("product", "").strip()
    name = vuln.get("vulnerabilityName", "")
    description = vuln.get("shortDescription", "")

    severity = "critical" if vuln.get("knownRansomwareCampaignUse") == "Known" else "high"
    stack_match, affected_service = match_stack(vendor, product, name, description)

    ops = [
        {"op": "set", "key": f"vulnerability.{new_id}.cve_id", "value": vuln["cveID"]},
        {"op": "set", "key": f"vulnerability.{new_id}.product", "value": f"{vendor} {product}"[:200]},
        {"op": "set", "key": f"vulnerability.{new_id}.summary", "value": description[:500]},
        {"op": "set", "key": f"vulnerability.{new_id}.severity", "value": severity},
        {"op": "set", "key": f"vulnerability.{new_id}.date_added", "value": vuln.get("dateAdded", "")},
        {"op": "set", "key": f"vulnerability.{new_id}.stack_match", "value": stack_match},
        {"op": "set", "key": f"vulnerability.{new_id}.affected_service", "value": affected_service},
        {"op": "set", "key": f"vulnerability.{new_id}.url", "value": f"https://nvd.nist.gov/vuln/detail/{vuln['cveID']}"},
        {"op": "set", "key": f"vulnerability.{new_id}.fetched_at", "value": now},
    ]

    if len(existing_ids) + 1 > MAX_VULNERABILITY_ENTRIES:
        oldest_id = min(existing_ids, key=lambda k: existing_ids[k])
        for field in VULNERABILITY_FIELDS:
            ops.append({"op": "delete", "key": f"vulnerability.{oldest_id}.{field}"})

    payload = {
        "description": f"Add CISA KEV entry {vuln['cveID']} ({new_id})",
        "ops": ops,
        "metadata": {
            "source": "threat-intel-agent",
            "cve_id": vuln["cveID"],
            "stack_match": stack_match,
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

```bash
cd /root/insidedcpulse-world-model
chmod +x scripts/agents/threat_intel_agent.py
python3 -m py_compile scripts/agents/threat_intel_agent.py
```

Expected: no output (success)

- [ ] **Step 3: Verify it imports cleanly alongside `openrouter_agent`**

```bash
cd /root/insidedcpulse-world-model/scripts/agents && python3 -c "import threat_intel_agent; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
cd /root/insidedcpulse-world-model
git add scripts/agents/threat_intel_agent.py
git commit -m "feat: add threat-intel-agent (CISA KEV -> vulnerability.*)"
```

---

### Task 6: README.md updates

**Files:**
- Modify: `README.md:111-112` (world state schema table)
- Modify: `README.md:349-373` (Always-on personas list)

- [ ] **Step 1: Add the `vulnerability` row to the world state schema table**

In `README.md`, the table at lines 105-112 currently ends with:

```markdown
| `research` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `topic` (string), `published` (string), `url` (string), `fetched_at` (string) |
| `finding` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `url` (string), `topics` (string), `relevance_score` (number, 0-1), `why_it_matters` (string), `source` (string), `fetched_at` (string), `notes` (object) |
```

Add a new row immediately after the `finding` row:

```markdown
| `research` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `topic` (string), `published` (string), `url` (string), `fetched_at` (string) |
| `finding` | `^[a-z0-9_]{1,32}$` | `title` (string), `summary` (string), `url` (string), `topics` (string), `relevance_score` (number, 0-1), `why_it_matters` (string), `source` (string), `fetched_at` (string), `notes` (object) |
| `vulnerability` | `^[a-z0-9_]{1,32}$` | `cve_id` (string), `product` (string), `summary` (string), `severity` (enum: `high`\|`critical`), `date_added` (string), `stack_match` (string), `affected_service` (string), `url` (string), `fetched_at` (string) |
```

- [ ] **Step 2: Update the "Always-on personas" intro count**

In `README.md:351`, change:

```markdown
Five hourly cron jobs each run one propose/evaluate/accept cycle against the
```

to:

```markdown
Six hourly cron jobs each run one propose/evaluate/accept cycle against the
```

- [ ] **Step 3: Add the `threat-intel-agent` persona bullet**

In `README.md`, after the `ai-research-agent` bullet (ending at line 373
with `\`docs/superpowers/specs/2026-06-13-ai-research-agent-design.md\`.`),
add a new bullet:

```markdown
- `threat-intel-agent` (`:15`) — deterministic, no LLM. Pulls one new
  actively-exploited CVE per run from CISA's Known Exploited Vulnerabilities
  (KEV) catalog into `vulnerability.*`, evicting the oldest entry once more
  than 10 are present. Each entry is checked against a small hand-maintained
  map of InsideDCPulse's own pinned stack components; a match sets
  `affected_service`, which is automatically projected into a `REFERENCES`
  graph edge to the matching `service.*`/`team.sre` node. Spec:
  `docs/superpowers/specs/2026-06-14-threat-intel-agent-design.md`.
```

- [ ] **Step 4: Verify the table and list render correctly**

Run: `grep -n "vulnerability\|threat-intel-agent\|Six hourly" README.md`

Expected: 3 matching lines (table row, intro sentence, persona bullet)

- [ ] **Step 5: Commit**

```bash
cd /root/insidedcpulse-world-model
git add README.md
git commit -m "docs: document vulnerability entity and threat-intel-agent persona"
```

---

### Task 7: `llms.txt` updates

**Files:**
- Modify: `docker/nginx/static/llms.txt:97-111`

- [ ] **Step 1: Add the `vulnerability.*` rotating-feed paragraph**

In `docker/nginx/static/llms.txt`, the `finding.*` paragraph (lines 103-110)
currently ends with:

```
`finding.*` holds a second, complementary rotating feed: AI-systems-research
arXiv papers relevant to InsideDCPulse's own architecture (event-sourced
systems, multi-agent coordination, agent memory, LLM planning, tool-use
agents, world models), refreshed hourly by the LLM-based `ai-research-agent`
persona — `finding.<id>.title`, `.summary`, `.url`, `.topics`,
`.relevance_score` (0-1), `.why_it_matters`, `.source`, `.fetched_at`, and
`.notes.insight` (one architectural insight per finding). Also capped at 10
entries, oldest evicted first.
```

Immediately after that paragraph (before the blank line + `## Get your own
agent identity` heading at line 112), add:

```
`vulnerability.*` holds a third, security-focused rotating feed: actively
exploited CVEs from CISA's Known Exploited Vulnerabilities (KEV) catalog,
refreshed hourly by the deterministic `threat-intel-agent` persona —
`vulnerability.<id>.cve_id`, `.product`, `.summary`, `.severity`
(`high`|`critical`), `.date_added`, `.stack_match`, `.affected_service`,
`.url`, `.fetched_at`. When `.affected_service` references an existing
`service.*`/`team.*` entity, it is automatically projected into a
`REFERENCES` graph edge linking the CVE to InsideDCPulse's own stack. Also
capped at 10 entries, oldest evicted first.
```

- [ ] **Step 2: Verify**

Run: `grep -n "vulnerability" docker/nginx/static/llms.txt`

Expected: 1 matching line (the new paragraph's first line)

- [ ] **Step 3: Commit**

```bash
cd /root/insidedcpulse-world-model
git add docker/nginx/static/llms.txt
git commit -m "docs: add vulnerability.* feed to llms.txt"
```

---

### Task 8: Push branch and open PR

**Files:** none (git/GitHub only)

- [ ] **Step 1: Run the full backend test suite one more time**

Run: `cd /root/insidedcpulse-world-model/backend && python3 -m pytest -q`

Expected: all tests PASSED

- [ ] **Step 2: Push the branch**

Use the documented PAT push pattern (see `project_insidedcpulse` memory for
the token value — do not paste it into any committed file):

```bash
cd /root/insidedcpulse-world-model
git -c credential.helper= push -u "https://x-access-token:<TOKEN>@github.com/insidedcpulse-spec/insidedcpulse-world-model.git" feature/threat-intel-agent
git config --local branch.feature/threat-intel-agent.remote origin
```

- [ ] **Step 3: Open the PR**

```bash
cd /root/insidedcpulse-world-model
GH_TOKEN=<TOKEN> gh pr create \
  --title "feat: add threat-intel-agent (6th persona) — CISA KEV feed" \
  --body "$(cat <<'EOF'
## Summary
- New `vulnerability` entity in `world_schema.py` (pure-additive, 9 fields)
- New deterministic `threat-intel-agent` persona (cron `:15`): pulls actively-exploited CVEs from CISA's KEV catalog into `vulnerability.*`, FIFO-capped at 10
- Best-effort stack-keyword match sets `affected_service`, auto-projected into a `REFERENCES` graph edge to `service.*`/`team.sre` (zero `graph_projection.py` changes)
- Docs updated: README, llms.txt, ENTITY_SCHEMA_TEXT

## Test plan
- [x] `backend/tests/test_world_schema.py` — vulnerability parse_key/get_field_spec
- [x] `backend/tests/test_domain_validation.py` — vulnerability set/merge/enum validation
- [ ] Live verification after merge (register agent, run script once, check world_state + graph node)

Spec: `docs/superpowers/specs/2026-06-14-threat-intel-agent-design.md`
EOF
)"
```

Expected: PR URL printed.

---

### Task 9: Merge, deploy, and live-verify

**Files:**
- Create (gitignored, not in repo): `/root/insidedcpulse-secrets/agents/threat-intel-agent.env`
- Modify: crontab (via `crontab -e` / `crontab <file>`)

- [ ] **Step 1: Merge the PR and update local `main`**

```bash
cd /root/insidedcpulse-world-model
GH_TOKEN=<TOKEN> gh pr merge feature/threat-intel-agent --merge
git checkout main
git pull origin main
```

- [ ] **Step 2: Create the agent env file**

```bash
cat > /root/insidedcpulse-secrets/agents/threat-intel-agent.env <<'EOF'
AGENT_NAME=threat-intel-agent
EOF
```

(`AGENT_ID`/`AGENT_API_KEY` are filled in automatically by `ensure_agent` on
first run, same as the other personas.)

- [ ] **Step 3: Run the agent manually once**

```bash
cd /root/insidedcpulse-world-model/scripts/agents
python3 threat_intel_agent.py
```

Expected: prints `== agent: threat-intel-agent-xxxxxx ==`, `== evaluate ==`
with `"would_accept": true`, then `== propose_vision ==` with the accepted
event. If `would_accept` is `false`, read the validator message and fix the
script before continuing (do not proceed to cron setup with a broken script).

- [ ] **Step 4: Verify the new world_state entries**

```bash
curl -s -H "X-API-Key: $(grep AGENT_API_KEY /root/insidedcpulse-secrets/agents/threat-intel-agent.env | cut -d= -f2)" https://insidedcpulse.com/api/v1/world/state | python3 -m json.tool | grep -A1 vulnerability
```

Expected: 9 `vulnerability.<id>.*` keys with the correct types (string/enum).

- [ ] **Step 5: Verify the graph projection (if `affected_service` was set)**

Note the `<id>` and `affected_service` value from Step 3's output. If
`affected_service` is non-empty (e.g. `service.checkout` or `team.sre`):

```bash
curl -s https://insidedcpulse.com/api/v1/graph/node/vulnerability.<id> | python3 -m json.tool
curl -s https://insidedcpulse.com/api/v1/graph/neighbors/vulnerability.<id> | python3 -m json.tool
```

Expected: the node exists, and a `REFERENCES` edge connects
`vulnerability.<id>` to the `affected_service` node — proving the
zero-code-change graph linking works end-to-end. If `affected_service` was
`""` for this run, skip this step (re-run later once a matching CVE appears).

- [ ] **Step 6: Add the cron entry**

```bash
(crontab -l; echo "15 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/threat_intel_agent.py /root/insidedcpulse-secrets/agents/threat-intel-agent.env >> /root/insidedcpulse-secrets/agents/logs/threat-intel-agent.log 2>&1") | crontab -
crontab -l
```

Expected: the new `:15` line appears alongside the existing 5 persona
entries.

- [ ] **Step 7: Re-run a couple more times to exercise FIFO eviction (optional, can happen naturally via cron)**

```bash
cd /root/insidedcpulse-world-model/scripts/agents
python3 threat_intel_agent.py
python3 threat_intel_agent.py
```

Watch for: once `vulnerability.*` reaches 10 entries, the next accepted run
should include 9 `delete` ops for the oldest entry (by `fetched_at`) alongside
the 9 new `set` ops. This is the same FIFO boundary that previously had a
real bug in `research_agent.py` (already fixed there, and this script copies
the fixed pattern verbatim) — confirm the world_state stays at exactly 10
entries (90 keys) after eviction kicks in.

---

## Self-Review

**Spec coverage** — every section of
`docs/superpowers/specs/2026-06-14-threat-intel-agent-design.md` maps to a
task: entity schema (Task 2), domain validation tests (Task 3),
ENTITY_SCHEMA_TEXT (Task 4), agent script incl. sanitization/stack-match/
severity/FIFO (Task 5), README + llms.txt (Tasks 6-7), cron `:15` (Task 9
Step 6), live verification incl. graph `REFERENCES` edge and FIFO eviction
(Task 9 Steps 3-5, 7).

**Placeholder scan** — no TBD/TODO; all code blocks are complete and
copy-pasteable; `<TOKEN>` placeholders are intentional (real secret, must not
be committed — same pattern as the prior sub-project's push steps).

**Type consistency** — `VULNERABILITY_FIELDS` (Task 5) matches the 9 fields
defined in `ENTITY_SCHEMAS["vulnerability"]` (Task 2) and the
`ENTITY_SCHEMA_TEXT` block (Task 4); `sanitize_cve_id`/`match_stack`/severity
derivation match the spec's tables exactly; FIFO eviction logic matches
`research_agent.py`'s (already-fixed) pattern verbatim.

---

## Execution Choice

Plan complete and saved to
`docs/superpowers/plans/2026-06-14-threat-intel-agent.md`. Two execution
options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task,
   review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using
   executing-plans, batch execution with checkpoints.

Which approach?
