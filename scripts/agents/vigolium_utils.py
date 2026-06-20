#!/usr/bin/env python3
"""Shared Vigolium scanning utilities for InsideDCPulse agents.

Usage from any agent:
    from vigolium_utils import get_scan_target, scan_and_feed

    target = get_scan_target(env)
    if target:
        findings = scan_and_feed(target, agent_api_key, world_state, evaluate_vision, propose_vision)

CLI override (any agent):
    python3 <agent>.py <env-file> --scan-target https://example.com --scan-strategy balanced
"""

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone

VIGOLIUM_BIN = "/usr/local/bin/vigolium"
SCAN_FINDING_FIELDS = [
    "target", "severity", "confidence", "module_name", "summary",
    "url", "matched_at", "tags", "scan_uuid", "found_at",
]
MAX_SCAN_FINDINGS = 20
MAX_OPS_PER_VISION = 20


def run_scan(target: str, strategy: str = "lite", timeout: int = 600) -> str:
    """Run vigolium scan and return the scan UUID."""
    scan_uuid = str(uuid.uuid4())
    cmd = [
        VIGOLIUM_BIN, "scan", "-t", target,
        "-F", "--no-color",
        "--scan-uuid", scan_uuid,
        "--strategy", strategy,
    ]
    print(f"== vigolium scan: {target} (strategy={strategy}, uuid={scan_uuid}) ==")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"vigolium scan exit {result.returncode}: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        print(f"vigolium scan timed out after {timeout}s")
    return scan_uuid


def _extract_host(url: str) -> str:
    """Extract hostname from URL."""
    from urllib.parse import urlparse
    return urlparse(url).hostname or url


def get_findings(
    scan_uuid: str,
    target: str = "",
    min_severity: str = "low",
) -> list[dict]:
    """Read findings from Vigolium DB — by host first, fallback to scan UUID.

    Vigolium deduplicates findings: re-scanning the same host attributes
    results to the original scan, not the new one. Query by host catches those.
    """
    host = _extract_host(target) if target else ""

    cmd = [VIGOLIUM_BIN, "finding", "-j", "--min-severity", min_severity, "--limit", "50"]
    if host:
        cmd.extend(["--host", host])
    else:
        cmd.extend(["--scan-uuid", scan_uuid])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not result.stdout.strip():
        print(f"vigolium finding query failed: {result.stderr[:200]}")
        return []
    try:
        data = json.loads(result.stdout)
        return data.get("findings", [])
    except json.JSONDecodeError:
        print(f"failed to parse vigolium findings JSON: {result.stdout[:200]}")
        return []


def finding_to_id(finding: dict) -> str:
    """Generate a deterministic ID for a finding (max 32 chars for KEY_PATTERN)."""
    import hashlib
    module = (finding.get("module_id") or finding.get("module_name", "unknown"))
    module = module.lower().replace(" ", "_").replace("-", "_")[:16]
    fid = finding.get("id", 0)
    host_hash = hashlib.md5(finding.get("hostname", "").encode()).hexdigest()[:6]
    raw = f"{module}_{host_hash}_{fid}"
    return raw[:32]


def findings_to_ops(
    findings: list[dict],
    existing_ids: dict[str, str],
    scan_target: str,
    scan_uuid: str,
) -> list[dict]:
    """Convert Vigolium findings to InsideDCPulse world_state ops."""
    ops: list[dict] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    added_count = 0

    for f in findings:
        if added_count >= MAX_SCAN_FINDINGS:
            break

        fid = finding_to_id(f)
        if fid in existing_ids:
            continue

        matched_at = f.get("matched_at", [])
        matched_str = matched_at[0] if matched_at else ""
        tags_str = ",".join(f.get("tags", []))

        ops.extend([
            {"op": "set", "key": f"scan_finding.{fid}.target", "value": scan_target[:200]},
            {"op": "set", "key": f"scan_finding.{fid}.severity", "value": f.get("severity", "info")},
            {"op": "set", "key": f"scan_finding.{fid}.confidence", "value": f.get("confidence", "tentative")},
            {"op": "set", "key": f"scan_finding.{fid}.module_name", "value": (f.get("module_name") or "")[:200]},
            {"op": "set", "key": f"scan_finding.{fid}.summary", "value": (f.get("description") or "")[:500]},
            {"op": "set", "key": f"scan_finding.{fid}.url", "value": (f.get("url") or "")[:500]},
            {"op": "set", "key": f"scan_finding.{fid}.matched_at", "value": matched_str[:500]},
            {"op": "set", "key": f"scan_finding.{fid}.tags", "value": tags_str[:200]},
            {"op": "set", "key": f"scan_finding.{fid}.scan_uuid", "value": scan_uuid},
            {"op": "set", "key": f"scan_finding.{fid}.found_at", "value": f.get("found_at", now)},
        ])
        added_count += 1

    total = len(existing_ids) + added_count
    if total > MAX_SCAN_FINDINGS:
        sorted_ids = sorted(existing_ids, key=lambda k: existing_ids[k])
        evict_count = total - MAX_SCAN_FINDINGS
        for eid in sorted_ids[:evict_count]:
            for field in SCAN_FINDING_FIELDS:
                ops.append({"op": "delete", "key": f"scan_finding.{eid}.{field}"})

    return ops


