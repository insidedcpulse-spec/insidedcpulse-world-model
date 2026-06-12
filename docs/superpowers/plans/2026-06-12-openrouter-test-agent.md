# OpenRouter Test Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-shot Python script (`scripts/agents/openrouter_agent.py`) that drives an OpenRouter-hosted LLM (`nex-agi/nex-n2-pro:free`) through one full propose/evaluate/accept cycle against the live InsideDCPulse REST API.

**Architecture:** Single standalone script, no new deps (`requests` already present system-wide). Self-registers a new low-privilege agent on first run via `/api/v1/agents/register-self`, reads `world/state` + `world/memory`, asks the LLM for one update, dry-runs it via `world/evaluate`, and only calls `world/vision` if the validator would accept it. Secrets live outside the repo in `/root/insidedcpulse-secrets/openrouter_agent.env`.

**Tech Stack:** Python 3, `requests`, InsideDCPulse REST API (`https://insidedcpulse.com`), OpenRouter chat completions API.

**Spec:** `docs/superpowers/specs/2026-06-12-openrouter-test-agent-design.md`

---

## File Structure

- Create: `/root/insidedcpulse-secrets/openrouter_agent.env` — secrets (NOT in repo, chmod 600)
- Create: `scripts/agents/openrouter_agent.py` — the whole script, built incrementally task by task

---

### Task 1: Secrets file + env load/save helpers

**Files:**
- Create: `/root/insidedcpulse-secrets/openrouter_agent.env`
- Create: `scripts/agents/openrouter_agent.py`

- [ ] **Step 1: Create the secrets file**

```
OPENROUTER_API_KEY=sk-or-v1-<your-key-here>
OPENROUTER_MODEL=nex-agi/nex-n2-pro:free
AGENT_ID=
AGENT_API_KEY=
```

- [ ] **Step 2: Lock down permissions**

Run: `chmod 600 /root/insidedcpulse-secrets/openrouter_agent.env`

- [ ] **Step 3: Create the script with constants + env helpers**

```python
#!/usr/bin/env python3
"""One-shot OpenRouter agent that proposes a vision to InsideDCPulse."""

import json
import sys
from pathlib import Path

import requests

BASE_URL = "https://insidedcpulse.com"
ENV_PATH = Path("/root/insidedcpulse-secrets/openrouter_agent.env")
DEFAULT_MODEL = "nex-agi/nex-n2-pro:free"
AGENT_NAME = "openrouter-nex-n2"

ENTITY_SCHEMA_TEXT = """\
World state keys follow "<entity>.<id>.<field>". Known entities and fields:

region.<id>.capacity_forecast  number >= 0
region.<id>.population          integer >= 0
region.<id>.status              enum: stable, growing, declining, critical
region.<id>.notes               object

service.<id>.status             enum: healthy, degraded, down
service.<id>.load               number 0-100
service.<id>.version            string
service.<id>.capacity           number >= 0

incident.<id>.severity          enum: low, medium, high, critical
incident.<id>.status            enum: open, mitigated, resolved
incident.<id>.affected_service  string
incident.<id>.affected_region   string
incident.<id>.notes             object

deployment.<id>.status          enum: pending, in_progress, done, failed, rolled_back
deployment.<id>.version         string
deployment.<id>.target_service  string
deployment.<id>.progress        number 0-100

team.<id>.on_call               enum: active, off
team.<id>.headcount             integer >= 0
team.<id>.owned_services        object

alert.<id>.severity             enum: info, warning, critical
alert.<id>.status               enum: firing, resolved
alert.<id>.source_service       string
alert.<id>.message              object

Valid ops: {"op": "set"|"increment"|"merge", "key": "<entity>.<id>.<field>", "value": ...}
"""


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def save_env(path: Path, env: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in env.items()]
    path.write_text("\n".join(lines) + "\n")
```

- [ ] **Step 4: Verify load_env/save_env round-trip**

Run:
```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/agents')
from pathlib import Path
from openrouter_agent import load_env, save_env

p = Path('/tmp/test_openrouter_agent.env')
save_env(p, {'A': '1', 'B': 'two'})
env = load_env(p)
assert env == {'A': '1', 'B': 'two'}, env
p.unlink()
print('OK')
"
```
Expected: `OK`

- [ ] **Step 5: Verify real secrets file loads correctly**

Run:
```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/agents')
from openrouter_agent import load_env, ENV_PATH
env = load_env(ENV_PATH)
assert env['OPENROUTER_API_KEY'].startswith('sk-or-v1-')
assert env['OPENROUTER_MODEL'] == 'nex-agi/nex-n2-pro:free'
assert env['AGENT_ID'] == ''
assert env['AGENT_API_KEY'] == ''
print('OK')
"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add scripts/agents/openrouter_agent.py
git commit -m "Add OpenRouter test agent: env load/save helpers + entity schema text"
```

