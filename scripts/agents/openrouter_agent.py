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
