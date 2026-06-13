# arXiv research-agent — design

## Goal

Add a 4th always-on persona, `research-agent`, that pulls real papers from
arXiv (via a CLI generated with `cli-printing-press`) and writes them into
`world_state` as a new `research.*` entity — giving the world model a live
feed of SRE/ops-relevant research literature.

## Non-goals

- No LLM call for this persona (deterministic script, unlike
  `sre`/`deploy`/`alert` personas — see
  `2026-06-12-specialized-agent-personas-design.md`). The arXiv feed itself is
  the new information; no LLM commentary is added.
- No dynamic/world-state-driven query construction — fixed rotating topic
  list only.
- No changes to `check_op_consistency` / `check_domain_consistency` —
  `research` is a pure additive entry in `ENTITY_SCHEMAS`, same pattern as
  the `incident`/`deployment`/`team`/`alert` expansion.
- No auth needed for arXiv's public API — the generated CLI calls
  `export.arxiv.org/api/query` unauthenticated.

## Components

### 1. `arxiv-pp-cli` (generated via `/printing-press`)

Generated into `$PRESS_LIBRARY/arxiv-pp-cli`, built with
`go install ./cmd/arxiv-pp-cli` → `~/go/bin/arxiv-pp-cli`. Must expose a
search command returning JSON with at least: `id` (arXiv URL/ID), `title`,
`summary` (abstract), `published` (date), `link` (abs page URL), for a
free-text query against arXiv's `search_query` (e.g.
`all:"site reliability engineering"`), sorted by relevance or submission
date, `max_results` capped (e.g. 5).

Exact flag names are whatever `/printing-press` produces from arXiv's API —
`research_agent.py` (component 3) is written against the actual generated
`--help` output, not assumed in advance.

**Verified generated interface** (built 2026-06-13, `~/go/bin/arxiv-pp-cli`):

```
arxiv-pp-cli query --search-query '<query>' --max-results 5 --json
```

Output is a JSON envelope, not pre-parsed paper fields:

```json
{"meta": {"source": "live"}, "results": "<?xml version='1.0' ...?><feed xmlns=\"http://www.w3.org/2005/Atom\" ...>...<entry>...</entry>...</feed>"}
```

`results` is the raw Atom 1.0 XML feed as a string (the spec's response
content-type is `application/atom+xml`, so the generated client passes the
body through unparsed). `research_agent.py` (component 3) therefore parses
this XML itself via `xml.etree.ElementTree` (stdlib, no new dependency) —
each `<entry>` has `<id>`, `<title>`, `<summary>`, `<published>`, and a
`<link rel="alternate" type="text/html" href="...">` for the abs page URL,
all under the `http://www.w3.org/2005/Atom` namespace.

### 2. New `research` entity — `backend/app/world_schema.py`

Pure-additive entry in `ENTITY_SCHEMAS`, no validation-engine changes (same
as the 2026-06-11 entity expansion):

```python
"research": {
    "title": {"type": "string"},
    "summary": {"type": "string"},
    "topic": {"type": "string"},
    "published": {"type": "string"},
    "url": {"type": "string"},
    "fetched_at": {"type": "string"},
},
```

`<id>` = sanitized arXiv ID matching `[a-z0-9_]{1,32}`: take the numeric/path
portion of the arXiv ID (drop `vN` version suffix), lowercase, replace `.`
and `/` with `_` (e.g. `2506.01234v2` → `2506_01234`, `cs.AI/0601001` →
`cs_ai_0601001`).

`ENTITY_SCHEMA_TEXT` in `scripts/agents/openrouter_agent.py` gains the
`research.*` rows too (the 3 LLM personas should know the namespace exists,
even if they're not the ones writing it — keeps the prompt's schema picture
complete and matches how the 2026-06-11 expansion was documented).

### 3. `scripts/agents/research_agent.py` (new script)

Deterministic, no LLM. Reuses `load_env`/`save_env`/`ensure_agent`/
`get_world_state`/`evaluate_vision`/`propose_vision` from `openrouter_agent.py`
via `from openrouter_agent import ...` — both scripts live in
`scripts/agents/`, and Python puts the invoked script's directory first on
`sys.path`, so this works for direct execution and for the cron invocation
(absolute path to `research_agent.py`) without any path hacks. None of the
reused helpers touch OpenRouter, so no unused dependency is pulled in.

Fixed topic list (module constant, rotates one per run):

```python
TOPICS = [
    "site reliability engineering",
    "anomaly detection time series",
    "incident response automation",
    "distributed systems fault tolerance",
    "chaos engineering",
]
```

Flow:

1. Load env (`/root/insidedcpulse-secrets/agents/research-agent.env`),
   `ensure_agent` (self-register on first run, same as other personas).
2. `get_world_state` → collect existing `research.<id>.*` ids into a set, and
   `research.<id>.fetched_at` values for FIFO comparison.
3. Pick this run's topic: persisted `TOPIC_INDEX` in the env file, `% len(TOPICS)`,
   incremented and saved back each run (simple round-robin, survives restarts).
