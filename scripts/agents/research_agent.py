#!/usr/bin/env python3
"""Deterministic research-agent: pulls arXiv papers into world_state research.*."""

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from openrouter_agent import (
    ensure_agent,
    evaluate_vision,
    get_world_state,
    load_env,
    propose_vision,
    save_env,
)

ENV_PATH = Path("/root/insidedcpulse-secrets/agents/research-agent.env")
ARXIV_CLI = "/root/go/bin/arxiv-pp-cli"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
RESEARCH_FIELDS = ["title", "summary", "topic", "published", "url", "fetched_at"]
MAX_RESEARCH_ENTRIES = 10

TOPICS = [
    "site reliability engineering",
    "anomaly detection time series",
    "incident response automation",
    "distributed systems fault tolerance",
    "chaos engineering",
]


def sanitize_id(arxiv_id: str) -> str:
    """'http://arxiv.org/abs/2506.01234v2' -> '2506_01234'."""
    tail = arxiv_id.rsplit("/abs/", 1)[-1]
    tail = re.sub(r"v\d+$", "", tail)
    tail = tail.lower().replace(".", "_").replace("/", "_")
    return tail[:32]


def search_arxiv(topic: str, max_results: int = 5) -> list[dict]:
    result = subprocess.run(
        [
            ARXIV_CLI,
            "query",
            "--search-query",
            f'all:"{topic}"',
            "--max-results",
            str(max_results),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"arxiv-pp-cli failed: {result.returncode}")
        print(result.stderr)
        sys.exit(1)

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"failed to parse arxiv-pp-cli output as JSON: {exc}")
        print(result.stdout)
        sys.exit(1)

    try:
        root = ET.fromstring(envelope["results"])
    except (KeyError, ET.ParseError) as exc:
        print(f"failed to parse arXiv Atom feed: {exc}")
        print(envelope)
        sys.exit(1)

    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        arxiv_id = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
        title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
        published = (entry.findtext("atom:published", default="", namespaces=ATOM_NS) or "").strip()
        link = arxiv_id
        for link_el in entry.findall("atom:link", ATOM_NS):
            if link_el.get("rel") == "alternate":
                link = link_el.get("href", link)
                break
        papers.append(
            {
                "id": arxiv_id,
                "title": title,
                "summary": summary,
                "published": published,
                "link": link,
            }
        )
    return papers


def main() -> None:
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ENV_PATH
    env = load_env(env_path)

    agent_id, agent_api_key = ensure_agent(env, env_path)
    print(f"== agent: {agent_id} ==")

    world_state = get_world_state(agent_api_key)
    state = world_state["state"]

    existing_ids: dict[str, str] = {}
    for key, value in state.items():
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "research" and parts[2] == "fetched_at":
            existing_ids[parts[1]] = value

    topic_index = int(env.get("TOPIC_INDEX", "0"))
    topic = TOPICS[topic_index % len(TOPICS)]
    env["TOPIC_INDEX"] = str((topic_index + 1) % len(TOPICS))
    save_env(env_path, env)
    print(f"== topic: {topic} ==")

    papers = search_arxiv(topic)

    chosen = None
    for paper in papers:
        candidate_id = sanitize_id(paper["id"])
        if candidate_id not in existing_ids:
            chosen = (candidate_id, paper)
            break

    if chosen is None:
        print("All returned papers already in research.* — no-op this cycle.")
        return

    new_id, paper = chosen
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ops = [
        {"op": "set", "key": f"research.{new_id}.title", "value": paper["title"]},
        {"op": "set", "key": f"research.{new_id}.summary", "value": paper["summary"][:500]},
        {"op": "set", "key": f"research.{new_id}.topic", "value": topic},
        {"op": "set", "key": f"research.{new_id}.published", "value": paper["published"]},
        {"op": "set", "key": f"research.{new_id}.url", "value": paper["link"]},
        {"op": "set", "key": f"research.{new_id}.fetched_at", "value": now},
    ]

    if len(existing_ids) + 1 > MAX_RESEARCH_ENTRIES:
        oldest_id = min(existing_ids, key=lambda k: existing_ids[k])
        for field in RESEARCH_FIELDS:
            ops.append({"op": "delete", "key": f"research.{oldest_id}.{field}"})

    payload = {
        "description": f"Add arXiv paper '{paper['title']}' ({new_id}) on {topic}",
        "ops": ops,
        "metadata": {"source": "research-agent", "topic": topic},
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
