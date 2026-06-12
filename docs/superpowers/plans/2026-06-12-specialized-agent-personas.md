# Specialized Agent Personas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the one-shot OpenRouter test agent into 3 always-on, persona-scoped agents (SRE / deploy / alerting) that each self-register their own identity and run hourly via cron, proposing real server-validated updates to the live `world_state`.

**Architecture:** Extend `scripts/agents/openrouter_agent.py` with `PERSONA_FOCUS`-driven prompts and lenient JSON parsing (no behavior change for the existing single agent if `PERSONA_FOCUS` is unset). Add 3 new per-persona secrets files under `/root/insidedcpulse-secrets/agents/` (each with its own free OpenRouter model + empty agent identity, self-registered on first run). Wire up 3 staggered hourly cron entries.

**Tech Stack:** Python 3 (`requests`, stdlib `json`/`pathlib`), bash/cron, InsideDCPulse REST API (`https://insidedcpulse.com`), OpenRouter chat completions API.

Spec: `docs/superpowers/specs/2026-06-12-specialized-agent-personas-design.md`

---

### Task 1: Extend `openrouter_agent.py` with persona focus + lenient JSON parsing

**Files:**
- Modify: `scripts/agents/openrouter_agent.py`

- [ ] **Step 1: Add `DEFAULT_PERSONA_FOCUS` constant**

In `scripts/agents/openrouter_agent.py`, replace:

```python
BASE_URL = "https://insidedcpulse.com"
ENV_PATH = Path("/root/insidedcpulse-secrets/openrouter_agent.env")
DEFAULT_MODEL = "nex-agi/nex-n2-pro:free"
AGENT_NAME = "openrouter-nex-n2"

ENTITY_SCHEMA_TEXT = """\
```

with:

```python
BASE_URL = "https://insidedcpulse.com"
ENV_PATH = Path("/root/insidedcpulse-secrets/openrouter_agent.env")
DEFAULT_MODEL = "nex-agi/nex-n2-pro:free"
AGENT_NAME = "openrouter-nex-n2"
DEFAULT_PERSONA_FOCUS = (
    "Pick ONE small, valid, useful update to the current scenario "
    "(e.g. advance deployment.checkout_rollback.progress, add an "
    "incident.inc1 note, update an alert status)."
)

ENTITY_SCHEMA_TEXT = """\
```

- [ ] **Step 2: Update `build_prompt` to take a `persona_focus` argument**

Replace the whole `build_prompt` function:

```python
def build_prompt(world_state: dict, memory: dict) -> tuple[str, str]:
    system_msg = (
        "You are an autonomous agent proposing small, valid updates to a "
        "shared infrastructure world model.\n\n"
        f"{ENTITY_SCHEMA_TEXT}\n"
        "Respond with ONLY a JSON object: "
        '{"description": str, "ops": [...], "metadata": {}}. '
        "No prose, no markdown fences."
    )
    user_msg = (
        "Current world state:\n"
        f"{json.dumps(world_state, indent=2)}\n\n"
        "Recent events:\n"
        f"{json.dumps(memory, indent=2)}\n\n"
        "Pick ONE small, valid, useful update to the current scenario "
        "(e.g. advance deployment.checkout_rollback.progress, add an "
        "incident.inc1 note, update an alert status) and respond with the "
        "JSON object described above."
    )
    return system_msg, user_msg
```

with:

```python
def build_prompt(world_state: dict, memory: dict, persona_focus: str) -> tuple[str, str]:
    system_msg = (
        "You are an autonomous agent proposing small, valid updates to a "
        "shared infrastructure world model.\n\n"
        f"{ENTITY_SCHEMA_TEXT}\n"
        "Respond with ONLY a JSON object: "
        '{"description": str, "ops": [...], "metadata": {}}. '
        "No prose, no markdown fences."
    )
    user_msg = (
        "Current world state:\n"
        f"{json.dumps(world_state, indent=2)}\n\n"
        "Recent events:\n"
        f"{json.dumps(memory, indent=2)}\n\n"
        f"{persona_focus} Respond with the JSON object described above."
    )
    return system_msg, user_msg
```

- [ ] **Step 3: Add `_parse_json_content` helper and use it in `call_openrouter`**

Replace:

```python
def call_openrouter(api_key: str, model: str, system_msg: str, user_msg: str) -> dict:
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    if resp.status_code >= 300:
        print(f"OpenRouter call failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"failed to parse OpenRouter response as JSON: {exc}")
        print(f"raw content: {content}")
        sys.exit(1)
```

with:

```python
def _parse_json_content(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        stripped = content.strip().strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
        return json.loads(stripped)


def call_openrouter(api_key: str, model: str, system_msg: str, user_msg: str) -> dict:
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    if resp.status_code >= 300:
        print(f"OpenRouter call failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return _parse_json_content(content)
    except json.JSONDecodeError as exc:
        print(f"failed to parse OpenRouter response as JSON: {exc}")
        print(f"raw content: {content}")
        sys.exit(1)
```

