#!/usr/bin/env python3
"""Webhook listener: verifies a GitHub push to main and redeploys the api service."""
import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
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
    # insidedcpulse.conf is gitignored (live nginx config); insidedcpulse.conf.ssl
    # is the tracked source of truth. Re-sync on every deploy so nginx changes
    # pushed via .conf.ssl actually take effect (recurring gotcha otherwise).
    (["cp", "-f", "docker/nginx/conf.d/insidedcpulse.conf.ssl", "docker/nginx/conf.d/insidedcpulse.conf"], REPO_DIR),
    (["docker", "compose", "build", "api"], DOCKER_DIR),
    (["docker", "compose", "up", "-d", "--remove-orphans"], DOCKER_DIR),
    (["docker", "compose", "restart", "nginx"], DOCKER_DIR),
    (["docker", "image", "prune", "-f"], DOCKER_DIR),
]


SMOKE_BASE_URL = "https://insidedcpulse.com"
SMOKE_SETTLE_SECONDS = 3

LAST_SMOKE_RESULTS: dict = {}


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    """Stops urllib from following the 3xx so we can inspect Location ourselves."""

    def http_response(self, request, response):
        return response

    https_response = http_response


def _smoke_get(path: str, expect_status: int, check=None) -> tuple[bool, str]:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(f"{SMOKE_BASE_URL}{path}", timeout=10)
    except urllib.error.URLError as exc:
        return False, f"error: {exc}"
    if resp.status != expect_status:
        return False, f"status {resp.status} != {expect_status}"
    if check is not None and not check(resp):
        return False, "response check failed"
    return True, "ok"


def _smoke_post(path: str, payload: dict, expect_status: int, body_contains: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        f"{SMOKE_BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.URLError as exc:
        return False, f"error: {exc}"
    if resp.status != expect_status:
        return False, f"status {resp.status} != {expect_status}"
    body = resp.read().decode()
    if body_contains not in body:
        return False, f"{body_contains!r} not in response"
    return True, "ok"


def run_smoke_checks() -> list[dict]:
    """Post-deploy checks for the regressions we've already hit in prod:
    Grafana redirect loop on /grafana/ and the /mcp/ session-manager crash
    on an unrecognized JSON-RPC method.
    """
    checks = [
        ("healthz", lambda: _smoke_get("/healthz", 200)),
        ("status_page", lambda: _smoke_get("/status", 200)),
        ("grafana_no_redirect_loop", lambda: _smoke_get(
            "/grafana/", 302, lambda r: r.headers.get("Location", "").endswith("/grafana/login")
        )),
        ("mcp_tools_list", lambda: _smoke_post(
            "/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, 200, '"tools"'
        )),
        ("mcp_unknown_method_handled", lambda: _smoke_post(
            "/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "smoke-test/unknown", "params": {}}, 200, "-32601"
        )),
    ]
    results = []
    for name, fn in checks:
        ok, detail = fn()
        results.append({"name": name, "ok": ok, "detail": detail})
    return results


def run_deploy() -> None:
    global LAST_SMOKE_RESULTS

    for cmd, cwd in DEPLOY_STEPS:
        print(f"[deploy] $ {' '.join(cmd)} (cwd={cwd})", flush=True)
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        print(result.stdout, flush=True)
        if result.returncode != 0:
            print(result.stderr, flush=True)
            print(f"[deploy] step failed with exit code {result.returncode}, aborting", flush=True)
            return
    print("[deploy] done", flush=True)

    time.sleep(SMOKE_SETTLE_SECONDS)
    results = run_smoke_checks()
    LAST_SMOKE_RESULTS = {"timestamp": time.time(), "results": results}
    failed = [r for r in results if not r["ok"]]
    for r in results:
        print(f"[smoke] {r['name']}: {'PASS' if r['ok'] else 'FAIL - ' + r['detail']}", flush=True)
    if failed:
        print(f"[smoke] {len(failed)}/{len(results)} checks FAILED", flush=True)
    else:
        print(f"[smoke] all {len(results)} checks passed", flush=True)


class DeployWebhookHandler(BaseHTTPRequestHandler):
    secret: bytes = b""

    def do_GET(self):
        if self.path == "/healthz":
            self._respond(200, b"ok")
        elif self.path == "/smoke":
            self._respond(200, json.dumps(LAST_SMOKE_RESULTS).encode(), content_type="application/json")
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

    def _respond(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
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