4. Run `arxiv-pp-cli query --search-query 'all:"<topic>"' --max-results 5
   --json` via `subprocess.run`, `json.loads` the stdout envelope, then parse
   its `results` string (raw Atom XML) with `xml.etree.ElementTree`, reading
   `<entry>` elements under the `http://www.w3.org/2005/Atom` namespace.
5. Iterate entries in order, sanitize each `<id>`; pick the first whose
   sanitized id is **not** already in `research.*`. If all 5 are already
   present, exit 0 (no-op, logged) — try again next hour (topic will have
   rotated).
6. Build ops for the chosen paper:
   ```
   set research.<id>.title      <title>
   set research.<id>.summary    <abstract, truncated to 500 chars>
   set research.<id>.topic      <this run's topic string>
   set research.<id>.published  <published date from arXiv>
   set research.<id>.url        <abs link>
   set research.<id>.fetched_at <now, ISO 8601 UTC>
   ```
7. FIFO eviction: if the number of distinct `research.*` ids **after adding
   the new one** exceeds 10, find the existing id with the oldest
   `fetched_at` and append 6 `delete` ops for its keys (`research.<old_id>.*`
   for all 6 fields above).
8. `payload = {"description": ..., "ops": [...], "metadata": {"source":
   "research-agent", "topic": <topic>}}` — `event_type` defaults to
   `"vision"` (per the 2026-06-11 constraint, must be the literal `"vision"`
   or `"action"`).
9. `evaluate_vision` → if `would_accept`, `propose_vision`; else log and exit
   0 (same "do nothing this cycle" pattern as the LLM personas).

Total ops per run: 6 (new paper) + up to 6 (eviction) = 12, under the 20/call
cap.

### 4. Secrets

New `/root/insidedcpulse-secrets/agents/research-agent.env` (chmod 600, not
committed — same gitignore pattern as the other 3 persona env files):

```
AGENT_NAME=research-agent
AGENT_ID=                # filled in by script on first run
AGENT_API_KEY=           # filled in by script on first run
TOPIC_INDEX=0            # rotates each run
```

No OpenRouter key needed — arXiv's API is unauthenticated.

### 5. Cron schedule

4th staggered hourly slot, root crontab:

```cron
50 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/research_agent.py /root/insidedcpulse-secrets/agents/research-agent.env >> /root/insidedcpulse-secrets/agents/logs/research-agent.log 2>&1
```

(existing slots: `:05` sre, `:20` deploy, `:35` alert — `:50` keeps all 4
spread across the hour.)

### 6. Docs

- `README.md`: extend the world-schema table with the `research` entity row,
  add `research-agent` to the "Test agents" / personas section.
- `llms.txt`: mention the `research.*` namespace as part of the live
  scenario / world-state description, so external agents reading it
  understand the new entity exists.

## Data flow

```
cron (:50 hourly)
  → research_agent.py
      → arxiv-pp-cli search "<topic>" --json   (no auth, public arXiv API)
      → GET /api/v1/world/state                (X-API-Key, find existing research.* ids)
      → POST /api/v1/world/evaluate            (dry-run validation)
      → POST /api/v1/world/vision              (if would_accept)
          → queue → worker → world_state updated (research.<id>.* set, oldest evicted)
```

## Error handling

Same as existing personas: any non-2xx from InsideDCPulse or a non-zero exit
/ unparseable JSON from `arxiv-pp-cli` prints diagnostics and `sys.exit(1)` —
cron just logs a failed run, tried again next hour. A rejected `evaluate`
(`would_accept: false`, e.g. if the FIFO/dedup logic produces a stale
duplicate) is a normal logged no-op, exit 0.

## Testing

No automated backend tests needed (pure-additive schema entry, covered by
existing generic `ENTITY_SCHEMAS`-driven tests from the 2026-06-11
expansion — confirm by running the existing `test_world_schema.py` /
`test_domain_validation.py` suite, which is parametrized over
`ENTITY_SCHEMAS` and should pick up `research` automatically).

Manual verification:

1. `arxiv-pp-cli <search-cmd> --query 'site reliability engineering' --json`
   returns parseable results.
2. Run `research_agent.py` once manually: confirm self-registration, a
   `research.<id>.*` set of 6 ops, `would_accept: true`, successful
   `propose_vision`.
3. `get_world_state` shows the new `research.<id>.*` keys with correct
   values.
4. Manually pre-seed 10 `research.*` entries (or run the script 11x across
   rotated topics) and confirm the 11th run emits 6 `delete` ops for the
   oldest `fetched_at` and the count stays at 10.
5. Install the cron line, confirm `crontab -l` shows all 4 personas.

## Security notes

- arXiv's public API requires no credentials — nothing new to rotate.
- `research-agent`'s self-registered `agent_api_key` is low-privilege
  (reputation 0.3, rate-limited), same as the other 3 personas.
- `arxiv-pp-cli` is invoked with a fixed query string from `TOPICS` (no
  user-controlled input), via `subprocess.run` with an argument list (no
  shell), avoiding command-injection concerns.