(`/root/insidedcpulse-secrets/openrouter_agent.env` stays untracked — outside the repo, never `git add` it.)

---

### Task 2: Agent self-registration (`ensure_agent`)

**Files:**
- Modify: `scripts/agents/openrouter_agent.py`

- [ ] **Step 1: Append `ensure_agent`**

Find this in the file:
```python
def save_env(path: Path, env: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in env.items()]
    path.write_text("\n".join(lines) + "\n")
```

Replace with:
```python
def save_env(path: Path, env: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in env.items()]
    path.write_text("\n".join(lines) + "\n")


def ensure_agent(env: dict[str, str]) -> tuple[str, str]:
    agent_id = env.get("AGENT_ID", "")
    agent_api_key = env.get("AGENT_API_KEY", "")
    if agent_id and agent_api_key:
        return agent_id, agent_api_key

    resp = requests.post(
        f"{BASE_URL}/api/v1/agents/register-self",
        json={"name": AGENT_NAME},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"register-self failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    data = resp.json()
    agent_id = data["agent_id"]
    agent_api_key = data["api_key"]
    env["AGENT_ID"] = agent_id
    env["AGENT_API_KEY"] = agent_api_key
    save_env(ENV_PATH, env)
    return agent_id, agent_api_key
```

- [ ] **Step 2: Verify against the live API**

This makes a real `register-self` call (counts against the 5/IP/24h quota) and persists the result into the secrets file.

Run:
```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/agents')
from openrouter_agent import load_env, ensure_agent, ENV_PATH
env = load_env(ENV_PATH)
agent_id, agent_api_key = ensure_agent(env)
print('agent_id:', agent_id)
print('api_key starts with:', agent_api_key[:8])
"
```
Expected: prints a new `agent_id` (e.g. `openrouter-nex-n2-xxxxxx`) and the start of an `api_key`.

- [ ] **Step 3: Confirm the secrets file was updated**

Run: `grep -E '^AGENT_(ID|API_KEY)=' /root/insidedcpulse-secrets/openrouter_agent.env`
Expected: both lines now have non-empty values (no longer `AGENT_ID=` / `AGENT_API_KEY=`).

- [ ] **Step 4: Commit**

```bash
git add scripts/agents/openrouter_agent.py
git commit -m "Add ensure_agent: self-register InsideDCPulse agent identity"
```

---

### Task 3: Read world state and memory

**Files:**
- Modify: `scripts/agents/openrouter_agent.py`

- [ ] **Step 1: Append `get_world_state` and `get_world_memory`**

Find this in the file (end of `ensure_agent`):
```python
    env["AGENT_ID"] = agent_id
    env["AGENT_API_KEY"] = agent_api_key
    save_env(ENV_PATH, env)
    return agent_id, agent_api_key
```

Replace with:
```python
    env["AGENT_ID"] = agent_id
    env["AGENT_API_KEY"] = agent_api_key
    save_env(ENV_PATH, env)
    return agent_id, agent_api_key


def get_world_state(api_key: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/api/v1/world/state",
        headers={"X-API-Key": api_key},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"get world state failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()


def get_world_memory(api_key: str, limit: int = 10) -> dict:
    resp = requests.get(
        f"{BASE_URL}/api/v1/world/memory",
        headers={"X-API-Key": api_key},
        params={"limit": limit},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"get world memory failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()
```

- [ ] **Step 2: Verify against the live API**

