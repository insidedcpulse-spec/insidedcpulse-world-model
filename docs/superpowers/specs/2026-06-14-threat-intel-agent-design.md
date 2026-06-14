# Threat-intel agent (6th persona) — design spec

Date: 2026-06-14

## Summary

A 6th always-on persona, `threat-intel-agent` — deterministic (no LLM, same
class as `research-agent`), pulls actively-exploited CVEs from CISA's Known
Exploited Vulnerabilities (KEV) catalog into a new `vulnerability.*` entity in
`world_state` + the knowledge graph. Each entry is checked against a small
static map of InsideDCPulse's own pinned stack components (nginx, postgres,
redis, grafana, prometheus, certbot, fastapi/starlette/mcp); a match sets
`affected_service` (an existing `REFERENCE_FIELDS` member), which
`graph_projection.py` already turns into a `REFERENCES` edge to the matching
`service.*` or `team.sre` node — **zero projection-code changes needed**.

This is the first persona whose feed has direct operational relevance to
InsideDCPulse's own deployed infrastructure (vs. arXiv research feeds), and
the first real "cybersecurity" use case for the agent-persona system.

## Data source

CISA KEV JSON feed:
`https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`

- No auth, no rate limit observed. Verified reachable (200) and structure
  2026-06-14 — `count: 1619`, `vulnerabilities: [...]`, each entry:
  `cveID`, `vendorProject`, `product`, `vulnerabilityName`, `dateAdded`,
  `shortDescription`, `requiredAction`, `dueDate`, `knownRansomwareCampaignUse`
  (`"Known"` or `"Unknown"`), `notes`, `cwes`.
- Fetched fresh on every run via `urllib`/`requests` (no CLI tool needed,
  unlike `arxiv-pp-cli` — plain JSON over HTTPS).

## World schema: new `vulnerability` entity

Added to `ENTITY_SCHEMAS` in `backend/app/world_schema.py`, key pattern
`vulnerability.<cve_id>.<field>` (e.g. `vulnerability.cve_2026_35273.severity`):

| Field | Type | Source / derivation |
|---|---|---|
| `cve_id` | string | `cveID` verbatim (e.g. `"CVE-2026-35273"`) |
| `product` | string | `f"{vendorProject.strip()} {product.strip()}"`, truncated to 200 chars |
| `summary` | string | `shortDescription`, truncated to 500 chars (same as `research.summary`) |
| `severity` | enum `[high, critical]` | `critical` if `knownRansomwareCampaignUse == "Known"`, else `high` |
| `date_added` | string | `dateAdded` verbatim (`YYYY-MM-DD`) |
| `stack_match` | string | matched component label (e.g. `"redis:7-alpine"`), `""` if no match |
| `affected_service` | string | `"service.<id>"` or `"team.sre"` if matched, `""` otherwise — existing `REFERENCE_FIELDS` member |
| `url` | string | `f"https://nvd.nist.gov/vuln/detail/{cve_id}"` |
| `fetched_at` | string | ISO 8601 UTC timestamp, same as other personas |

`affected_service` reuses the existing `REFERENCE_FIELDS = {"affected_service",
"affected_region", "source_service", "target_service"}` mechanism in
`graph_projection.py` — when non-empty and matches `<entity>.<id>`,
`project_event` automatically creates a `REFERENCES` edge
`vulnerability.<cve_id> -> service.<id>` (or `-> team.sre`). No changes to
`graph_projection.py` required.

### CVE ID sanitization

`"CVE-2026-35273"` -> `"cve_2026_35273"` (lowercase, `-` -> `_`). Always
`cve_<year>_<number>`, well under the 32-char entity-id limit.

## Stack-match table

Case-insensitive substring scan over
`f"{vendorProject} {product} {vulnerabilityName} {shortDescription}"`,
first match wins (checked in table order):

| Keyword(s) | `stack_match` | `affected_service` |
|---|---|---|
| `nginx` | `nginx:1.27-alpine` | `service.checkout` |
| `postgres`, `postgresql` | `postgres:16-alpine` | `service.payments_db` |
| `redis` | `redis:7-alpine` | `service.checkout` |
| `grafana` | `grafana:13.0.2` | `team.sre` |
| `prometheus` | `prometheus:v3.12.0` | `team.sre` |
| `certbot`, `let's encrypt`, `acme` | `certbot:v5.6.0` | `team.sre` |
| `fastapi`, `starlette`, `uvicorn`, `mcp` | `fastapi/starlette/mcp` | `team.sre` |
| (no match) | `""` | `""` |

This is intentionally a small, hand-maintained, best-effort map — not a real
CPE/SBOM match. It demonstrates the linking mechanism and surfaces
*potentially* relevant CVEs; false positives/negatives are expected and
acceptable (informational feed, not an alerting/paging system).

