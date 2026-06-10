#!/usr/bin/env python3
"""Webhook listener: verifies a GitHub push to main and redeploys the api service."""
import hashlib
import hmac
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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


class DeployWebhookHandler(BaseHTTPRequestHandler):
    secret: bytes = b""

    def do_GET(self):
        if self.path == "/healthz":
            self._respond(200, b"ok")
        else:
            self._respond(404, b"not found")

    def do_POST(self):
        if self.path != "/hooks/deploy":
            self._respond(404, b"not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if not verify_signature(self.secret, body, self.headers.get("X-Hub-Signature-256")):
            print("[webhook] invalid signature", flush=True)
            self._respond(401, b"invalid signature")
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}

        event = self.headers.get("X-GitHub-Event")
        if should_deploy(event, payload):
            print(f"[webhook] push to {payload.get('ref')}, deploying", flush=True)
            threading.Thread(target=run_deploy, daemon=True).start()
        else:
            print(f"[webhook] ignoring event={event} ref={payload.get('ref')}", flush=True)

        self._respond(200, b"ok")

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[http] " + (fmt % args), flush=True)


def main() -> None:
    secret = os.environ["WEBHOOK_SECRET"].encode()
    DeployWebhookHandler.secret = secret
    # 0.0.0.0: nginx reaches this via host.docker.internal (the docker
    # bridge gateway IP), which is not 127.0.0.1 from inside the container.
    # The /hooks/deploy endpoint is protected by HMAC signature verification.
    server = ThreadingHTTPServer(("0.0.0.0", 9001), DeployWebhookHandler)
    print("[webhook] listening on 0.0.0.0:9001", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
