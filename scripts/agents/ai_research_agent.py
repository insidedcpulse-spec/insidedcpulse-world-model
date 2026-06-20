#!/usr/bin/env python3
"""LLM-based ai-research-agent: finds AI-systems-research arXiv papers
relevant to InsideDCPulse's own architecture, scores relevance via LLM, and
writes finding.* world_state entries with a per-run architectural insight."""

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

ENV_PATH = Path("/root/insidedcpulse-secrets/agents/ai-research-agent.env")
FINDING_FIELDS = [
    "title", "summary", "url", "topics", "relevance_score",
    "why_it_matters", "source", "fetched_at", "notes",
]
MAX_FINDING_ENTRIES = 10
RELEVANCE_THRESHOLD = 0.3

TOPICS = [
    "event-sourced AI systems",
    "multi-agent coordination",
    "agent memory architectures",
    "LLM planning systems",
    "autonomous tool-using agents",
    "world models for artificial intelligence",
]

SYSTEM_PROMPT = """You are the AI Research Intelligence persona for InsideDCPulse, an
event-sourced world model for multi-LLM agents.

Your job is NOT to summarize random papers. Your job is to find research
that could improve InsideDCPulse's own architecture: event-sourced AI
systems, multi-agent coordination, agent memory architectures, planning
systems for LLMs, autonomous tool-using agents, and world models for AI.

Mandatory filters:
- Ignore papers that are purely biomedical/clinical.
- Ignore science unrelated to AI systems.
- Prioritize system-level AI papers over narrow ML benchmarks.
- Prioritize recent research (last 2-3 years).
- Prioritize topics like autonomous agents, agent memory, planning,
  multi-agent orchestration.

Never include medical or clinical interpretations. Focus exclusively on AI
systems design. Always prioritize architectural impact over descriptive
summary.

Respond with ONLY a JSON object:
{"chosen_index": <int> | null, "topics": [<string>, ...], "relevance_score": <number 0-1>, "why_it_matters_for_inside_dcpulse": <string>, "insight": <string>}
Set "chosen_index" to null if none of the candidates meaningfully match the
filters above. No prose, no markdown fences."""


def build_user_prompt(topic: str, candidates: list[dict]) -> str:
    lines = [f"Rotated topic hint for this run: {topic}", "", "Candidates:"]
    for idx, paper in enumerate(candidates):
        lines.append(
            f"{idx}. title: {paper['title']}\n"
            f"   published: {paper['published']}\n"
            f"   url: {paper['link']}\n"
            f"   summary: {paper['summary'][:500]}"
        )
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
        if len(parts) == 3 and parts[0] == "finding" and parts[2] == "fetched_at":
            existing_ids[parts[1]] = value["value"]

    topic_index = int(env.get("TOPIC_INDEX", "0"))
    topic = TOPICS[topic_index % len(TOPICS)]
    env["TOPIC_INDEX"] = str((topic_index + 1) % len(TOPICS))
    save_env(env_path, env)
    print(f"== topic: {topic} ==")

    papers = search_arxiv(topic, max_results=10)

    candidates = []
    for paper in papers:
        candidate_id = sanitize_id(paper["id"])
        if candidate_id not in existing_ids:
            candidates.append(paper)

    if not candidates:
        print("All returned papers already in finding.* — no-op this cycle.")
        return

    system_msg = SYSTEM_PROMPT
    user_msg = build_user_prompt(topic, candidates)

    print("== OpenRouter response ==")
    llm = call_openrouter(openrouter_key, model, system_msg, user_msg)
    print(json.dumps(llm, indent=2))

    chosen_index = llm.get("chosen_index")
    if not isinstance(chosen_index, int) or not (0 <= chosen_index < len(candidates)):
        print("LLM found no sufficiently relevant candidate — no-op this cycle.")
        return

    relevance_score = float(llm.get("relevance_score", 0))
    if relevance_score < RELEVANCE_THRESHOLD:
        print(f"Relevance score {relevance_score} below threshold {RELEVANCE_THRESHOLD} — no-op this cycle.")
        return

    chosen = candidates[chosen_index]
    new_id = sanitize_id(chosen["id"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    topics_str = ", ".join(llm.get("topics") or [])
    why_it_matters = llm.get("why_it_matters_for_inside_dcpulse", "")
    insight = llm.get("insight", "")

    ops = [
        {"op": "set", "key": f"finding.{new_id}.title", "value": chosen["title"]},
        {"op": "set", "key": f"finding.{new_id}.summary", "value": chosen["summary"][:500]},
        {"op": "set", "key": f"finding.{new_id}.url", "value": chosen["link"]},
        {"op": "set", "key": f"finding.{new_id}.topics", "value": topics_str},
        {"op": "set", "key": f"finding.{new_id}.relevance_score", "value": relevance_score},
        {"op": "set", "key": f"finding.{new_id}.why_it_matters", "value": why_it_matters},
        {"op": "set", "key": f"finding.{new_id}.source", "value": "arxiv"},
        {"op": "set", "key": f"finding.{new_id}.fetched_at", "value": now},
        {"op": "merge", "key": f"finding.{new_id}.notes", "value": {"insight": insight}},
    ]

    if len(existing_ids) + 1 > MAX_FINDING_ENTRIES:
        oldest_id = min(existing_ids, key=lambda k: existing_ids[k])
        for field in FINDING_FIELDS:
            ops.append({"op": "delete", "key": f"finding.{oldest_id}.{field}"})

    payload = {
        "description": f"Add arXiv finding '{chosen['title']}' ({new_id}) — relevance {relevance_score}",
        "ops": ops,
        "metadata": {
            "source": "ai-research-agent",
            "kind": "research_paper_found",
            "topic": topic,
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