def get_existing_scan_findings(world_state: dict) -> dict[str, str]:
    """Extract existing scan_finding IDs and their found_at from world state."""
    existing: dict[str, str] = {}
    for key, value in world_state.get("state", {}).items():
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "scan_finding" and parts[2] == "found_at":
            existing[parts[1]] = value["value"]
    return existing


def scan_and_feed(
    target: str,
    agent_api_key: str,
    world_state: dict,
    evaluate_fn,
    propose_fn,
    strategy: str = "lite",
    min_severity: str = "low",
    timeout: int = 600,
) -> list[dict]:
    """Run full pipeline: scan -> parse -> feed world model. Returns raw findings."""
    scan_uuid = run_scan(target, strategy=strategy, timeout=timeout)
    findings = get_findings(scan_uuid, target=target, min_severity=min_severity)

    if not findings:
        print(f"No findings from vigolium scan of {target}")
        return []

    print(f"== {len(findings)} findings from vigolium ==")
    for f in findings[:5]:
        print(f"  [{f.get('severity', '?')}] {f.get('module_name', '?')} — {f.get('url', '?')}")

    existing_ids = get_existing_scan_findings(world_state)
    ops = findings_to_ops(findings, existing_ids, target, scan_uuid)

    if not ops:
        print("All findings already in world_state — no new ops.")
        return findings

    for i in range(0, len(ops), MAX_OPS_PER_VISION):
        batch = ops[i:i + MAX_OPS_PER_VISION]
        batch_num = (i // MAX_OPS_PER_VISION) + 1
        total_batches = (len(ops) + MAX_OPS_PER_VISION - 1) // MAX_OPS_PER_VISION

        payload = {
            "description": f"Vigolium scan of {target} ({batch_num}/{total_batches})",
            "ops": batch,
            "metadata": {
                "source": "vigolium-scan",
                "target": target,
                "scan_uuid": scan_uuid,
                "strategy": strategy,
                "batch": f"{batch_num}/{total_batches}",
            },
        }

        print(f"== evaluate batch {batch_num}/{total_batches} ({len(batch)} ops) ==")
        evaluation = evaluate_fn(agent_api_key, payload)
        print(json.dumps(evaluation, indent=2))

        if not evaluation.get("would_accept"):
            print(f"Batch {batch_num} rejected — skipping.")
            continue

        print(f"== propose batch {batch_num}/{total_batches} ==")
        result = propose_fn(agent_api_key, payload)
        print(json.dumps(result, indent=2))

    return findings


def get_scan_target(env: dict[str, str]) -> str | None:
    """Get scan target from CLI --scan-target or env SCAN_TARGET."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--scan-target" and i + 1 < len(args):
            return args[i + 1]
    return env.get("SCAN_TARGET") or None


def get_scan_strategy(env: dict[str, str]) -> str:
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--scan-strategy" and i + 1 < len(args):
            return args[i + 1]
    return env.get("SCAN_STRATEGY", "lite")


def get_scan_min_severity(env: dict[str, str]) -> str:
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--scan-min-severity" and i + 1 < len(args):
            return args[i + 1]
    return env.get("SCAN_MIN_SEVERITY", "low")


def format_findings_context(findings: list[dict]) -> str:
    """Format findings as text context for LLM agents."""
    if not findings:
        return ""
    lines = ["Vigolium scan findings:"]
    for f in findings[:10]:
        sev = f.get("severity", "?")
        mod = f.get("module_name", "?")
        url = f.get("url", "?")
        desc = (f.get("description") or "")[:120]
        lines.append(f"  [{sev}] {mod} @ {url} — {desc}")
    return "\n".join(lines)
