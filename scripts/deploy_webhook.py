#!/usr/bin/env python3
"""Webhook listener: verifies a GitHub push to main and redeploys the api service."""
import hashlib
import hmac
import subprocess


def verify_signature(secret: bytes, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


def should_deploy(event_header: str | None, payload: dict) -> bool:
    if event_header != "push":
        return False
    return payload.get("ref") == "refs/heads/main"


REPO_DIR = "/opt/insidedcpulse-world-model"
DOCKER_DIR = f"{REPO_DIR}/docker"

DEPLOY_STEPS = [
    (["git", "fetch", "origin", "main"], REPO_DIR),
    (["git", "reset", "--hard", "origin/main"], REPO_DIR),
    (["docker", "compose", "build", "api"], DOCKER_DIR),
    (["docker", "compose", "up", "-d", "--remove-orphans"], DOCKER_DIR),
    (["docker", "image", "prune", "-f"], DOCKER_DIR),
]


def run_deploy() -> None:
    for cmd, cwd in DEPLOY_STEPS:
        print(f"[deploy] $ {' '.join(cmd)} (cwd={cwd})", flush=True)
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        print(result.stdout, flush=True)
        if result.returncode != 0:
            print(result.stderr, flush=True)
            print(f"[deploy] step failed with exit code {result.returncode}, aborting", flush=True)
            return
    print("[deploy] done", flush=True)
