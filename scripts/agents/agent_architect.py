#!/usr/bin/env python3
"""LLM-based agent-architect: researches Google's Agent2Agent (A2A) protocol
via arXiv and proposes new InsideDCPulse agent personas that would implement
or apply A2A concepts, writing proposal.* world_state entries."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from openrouter_agent import (
    DEFAULT_MODEL,
    call_openrouter,
    ensure_agent,
    evaluate_vision,
    get_world_state,
    load_env,
    propose_vision,
    save_env,
)
from research_agent import sanitize_id, search_arxiv

ENV_PATH = Path("/root/insidedcpulse-secrets/agents/agent-architect.env")
PROPOSAL_FIELDS = [
    "title", "summary", "target_capability", "source_paper_title",
    "source_paper_url", "relevance_score", "status", "context", "fetched_at",
]
MAX_PROPOSAL_ENTRIES = 10
RELEVANCE_THRESHOLD = 0.3
A2A_QUERIES = [
    "Agent2Agent protocol",
    "agent interoperability protocol",
    "agent interoperability",
    "multi-agent system protocol",
]
CONTEXT_ENTITIES = ("finding", "research")
MAX_CONTEXT_ENTRIES = 5

SYSTEM_PROMPT = """You are the Agent Architect persona for InsideDCPulse, an
event-sourced world model for multi-LLM agents. InsideDCPulse currently runs
6 always-on agent personas: sre-agent, deploy-agent, alert-agent,
research-agent, ai-research-agent, threat-intel-agent.

Your job is to study Google's Agent2Agent (A2A) protocol via the arXiv
candidates below, and propose ONE new agent persona that would implement or
apply an A2A concept (e.g. capability advertisement, agent discovery, task
delegation, negotiation between agents) within InsideDCPulse's existing
world_state + knowledge graph architecture.

You may also see recent research/finding entries already known to the
system — avoid proposing something that duplicates them, and reference
relevant ones by id in "consulted" if they inform your proposal.

Respond with ONLY a JSON object:
{"chosen_index": <int> | null, "title": <string>, "summary": <string>, "target_capability": <string>, "relevance_score": <number 0-1>, "rationale": <string>, "consulted": [<string ids>]}
Set "chosen_index" to null if none of the candidate papers describe an A2A
concept applicable to this system. No prose, no markdown fences."""


def _collect_context_entries(state: dict, entity: str) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for key, value in state.items():
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != entity:
            continue
        entry = entries.setdefault(parts[1], {})
        if parts[2] in ("title", "topic", "topics", "fetched_at"):
            entry[parts[2]] = value["value"]
    return entries


def build_context_lines(state: dict) -> list[str]:
    combined = []
    for entity in CONTEXT_ENTITIES:
        for entity_id, entry in _collect_context_entries(state, entity).items():
            if "fetched_at" not in entry or "title" not in entry:
                continue
            combined.append((entry["fetched_at"], f"{entity}.{entity_id}", entry))
    combined.sort(key=lambda item: item[0], reverse=True)
    lines = []
    for _, full_id, entry in combined[:MAX_CONTEXT_ENTRIES]:
        topic = entry.get("topic") or entry.get("topics") or ""
        lines.append(f"- {full_id}: {entry['title']} (topic: {topic})")
    return lines


def build_user_prompt(candidates: list[dict], context_lines: list[str]) -> str:
    lines = ["Candidate A2A papers:"]
    for idx, paper in enumerate(candidates):
        lines.append(
            f"{idx}. title: {paper['title']}\n"
            f"   published: {paper['published']}\n"
            f"   url: {paper['link']}\n"
            f"   summary: {paper['summary'][:500]}"
        )
    lines.append("")
    if context_lines:
        lines.append("Existing research/finding entries already known to the system:")
        lines.extend(context_lines)
    else:
        lines.append("No existing research/finding entries yet.")
    lines.append("")
    lines.append("Respond with the JSON object described in the system prompt.")
    return "\n".join(lines)


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
    state = world_state["state"]

    existing_ids: dict[str, str] = {}
    for key, value in state.items():
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "proposal" and parts[2] == "fetched_at":
            existing_ids[parts[1]] = value["value"]

    query_index = int(env.get("A2A_QUERY_INDEX", "0"))
    query = A2A_QUERIES[query_index % len(A2A_QUERIES)]
    env["A2A_QUERY_INDEX"] = str((query_index + 1) % len(A2A_QUERIES))
    save_env(env_path, env)
    print(f"== query: {query} ==")

    papers = search_arxiv(query, max_results=10)

    candidates = []
    for paper in papers:
        candidate_id = sanitize_id(paper["id"])
        if candidate_id not in existing_ids:
            candidates.append(paper)

    if not candidates:
        print("All returned A2A papers already in proposal.* — no-op this cycle.")
        return

    context_lines = build_context_lines(state)
    system_msg = SYSTEM_PROMPT
    user_msg = build_user_prompt(candidates, context_lines)

    print("== OpenRouter response ==")
    llm = call_openrouter(openrouter_key, model, system_msg, user_msg)
    print(json.dumps(llm, indent=2))

    chosen_index = llm.get("chosen_index")
    if not isinstance(chosen_index, int) or not (0 <= chosen_index < len(candidates)):
        print("LLM found no sufficiently relevant A2A paper — no-op this cycle.")
        return

    relevance_score = float(llm.get("relevance_score", 0))
    if relevance_score < RELEVANCE_THRESHOLD:
        print(f"Relevance score {relevance_score} below threshold {RELEVANCE_THRESHOLD} — no-op this cycle.")
        return

    chosen = candidates[chosen_index]
    new_id = sanitize_id(chosen["id"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = llm.get("title", "")
    summary = llm.get("summary", "")
    target_capability = llm.get("target_capability", "")
    rationale = llm.get("rationale", "")
    consulted = llm.get("consulted") or []

    ops = [
        {"op": "set", "key": f"proposal.{new_id}.title", "value": title},
        {"op": "set", "key": f"proposal.{new_id}.summary", "value": summary},
        {"op": "set", "key": f"proposal.{new_id}.target_capability", "value": target_capability},
        {"op": "set", "key": f"proposal.{new_id}.source_paper_title", "value": chosen["title"]},
        {"op": "set", "key": f"proposal.{new_id}.source_paper_url", "value": chosen["link"]},
        {"op": "set", "key": f"proposal.{new_id}.relevance_score", "value": relevance_score},
        {"op": "set", "key": f"proposal.{new_id}.status", "value": "proposed"},
        {"op": "set", "key": f"proposal.{new_id}.fetched_at", "value": now},
        {"op": "merge", "key": f"proposal.{new_id}.context", "value": {"consulted": consulted, "rationale": rationale}},
    ]

    if len(existing_ids) + 1 > MAX_PROPOSAL_ENTRIES:
        oldest_id = min(existing_ids, key=lambda k: existing_ids[k])
        for field in PROPOSAL_FIELDS:
            ops.append({"op": "delete", "key": f"proposal.{oldest_id}.{field}"})

    payload = {
        "description": f"Propose new persona '{title}' from A2A paper '{chosen['title']}' ({new_id})",
        "ops": ops,
        "metadata": {
            "source": "agent-architect",
            "kind": "persona_proposal",
            "target_capability": target_capability,
            "relevance_score": relevance_score,
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
