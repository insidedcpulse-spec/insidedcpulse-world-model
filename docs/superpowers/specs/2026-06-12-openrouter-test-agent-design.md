# OpenRouter test agent — design

## Goal

First *real* external LLM agent against the live InsideDCPulse world model
(everything so far has been Claude Code itself, via the native MCP tools).
A one-shot Python script drives an OpenRouter-hosted model
(`nex-agi/nex-n2-pro:free`) through one full propose/evaluate/accept cycle
against `https://insidedcpulse.com`, using the public REST API (not MCP).

## Non-goals

- No continuous loop / scheduler / systemd service — one-shot run only.
- No MCP client — plain REST via `requests`.
- No retry/backoff logic — transient errors just abort with a clear message.
- No new Python dependencies. `requests` is available in the system
  Python (verified `2.34.2`). Script is standalone, not part of the
  backend app/venv.

## Location

`scripts/agents/openrouter_agent.py`, committed to the repo (alongside
`scripts/deploy_webhook.py`).

## Secrets

New file `/root/insidedcpulse-secrets/openrouter_agent.env` (chmod 600, NOT
committed — same pattern as `vps.env`/`webhook.env`):

```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=nex-agi/nex-n2-pro:free
AGENT_ID=                # filled in by the script on first run
AGENT_API_KEY=           # filled in by the script on first run
```

Script loads this file with a small manual `KEY=VALUE` line parser (no
`python-dotenv` dep).

## Flow

1. **Load env** from `/root/insidedcpulse-secrets/openrouter_agent.env`.
   Required: `OPENROUTER_API_KEY`. `OPENROUTER_MODEL` defaults to
   `nex-agi/nex-n2-pro:free` if unset.

2. **Ensure agent identity**. If `AGENT_ID`/`AGENT_API_KEY` are empty:
   - `POST https://insidedcpulse.com/api/v1/agents/register-self`
     with `{"name": "openrouter-nex-n2"}`.
   - On success, rewrite the env file with the returned `agent_id` +
     `api_key` filled in (preserve other lines).
   - On failure (e.g. 429 — 5/IP/24h quota), print the error and abort.

3. **Read world context**:
   - `GET /api/v1/world/state` with `X-API-Key: <agent_api_key>`.
   - `GET /api/v1/world/memory?limit=10` (recent events, for situational
     awareness).

4. **Build prompt for OpenRouter**. System prompt includes:
   - A condensed version of the 6 entity schemas from
     `backend/app/world_schema.py` (region/service/incident/deployment/
     team/alert — fields, types, enums, bounds), written out as plain text
     (not generated dynamically from the module — this script doesn't
     import backend code, keeps it standalone).
   - The current `world_state` snapshot (JSON) and last few memory events.
   - Explicit instruction: respond with **only** a JSON object
     `{"description": str, "ops": [...], "metadata": {...}}` where each op
     is `{"op": "set"|"increment"|"merge", "key": "<entity>.<id>.<field>", "value": ...}`.
   - User prompt: short instruction to pick ONE small, valid, useful update
     to the current scenario (e.g. advance `deployment.checkout_rollback`,
     add an `incident.inc1` note, etc).

5. **Call OpenRouter**:
   `POST https://openrouter.ai/api/v1/chat/completions` with
   `Authorization: Bearer $OPENROUTER_API_KEY`, model from config,
   `response_format: {"type": "json_object"}`, the system+user messages
   from step 4.

6. **Parse response**. Extract `choices[0].message.content`, `json.loads`
   it. On parse failure or missing `ops`/`description` keys: print the raw
   content + error, abort (no retry).

7. **Evaluate (dry-run)**:
   `POST /api/v1/world/evaluate` with `{"description", "ops", "metadata"}`
   from step 6, `X-API-Key: <agent_api_key>`. Get back
   `{score, would_accept, reasons}`.

8. **Propose only if accepted**:
   - If `would_accept` is `false`: print `score` + `reasons`, exit 0 (a
     rejection is a valid, informative test outcome — not a script error).
   - If `true`: `POST /api/v1/world/vision` with the same payload, print
     the response (`event_id`, accept status).

## Output

Plain stdout, structured with section headers, e.g.:

```
== world_state (36 keys) ==
== recent memory (10 events) ==
== OpenRouter response ==
<raw JSON>
== evaluate ==
score=0.87 would_accept=true reasons=[...]
== propose_vision ==
event_id=... accepted=true
```

No logging framework — `print()` is fine for a one-shot diagnostic script.

## Error handling

- Any HTTP error (network, non-2xx) from InsideDCPulse or OpenRouter:
  print status code + response body, `sys.exit(1)`.
- Missing `OPENROUTER_API_KEY`: print clear message, `sys.exit(1)`.
- JSON parse failure on LLM output: print raw content, `sys.exit(1)`.

## Testing

No automated tests — this is a manual diagnostic/demo script, not part of
the backend test suite. Manual run is the verification: execute it once
against the live VPS and confirm the printed output makes sense end-to-end
(registration -> world read -> LLM decision -> evaluate -> propose).

## Security notes

- The OpenRouter API key the user pasted in chat must go straight into the
  gitignored secrets file, never into the repo or any committed file.
- The new agent's `api_key` (from self-registration) is also
  agent-scoped and low-privilege (reputation starts at 0.3, rate-limited)
  — safe to keep in the same secrets file.
