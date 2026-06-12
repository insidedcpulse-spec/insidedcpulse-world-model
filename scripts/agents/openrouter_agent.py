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
