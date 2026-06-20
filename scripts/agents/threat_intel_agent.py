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
    (["fastapi", "starlette", "uvicorn", "mcp", "pydantic", "orjson", "sse-starlette"], "fastapi/starlette/mcp", "team.sre"),
    (["asyncpg"], "asyncpg==0.30.0", "service.payments_db"),
    (["docker", "containerd", "runc", "moby"], "docker (container runtime)", "team.sre"),
    (["alpine linux", "alpine"], "alpine (nginx/redis/postgres base images)", "team.sre"),
    (["openssl"], "openssl (TLS)", "team.sre"),
    (["debian"], "debian (python:3.12-slim base)", "team.sre"),
    (["linux kernel"], "linux kernel (host OS)", "team.sre"),
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

    from vigolium_utils import get_scan_min_severity, get_scan_strategy, get_scan_target, scan_and_feed

    scan_target = get_scan_target(env)
    if scan_target:
        scan_and_feed(
            scan_target, agent_api_key, world_state,
            evaluate_vision, propose_vision,
            strategy=get_scan_strategy(env),
            min_severity=get_scan_min_severity(env),
        )
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
