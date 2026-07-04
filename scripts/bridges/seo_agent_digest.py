#!/usr/bin/env python3
"""Digest bridge: seo-agent accepted proposals -> a GitHub issue on whatsuser-link.

Detects new accepted world-state ops from the seo-agent persona and posts them
as a comment on a persistent tracking issue. Does not touch the whatsuser-link
repo's code or trigger any deploy — a human decides what to act on.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://insidedcpulse.com"
SEO_AGENT_ENV = Path("/root/insidedcpulse-secrets/agents/seo-agent.env")
GITHUB_PAT_ENV = Path("/root/insidedcpulse-secrets/github_pat.env")
STATE_PATH = Path("/root/insidedcpulse-secrets/agents/seo_bridge_state.json")

GITHUB_REPO = "insidedcpulse-spec/whatsuser-link"
ISSUE_TITLE = "SEO agent digest (auto)"
ISSUE_BODY = (
    "Auto-tracking issue for the InsideDCPulse `seo-agent` persona.\n\n"
    "Every hour, `scripts/bridges/seo_agent_digest.py` checks the "
    "[world-model](https://insidedcpulse.com/api/v1/world/state) for new "
    "accepted `page.*`/`keyword.*`/`content_gap.*`/`backlink.*` proposals "
    "from `seo-agent` and posts them below as a comment.\n\n"
    "This issue is informational only — nothing here auto-applies to the "
    "site. A human reviews each digest and decides what to act on."
)
TRACKED_PREFIXES = ("page.", "keyword.", "content_gap.", "backlink.")


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"last_processed_ids": [], "issue_number": None}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))
    STATE_PATH.chmod(0o600)


def fetch_accepted_events(api_key: str, agent_id: str) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/api/v1/world/memory",
        headers={"X-API-Key": api_key},
        params={"agent_id": agent_id, "status": "accepted", "limit": 200},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["items"]


def relevant_ops(event: dict) -> list[dict]:
    ops = event.get("payload", {}).get("ops", [])
    return [op for op in ops if op.get("key", "").startswith(TRACKED_PREFIXES)]


def verify_github_token(token: str) -> bool:
    resp = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    return resp.status_code == 200


def ensure_issue(token: str, state: dict) -> int:
    if state.get("issue_number"):
        return state["issue_number"]

    resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": ISSUE_TITLE, "body": ISSUE_BODY},
        timeout=30,
    )
    resp.raise_for_status()
    number = resp.json()["number"]
    state["issue_number"] = number
    return number


def post_comment(token: str, issue_number: int, body: str) -> None:
    resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/issues/{issue_number}/comments",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"body": body},
        timeout=30,
    )
    resp.raise_for_status()


def format_digest(events: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"### seo-agent digest — {now}", ""]
    for event in events:
        ops = relevant_ops(event)
        if not ops:
            continue
        lines.append(f"**{event['created_at']}** — {event['payload'].get('description', '')}")
        for op in ops:
            lines.append(f"- `{op['key']}` ({op['op']}) -> `{json.dumps(op.get('value'))}`")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    seo_env = load_env(SEO_AGENT_ENV)
    api_key = seo_env.get("AGENT_API_KEY")
    agent_id = seo_env.get("AGENT_ID")
    if not api_key or not agent_id:
        print("ERROR: AGENT_API_KEY/AGENT_ID missing from seo-agent.env", file=sys.stderr)
        return 1

    if not GITHUB_PAT_ENV.exists():
        print("ERROR: github_pat.env not found", file=sys.stderr)
        return 1
    gh_env = load_env(GITHUB_PAT_ENV)
    token = gh_env.get("GITHUB_PAT_CLASSIC")
    if not token:
        print("ERROR: GITHUB_PAT_CLASSIC missing from github_pat.env", file=sys.stderr)
        return 1
    if not verify_github_token(token):
        print("ERROR: GitHub PAT is dead (GET /user did not return 200) — needs a fresh PAT from the user", file=sys.stderr)
        return 1

    state = load_state()
    seen_ids = set(state.get("last_processed_ids", []))

    try:
        events = fetch_accepted_events(api_key, agent_id)
    except requests.RequestException as exc:
        print(f"ERROR: failed to fetch world/memory: {exc}", file=sys.stderr)
        return 1

    new_events = [e for e in events if e["id"] not in seen_ids and relevant_ops(e)]

    if not seen_ids:
        # First run: baseline everything currently accepted, report nothing.
        print(f"Baseline: marking {len(events)} existing accepted events as seen, no digest posted.")
        state["last_processed_ids"] = [e["id"] for e in events]
        save_state(state)
        return 0

    if not new_events:
        print("No new seo-agent proposals since last run.")
        return 0

    try:
        issue_number = ensure_issue(token, state)
        post_comment(token, issue_number, format_digest(new_events))
    except requests.RequestException as exc:
        print(f"ERROR: GitHub API call failed: {exc}", file=sys.stderr)
        return 1

    state["last_processed_ids"] = list(seen_ids | {e["id"] for e in new_events})
    save_state(state)
    print(f"Posted digest with {len(new_events)} new event(s) to issue #{issue_number}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