- [ ] **Step 4: Wire `PERSONA_FOCUS` from env into `main()`**

In `main()`, replace:

```python
    memory = get_world_memory(agent_api_key, limit=10)
    print("== recent memory ==")
    print(json.dumps(memory, indent=2))

    system_msg, user_msg = build_prompt(world_state, memory)
```

with:

```python
    memory = get_world_memory(agent_api_key, limit=10)
    print("== recent memory ==")
    print(json.dumps(memory, indent=2))

    persona_focus = env.get("PERSONA_FOCUS") or DEFAULT_PERSONA_FOCUS
    system_msg, user_msg = build_prompt(world_state, memory, persona_focus)
```

- [ ] **Step 5: Local verification (no network)**

Run:

```bash
cd /root/insidedcpulse-world-model && python3 - <<'EOF'
import sys
sys.path.insert(0, "scripts/agents")
import openrouter_agent as oa

assert oa._parse_json_content('{"a": 1}') == {"a": 1}
assert oa._parse_json_content("```json\n{\"a\": 1}\n```") == {"a": 1}
assert oa._parse_json_content("```\n{\"a\": 1}\n```") == {"a": 1}

_, user_msg = oa.build_prompt({"state": {}}, {"events": []}, "FOCUS_TEXT_HERE")
assert "FOCUS_TEXT_HERE" in user_msg

_, default_user_msg = oa.build_prompt({"state": {}}, {"events": []}, oa.DEFAULT_PERSONA_FOCUS)
assert oa.DEFAULT_PERSONA_FOCUS in default_user_msg

print("ok")
EOF
```

Expected: `ok` printed, no `AssertionError`/`Traceback`.

- [ ] **Step 6: Commit**

```bash
cd /root/insidedcpulse-world-model && git add scripts/agents/openrouter_agent.py && git commit -m "Support persona-scoped prompts + lenient JSON parsing in OpenRouter agent"
```

---

### Task 2: Create per-persona secrets files + logs dir

**Files:**
- Create: `/root/insidedcpulse-secrets/agents/sre-agent.env` (not in repo)
- Create: `/root/insidedcpulse-secrets/agents/deploy-agent.env` (not in repo)
- Create: `/root/insidedcpulse-secrets/agents/alert-agent.env` (not in repo)
- Create: `/root/insidedcpulse-secrets/agents/logs/` (not in repo)

- [ ] **Step 1: Create the directory and 3 env files, reusing the existing OpenRouter key**

Run as a single command (keeps the key in a shell variable, never prints it):

```bash
mkdir -p /root/insidedcpulse-secrets/agents/logs
chmod 700 /root/insidedcpulse-secrets/agents

OR_KEY=$(grep '^OPENROUTER_API_KEY=' /root/insidedcpulse-secrets/openrouter_agent.env | cut -d= -f2-)

cat > /root/insidedcpulse-secrets/agents/sre-agent.env <<EOF
OPENROUTER_API_KEY=$OR_KEY
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
AGENT_NAME=sre-agent
PERSONA_FOCUS=You are an SRE on-call coordinator. Prefer ops on team.* and incident.* keys: update on-call rotations, headcount, and incident status/notes as situations evolve.
AGENT_ID=
AGENT_API_KEY=
EOF

cat > /root/insidedcpulse-secrets/agents/deploy-agent.env <<EOF
OPENROUTER_API_KEY=$OR_KEY
OPENROUTER_MODEL=qwen/qwen3-next-80b-a3b-instruct:free
AGENT_NAME=deploy-agent
PERSONA_FOCUS=You are a deployment/release engineer. Prefer ops on deployment.* and service.* keys: advance rollout progress, and update service version/load/capacity/status as deploys proceed.
AGENT_ID=
AGENT_API_KEY=
EOF

cat > /root/insidedcpulse-secrets/agents/alert-agent.env <<EOF
OPENROUTER_API_KEY=$OR_KEY
OPENROUTER_MODEL=google/gemma-4-31b-it:free
AGENT_NAME=alert-agent
PERSONA_FOCUS=You are a monitoring/alerting engineer. Prefer ops on alert.* and region.* keys: update alert status/severity as conditions change, and region capacity_forecast/status/population.
AGENT_ID=
AGENT_API_KEY=
EOF