## Agent script: `scripts/agents/threat_intel_agent.py`

Mirrors `research_agent.py` exactly (deterministic, reuses
`ensure_agent`/`get_world_state`/`evaluate_vision`/`propose_vision`/
`load_env`/`save_env` from `openrouter_agent.py`):

1. Load env (`/root/insidedcpulse-secrets/agents/threat-intel-agent.env`),
   `ensure_agent` (self-registers `threat-intel-agent-xxxxxx` on first run).
2. `get_world_state`, collect existing `vulnerability.<id>` ids +
   `fetched_at` (for FIFO, same as `research.*`).
3. Fetch CISA KEV JSON. Sort `vulnerabilities` by `dateAdded` descending.
4. Walk the sorted list, pick the first `cveID` whose sanitized id is not
   already in `vulnerability.*`.
   - If all returned entries already exist (extremely unlikely with 1619
     entries and 10-slot FIFO, but mirrors `research_agent`'s no-op
     handling): print "no new CVE" and exit 0.
5. Compute `severity`, `stack_match`/`affected_service` per tables above.
6. Build `ops`: 9 `set` ops for the new `vulnerability.<id>.*`. If
   `len(existing) + 1 > 10`, append `delete` ops for the oldest entry's 9
   fields (oldest = min `fetched_at`, same pattern as `research_agent`).
7. `evaluate_vision` -> if `would_accept`, `propose_vision`; else print and
   no-op (same as `research_agent`).

`event_type: "vision"`, `metadata: {"source": "threat-intel-agent", "cve_id":
..., "stack_match": ...}`.

## Cron

New staggered slot `:15` (existing: `:05` sre, `:20` deploy, `:35` alert,
`:40` ai-research, `:50` research) — `>>
/root/insidedcpulse-secrets/agents/logs/threat-intel-agent.log 2>&1`.

## Docs updates (pure-additive, same pattern as prior entity expansions)

- `backend/app/world_schema.py`: add `"vulnerability"` to `ENTITY_SCHEMAS`.
- `scripts/agents/openrouter_agent.py`: add `vulnerability` to
  `ENTITY_SCHEMA_TEXT` (LLM-facing schema description used by the 3 LLM
  personas, so they're aware of the new namespace even though this agent
  itself doesn't use an LLM).
- `README.md`: add `vulnerability` row to the world state schema table +
  one-line description in a "Test agents" / persona list.
- `docker/nginx/static/llms.txt`: add `vulnerability.*` to the rotating-feeds
  description alongside `research.*`/`finding.*`.

## Testing

Following the "World schema entity expansion" precedent
(`test_world_schema.py` + `test_domain_validation.py`, pure-additive, zero
validation-engine changes since `ENTITY_SCHEMAS` is fully generic):

- `test_world_schema.py`: assert `"vulnerability"` in `ENTITY_SCHEMAS` with
  the 9 fields/types/enum above.
- `test_domain_validation.py`: a handful of `check_domain_consistency` cases
  — valid `set` ops for each field type (string/enum), invalid enum value
  for `severity` rejected, `affected_service`/`stack_match` accept arbitrary
  strings (including `""`).

`scripts/agents/threat_intel_agent.py` itself is **not** unit-tested (same as
`research_agent.py`/`ai_research_agent.py` — no `backend/tests` coverage for
`scripts/agents/`). Verified via live runs instead:

1. Run manually once, registers `threat-intel-agent-xxxxxx`, proposes 1
   `vulnerability.*` entry, `would_accept: true`.
2. Verify via `get_world_state` (9 fields present, correct types) and
   `GET /api/v1/graph/node/vulnerability.<id>` (if `affected_service` was
   set, a `REFERENCES` edge to `service.*`/`team.sre` exists — proves the
   zero-code-change graph linking works end-to-end).
3. Re-run a few more times to exercise FIFO eviction at 10 entries (same
   "real bug only at boundary" lesson from `research-agent` — watch for it,
   though the bug there was in `research_agent.py`'s own min() logic which
   this script copies verbatim, already fixed).

## Out of scope / explicitly not doing

- No NVD CVE API calls (KEV feed alone is sufficient signal: "actively
  exploited").
- No automated remediation, alerting, paging, or write-access to any real
  infrastructure — purely an informational feed into `world_state` +
  knowledge graph, same trust level as `research.*`/`finding.*`.
- No CVSS scoring / real CPE matching — `stack_match` is a best-effort
  keyword heuristic, documented as such.
- `vulnerability.*` entries do not affect `world_state` validation/scoring
  of *other* entities (no cross-entity consistency rules) — purely additive
  namespace, same as `research`/`finding`.
