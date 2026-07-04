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
DEFAULT_PERSONA_FOCUS = (
    "Pick ONE small, valid, useful update to the current scenario "
    "(e.g. advance deployment.checkout_rollback.progress, add an "
    "incident.inc1 note, update an alert status)."
)

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

proposal.<id>.title                 string
proposal.<id>.summary               string
proposal.<id>.target_capability     string
proposal.<id>.source_paper_title    string
proposal.<id>.source_paper_url      string
proposal.<id>.relevance_score       number 0-1
proposal.<id>.status                enum: proposed, reviewed, accepted, rejected
proposal.<id>.context               object
proposal.<id>.fetched_at            string

scan_finding.<id>.target          string
scan_finding.<id>.severity        enum: info, low, medium, high, critical
scan_finding.<id>.confidence      enum: tentative, firm, certain
scan_finding.<id>.module_name     string
scan_finding.<id>.summary         string
scan_finding.<id>.url             string
scan_finding.<id>.matched_at      string
scan_finding.<id>.tags            string
scan_finding.<id>.scan_uuid       string
scan_finding.<id>.found_at        string

page.<id>.url                   string
page.<id>.title                 string
page.<id>.meta_description      string
page.<id>.schema_types          string
page.<id>.status                enum: live, draft, planned

keyword.<id>.term               string
keyword.<id>.locale             enum: en, pt, es
keyword.<id>.target_page        string
keyword.<id>.search_intent      enum: informational, transactional, navigational
keyword.<id>.priority           enum: high, medium, low
keyword.<id>.status             enum: targeting, ranking, gap

content_gap.<id>.topic          string
content_gap.<id>.priority       enum: high, medium, low
content_gap.<id>.status         enum: identified, in_progress, done
content_gap.<id>.effort         enum: low, medium, high
content_gap.<id>.notes          string

backlink.<id>.source            string
backlink.<id>.target_page       string
backlink.<id>.status            enum: live, pending, rejected
backlink.<id>.type              enum: guest_post, directory, press, organic

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


def ensure_agent(env: dict[str, str], env_path: Path) -> tuple[str, str]:
    agent_id = env.get("AGENT_ID", "")
    agent_api_key = env.get("AGENT_API_KEY", "")
    if agent_id and agent_api_key:
        return agent_id, agent_api_key

    resp = requests.post(
        f"{BASE_URL}/api/v1/agents/register-self",
        json={"name": env.get("AGENT_NAME", AGENT_NAME)},
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
    save_env(env_path, env)
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
            "reasoning": {"effort": "low"},
        },
        timeout=60,
    )
    if resp.status_code >= 300:
        print(f"OpenRouter call failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    body = resp.json()
    choices = body.get("choices")
    if not choices:
        print("OpenRouter response missing 'choices'")
        print(f"raw body: {body}")
        sys.exit(1)

    message = choices[0]["message"]
    content = message["content"]
    if content is None:
        print("OpenRouter returned empty content")
        print(f"raw message: {message}")
        sys.exit(1)
    try:
        return _parse_json_content(content)
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
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ENV_PATH
    env = load_env(env_path)

    openrouter_key = env.get("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        print(f"OPENROUTER_API_KEY missing from {env_path}")
        sys.exit(1)
    model = env.get("OPENROUTER_MODEL") or DEFAULT_MODEL

    agent_id, agent_api_key = ensure_agent(env, env_path)
    print(f"== agent: {agent_id} ==")

    world_state = get_world_state(agent_api_key)
    print(f"== world_state ({len(world_state['state'])} keys) ==")
    print(json.dumps(world_state, indent=2))

    memory = get_world_memory(agent_api_key, limit=10)
    print("== recent memory ==")
    print(json.dumps(memory, indent=2))

    from vigolium_utils import (
        format_findings_context,
        get_scan_min_severity,
        get_scan_strategy,
        get_scan_target,
        scan_and_feed,
    )

    scan_target = get_scan_target(env)
    vigolium_context = ""
    if scan_target:
        findings = scan_and_feed(
            scan_target, agent_api_key, world_state,
            evaluate_vision, propose_vision,
            strategy=get_scan_strategy(env),
            min_severity=get_scan_min_severity(env),
        )
        vigolium_context = format_findings_context(findings)
        world_state = get_world_state(agent_api_key)

    persona_focus = env.get("PERSONA_FOCUS") or DEFAULT_PERSONA_FOCUS
    if vigolium_context:
        persona_focus = f"{persona_focus}\n\n{vigolium_context}\n\nIncorporate these scan findings into your update."

    system_msg, user_msg = build_prompt(world_state, memory, persona_focus)

    print("== OpenRouter response ==")
    vision = call_openrouter(openrouter_key, model, system_msg, user_msg)
    print(json.dumps(vision, indent=2))

    ops = vision.get("ops") or []
    if not ops:
        print("Model returned no ops — nothing to propose.")
        return

    payload = {
        "description": vision["description"],
        "ops": ops,
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