chmod 600 /root/insidedcpulse-secrets/agents/*.env
unset OR_KEY
```

- [ ] **Step 2: Verify files (without printing the key)**

```bash
for f in sre-agent deploy-agent alert-agent; do
  echo "== $f =="
  grep -v OPENROUTER_API_KEY /root/insidedcpulse-secrets/agents/$f.env
done
ls -la /root/insidedcpulse-secrets/agents/
```

Expected: each section shows `OPENROUTER_MODEL=...`, `AGENT_NAME=...`, `PERSONA_FOCUS=...`, `AGENT_ID=`, `AGENT_API_KEY=` (last two empty); `ls` shows 3 `.env` files (`-rw-------`) and `logs/` dir.

No commit — this directory is outside the repo and not tracked.

---

### Task 3: First run — `sre-agent` (self-register + propose)

**Files:** none (uses `scripts/agents/openrouter_agent.py` from Task 1 + `sre-agent.env` from Task 2)

- [ ] **Step 1: Run the agent**

```bash
cd /root/insidedcpulse-world-model && python3 scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/sre-agent.env
```

Expected: `== agent: <new-uuid> ==`, world_state/memory dumps, `== OpenRouter response ==` with a JSON object containing `ops` touching `team.*`/`incident.*`, `== evaluate ==` with a `score`/`would_accept`. If `would_accept: true`, also `== propose_vision ==` with an `event_id`. If `would_accept: false`, ends with "Validator would reject this vision — not proposing." — both are a pass.

- [ ] **Step 2: Confirm self-registration persisted**

```bash
grep -E '^(AGENT_NAME|AGENT_ID)=' /root/insidedcpulse-secrets/agents/sre-agent.env
```

Expected: `AGENT_NAME=sre-agent` and `AGENT_ID=<non-empty uuid>`.

---

### Task 4: First run — `deploy-agent` (self-register + propose)

**Files:** none (uses `scripts/agents/openrouter_agent.py` from Task 1 + `deploy-agent.env` from Task 2)

- [ ] **Step 1: Run the agent**

```bash
cd /root/insidedcpulse-world-model && python3 scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/deploy-agent.env
```

Expected: same shape as Task 3 — `== agent: <new-uuid> ==`, an OpenRouter JSON response with `ops` touching `deployment.*`/`service.*`, an `== evaluate ==` result, and a propose if accepted.

- [ ] **Step 2: Confirm self-registration persisted**

```bash
grep -E '^(AGENT_NAME|AGENT_ID)=' /root/insidedcpulse-secrets/agents/deploy-agent.env
```

Expected: `AGENT_NAME=deploy-agent` and `AGENT_ID=<non-empty uuid>`.

---

### Task 5: First run — `alert-agent` (self-register + propose)

**Files:** none (uses `scripts/agents/openrouter_agent.py` from Task 1 + `alert-agent.env` from Task 2)

- [ ] **Step 1: Run the agent**

```bash
cd /root/insidedcpulse-world-model && python3 scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/alert-agent.env
```

Expected: same shape as Task 3 — `== agent: <new-uuid> ==`, an OpenRouter JSON response with `ops` touching `alert.*`/`region.*`, an `== evaluate ==` result, and a propose if accepted.

- [ ] **Step 2: Confirm self-registration persisted**

```bash
grep -E '^(AGENT_NAME|AGENT_ID)=' /root/insidedcpulse-secrets/agents/alert-agent.env
```

Expected: `AGENT_NAME=alert-agent` and `AGENT_ID=<non-empty uuid>`.

---

### Task 6: Install hourly cron jobs

**Files:** none (root crontab)

- [ ] **Step 1: Confirm `python3` path**

```bash
which python3
```

Expected: `/usr/bin/python3`. If different, use that path in Step 2 instead.

- [ ] **Step 2: Append the 3 staggered hourly entries to root's crontab**

```bash
(crontab -l 2>/dev/null; cat <<'EOF'
 5 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/sre-agent.env >> /root/insidedcpulse-secrets/agents/logs/sre-agent.log 2>&1
20 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/deploy-agent.env >> /root/insidedcpulse-secrets/agents/logs/deploy-agent.log 2>&1
35 * * * * /usr/bin/python3 /root/insidedcpulse-world-model/scripts/agents/openrouter_agent.py /root/insidedcpulse-secrets/agents/alert-agent.env >> /root/insidedcpulse-secrets/agents/logs/alert-agent.log 2>&1
EOF
) | crontab -
```

- [ ] **Step 3: Verify**

```bash
crontab -l
```

Expected: previous crontab entries (if any) preserved, plus the 3 new lines at `:05`/`:20`/`:35` past every hour, each pointing at one persona's env file and its own log file under `/root/insidedcpulse-secrets/agents/logs/`.

---

## Self-Review Notes

- **Spec coverage**: script changes (Task 1), 3 persona secrets files + models + focuses (Task 2), 3 self-registrations + first proposals (Tasks 3-5), staggered hourly cron (Task 6) — all spec sections covered.
- **Backward compatibility**: existing `/root/insidedcpulse-secrets/openrouter_agent.env` has no `PERSONA_FOCUS` key, so `env.get("PERSONA_FOCUS") or DEFAULT_PERSONA_FOCUS` falls back to the original wording verbatim — the existing `openrouter-nex-n2` agent's behavior is unchanged.
- **Secret hygiene**: Task 2 never echoes `OPENROUTER_API_KEY`; Task 3-5 verification greps exclude it.