Run:
```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/agents')
from openrouter_agent import load_env, get_world_state, get_world_memory, ENV_PATH
env = load_env(ENV_PATH)
ws = get_world_state(env['AGENT_API_KEY'])
print('state keys:', len(ws['state']))
mem = get_world_memory(env['AGENT_API_KEY'], limit=5)
print('memory total:', mem['total'], 'items returned:', len(mem['items']))
"
```
Expected: `state keys: 36` (or similar non-zero count) and `memory total: <N> items returned: 5`.

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/openrouter_agent.py
git commit -m "Add get_world_state and get_world_memory"
```

---

### Task 4: Build the LLM prompt and call OpenRouter

**Files:**
- Modify: `scripts/agents/openrouter_agent.py`

- [ ] **Step 1: Append `build_prompt` and `call_openrouter`**

Find this in the file (end of `get_world_memory`):
```python
def get_world_memory(api_key: str, limit: int = 10) -> dict:
    resp = requests.get(
        f"{BASE_URL}/api/v1/world/memory",
        headers={"X-API-Key": api_key},
        params={"limit": limit},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"get world memory failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()
```

Replace with:
```python
def get_world_memory(api_key: str, limit: int = 10) -> dict:
    resp = requests.get(
        f"{BASE_URL}/api/v1/world/memory",
        headers={"X-API-Key": api_key},
        params={"limit": limit},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"get world memory failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()


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

- [ ] **Step 2: Verify against the live OpenRouter API**

Run:
```bash
python3 -c "
import sys, json
sys.path.insert(0, 'scripts/agents')
from openrouter_agent import (
    load_env, get_world_state, get_world_memory, build_prompt,
    call_openrouter, ENV_PATH, DEFAULT_MODEL,
)
env = load_env(ENV_PATH)
ws = get_world_state(env['AGENT_API_KEY'])
mem = get_world_memory(env['AGENT_API_KEY'], limit=5)
system_msg, user_msg = build_prompt(ws, mem)
model = env.get('OPENROUTER_MODEL') or DEFAULT_MODEL
vision = call_openrouter(env['OPENROUTER_API_KEY'], model, system_msg, user_msg)
print(json.dumps(vision, indent=2))
assert 'description' in vision
assert 'ops' in vision
print('OK')
"
```
Expected: prints a JSON object with `description` and `ops` keys, then `OK`. If the model wraps the JSON in extra text and parsing fails, the script prints the raw content — inspect it and adjust the prompt wording in `build_prompt` if needed before moving on.

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/openrouter_agent.py
git commit -m "Add build_prompt and call_openrouter"
```

---

### Task 5: Evaluate, propose, and wire up `main()`

**Files:**
- Modify: `scripts/agents/openrouter_agent.py`

- [ ] **Step 1: Append `evaluate_vision`, `propose_vision`, `main`, and the `__main__` guard**

Find this in the file (end of `call_openrouter`):
```python
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"failed to parse OpenRouter response as JSON: {exc}")
        print(f"raw content: {content}")
        sys.exit(1)
```

Replace with:
```python
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"failed to parse OpenRouter response as JSON: {exc}")
        print(f"raw content: {content}")
        sys.exit(1)


def evaluate_vision(api_key: str, payload: dict) -> dict:
    resp = requests.post(
        f"{BASE_URL}/api/v1/world/evaluate",
        headers={"X-API-Key": api_key},
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"evaluate failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()


def propose_vision(api_key: str, payload: dict) -> dict:
    resp = requests.post(
        f"{BASE_URL}/api/v1/world/vision",
        headers={"X-API-Key": api_key},
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"propose failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()


def main() -> None:
    env = load_env(ENV_PATH)

    openrouter_key = env.get("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        print(f"OPENROUTER_API_KEY missing from {ENV_PATH}")
        sys.exit(1)
    model = env.get("OPENROUTER_MODEL") or DEFAULT_MODEL

    agent_id, agent_api_key = ensure_agent(env)
    print(f"== agent: {agent_id} ==")

    world_state = get_world_state(agent_api_key)
    print(f"== world_state ({len(world_state['state'])} keys) ==")
    print(json.dumps(world_state, indent=2))

    memory = get_world_memory(agent_api_key, limit=10)
    print("== recent memory ==")
    print(json.dumps(memory, indent=2))

    system_msg, user_msg = build_prompt(world_state, memory)

    print("== OpenRouter response ==")
    vision = call_openrouter(openrouter_key, model, system_msg, user_msg)
    print(json.dumps(vision, indent=2))

    payload = {
        "description": vision["description"],
        "ops": vision["ops"],
        "metadata": vision.get("metadata") or {},
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

- [ ] **Step 2: Full end-to-end run against the live VPS**

Run: `python3 scripts/agents/openrouter_agent.py`

Expected: prints `== agent: ... ==`, `== world_state (... keys) ==`, `== recent memory ==`, `== OpenRouter response ==`, `== evaluate ==`, and then either:
- `Validator would reject this vision — not proposing.` (valid outcome — score/reasons were printed above), or
- `== propose_vision ==` followed by JSON containing an `event_id` and `status: "queued"`.

Either outcome is a successful test of the full pipeline.

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/openrouter_agent.py
git commit -m "Add evaluate_vision, propose_vision, and main entrypoint"
```

---

## Spec coverage check

- Self-registration + secrets file: Task 1 + 2.
- Read world_state/memory: Task 3.
- Prompt w/ entity schema + OpenRouter call: Task 1 (schema text) + Task 4.
- Evaluate-before-propose flow: Task 5.
- Output format (section headers, JSON dumps): Task 5 `main()`.
- Error handling (HTTP errors, missing key, JSON parse failure): present in every function via `sys.exit(1)` + printed message.
- No automated test suite, manual live verification per task: Steps 2 of Tasks 2-5 + final E2E in Task 5.
