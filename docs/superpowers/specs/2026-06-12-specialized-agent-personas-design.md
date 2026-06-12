# Specialized agent personas — design

## Goal

Move from a single one-shot OpenRouter test run to **3 always-on agent
personas**, each focused on a different slice of the world model, each
running on its own hourly cron schedule and proposing real (server-validated)
updates to the live `world_state` — continuous, low-cost "real work" instead
of a one-off demo.

## Non-goals

- No new backend code, no new endpoints, no schema changes.
- No retry/backoff if a model's output is rejected by `evaluate` — the agent
  simply does nothing that hour and tries again next cycle.
- No shared "orchestrator" process — each persona is an independent cron job
  running the same script with a different env file.
- No paid OpenRouter models — all 3 personas use `:free` models.

## Components

### 1. `scripts/agents/openrouter_agent.py` (extended, in place)

Same propose/evaluate/accept flow as today
(`docs/superpowers/plans/2026-06-12-openrouter-test-agent.md`), with two
additions:

- **`AGENT_NAME` and `PERSONA_FOCUS` from env file**. `AGENT_NAME` is used as
  the `name` in `register-self` (was previously a hardcoded constant).
  `PERSONA_FOCUS` is a short string injected into the system prompt, e.g.:

  > "You are an SRE on-call coordinator. Prefer ops on `team.*` and
  > `incident.*` keys — update on-call rotations, headcount, and incident
  > status/notes as situations evolve."

  The full `ENTITY_SCHEMA_TEXT` (all 6 entities) stays in the prompt
  unchanged — focus is steering only, the server validates identically for
  every agent regardless of which namespace it touches.

- **Lenient JSON parsing**. Some free models don't honor
  `response_format: json_object` exactly and wrap output in ```` ```json ```` 
  fences or add stray text. Before `sys.exit(1)` on `json.JSONDecodeError`,
  strip a leading/trailing ```` ``` ```` fence (with optional `json` tag) and
  retry `json.loads` once. If that still fails, abort as today (print raw
  content, exit 1) — no further retries.

`build_prompt(world_state, memory, persona_focus)` signature gains the new
param; the hardcoded "advance deployment.checkout_rollback..." example
sentence is replaced by `persona_focus`.

### 2. Per-persona secrets: `/root/insidedcpulse-secrets/agents/*.env`

New directory (chmod 700), 3 files (chmod 600), **not committed** (same
gitignore pattern as the existing single `openrouter_agent.env`):

```
/root/insidedcpulse-secrets/agents/sre-agent.env
/root/insidedcpulse-secrets/agents/deploy-agent.env
/root/insidedcpulse-secrets/agents/alert-agent.env
```

Each file:

```
OPENROUTER_API_KEY=<same key as existing openrouter_agent.env>
OPENROUTER_MODEL=<persona-specific free model>
AGENT_NAME=<persona name>
PERSONA_FOCUS=<persona prompt text>
AGENT_ID=                # filled in by script on first run
AGENT_API_KEY=           # filled in by script on first run
```

`AGENT_ID`/`AGENT_API_KEY` start **empty** — each persona self-registers its
own identity on first run via `register-self` (3 calls total, well within the
5/IP/24h self-serve quota), then `save_env` persists the returned id/key back
into its own file. No identity is shared across personas or with the existing
`openrouter-nex-n2` agent.

### 3. Personas

| Persona | Focus namespaces | Model |
|---|---|---|
| `sre-agent` | `team.*`, `incident.*` — on-call rotation, headcount, incident status/notes | `meta-llama/llama-3.3-70b-instruct:free` |
| `deploy-agent` | `deployment.*`, `service.*` — rollout progress, service version/load/capacity/status | `qwen/qwen3-next-80b-a3b-instruct:free` |
| `alert-agent` | `alert.*`, `region.*` — alert status/severity, region capacity_forecast/status/population | `google/gemma-4-31b-it:free` |

### 4. Cron schedule

Root crontab, 3 new lines, staggered hourly so the 3 propose calls don't land
in the same minute:

```cron
 5 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/sre-agent.env >> /root/insidedcpulse-secrets/agents/logs/sre-agent.log 2>&1
20 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/deploy-agent.env >> /root/insidedcpulse-secrets/agents/logs/deploy-agent.log 2>&1
35 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/alert-agent.env >> /root/insidedcpulse-secrets/agents/logs/alert-agent.log 2>&1
```

`/root/insidedcpulse-secrets/agents/logs/` created alongside the env files
(outside the repo — no gitignore entry needed).

## Error handling

Unchanged from the existing script: any non-2xx HTTP response from
InsideDCPulse or OpenRouter prints status+body and `sys.exit(1)` (cron just
logs a failed run, no alerting). A rejected `evaluate` (`would_accept:
false`) is a normal, logged outcome — exit 0, no propose call.

## Testing

No automated tests (same as the existing script — manual diagnostic tool, not
part of `backend/tests`). Verification:

1. Run each of the 3 env files manually once: confirm self-registration
   (agent_id/api_key written into its file), a valid persona-scoped JSON op
   from its model, an `evaluate` result, and (if accepted) a successful
   `propose_vision`.
2. Install the 3 crontab lines, confirm `crontab -l` shows them.
3. Spot-check `get_world_memory` after the first scheduled runs to confirm
   events are appearing under the new agent ids with persona-appropriate
   `description`/`ops`.

## Security notes

- `OPENROUTER_API_KEY` reused across all 3 persona env files is the existing
  key from `openrouter_agent.env` (already flagged for rotation separately —
  unrelated to this change, same key either way).
- Each persona's self-registered `agent_api_key` is low-privilege
  (reputation starts 0.3, rate-limited 30 writes/min / 120 reads/min per
  agent) — safe in the per-persona env files, same pattern as existing
  secrets.
