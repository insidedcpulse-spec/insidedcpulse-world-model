# Webhook Auto-Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the billing-locked GitHub Actions deploy with a webhook-triggered deploy: GitHub push to `main` -> nginx -> a small Python listener on the VPS host -> `git pull` + `docker compose build/up` for the `api` service.

**Architecture:** A stdlib-only Python HTTP server (`scripts/deploy_webhook.py`) runs as a systemd service on the VPS host, listening on `127.0.0.1:9001`. nginx (in docker, with `extra_hosts: host-gateway`) proxies `POST /hooks/deploy` to it. The handler verifies the GitHub `X-Hub-Signature-256` HMAC, checks the event is a `push` to `refs/heads/main`, and runs the same five commands as the existing `.github/workflows/deploy.yml` in a background thread.

**Tech Stack:** Python 3.12 stdlib (`http.server`, `hmac`, `hashlib`, `subprocess`, `threading`, `unittest`), nginx, docker compose, systemd, `gh` CLI.

Spec: `docs/superpowers/specs/2026-06-10-webhook-deploy-design.md`

---

## File Structure

- `scripts/deploy_webhook.py` (new) — the webhook listener (functions + HTTP handler + `main()`)
- `scripts/test_deploy_webhook.py` (new) — unit + integration tests, stdlib `unittest`
- `docker/docker-compose.yml` (modify) — add `extra_hosts` to the `nginx` service
- `docker/nginx/conf.d/insidedcpulse.conf.ssl` (modify) — add `/hooks/deploy` location
- VPS host (not tracked): `/root/insidedcpulse-secrets/webhook.env`, `/etc/systemd/system/insidedcpulse-webhook.service`, active `docker/nginx/conf.d/insidedcpulse.conf`

All local repo work happens in `/root/insidedcpulse-world-model`. VPS is `2.25.169.27`, SSH key `/root/.ssh/insidedcpulse_deploy`, repo at `/opt/insidedcpulse-world-model`.

---

### Task 1: Signature verification + event filtering

**Files:**
- Create: `scripts/deploy_webhook.py`
- Create: `scripts/test_deploy_webhook.py`

- [ ] **Step 1: Write the failing tests**

`scripts/test_deploy_webhook.py`:

```python
import hashlib
import hmac
import unittest

from deploy_webhook import should_deploy, verify_signature

SECRET = b"test-secret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


class TestVerifySignature(unittest.TestCase):
    def test_valid_signature(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertTrue(verify_signature(SECRET, body, sign(body)))

    def test_invalid_signature(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertFalse(verify_signature(SECRET, body, "sha256=deadbeef"))

    def test_missing_header(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertFalse(verify_signature(SECRET, body, None))

    def test_wrong_prefix(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertFalse(verify_signature(SECRET, body, "sha1=abcd"))


class TestShouldDeploy(unittest.TestCase):
    def test_push_to_main(self):
        self.assertTrue(should_deploy("push", {"ref": "refs/heads/main"}))

    def test_push_to_other_branch(self):
        self.assertFalse(should_deploy("push", {"ref": "refs/heads/feature-x"}))

    def test_non_push_event(self):
        self.assertFalse(should_deploy("ping", {"ref": "refs/heads/main"}))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/insidedcpulse-world-model/scripts && python3 -m unittest test_deploy_webhook -v`
Expected: `ModuleNotFoundError: No module named 'deploy_webhook'`

- [ ] **Step 3: Write minimal implementation**

`scripts/deploy_webhook.py`:

```python
#!/usr/bin/env python3
"""Webhook listener: verifies a GitHub push to main and redeploys the api service."""
import hashlib
import hmac


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/insidedcpulse-world-model/scripts && python3 -m unittest test_deploy_webhook -v`
Expected: `OK` (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/insidedcpulse-world-model
git add scripts/deploy_webhook.py scripts/test_deploy_webhook.py
git commit -m "feat: add webhook signature verification and event filtering"
```

---

### Task 2: Deploy command sequence

**Files:**
- Modify: `scripts/deploy_webhook.py`
- Modify: `scripts/test_deploy_webhook.py`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_deploy_webhook.py` (add `from unittest.mock import patch` to imports, add `run_deploy` to the import from `deploy_webhook`):

```python
from unittest.mock import patch

from deploy_webhook import run_deploy, should_deploy, verify_signature
```

Add this test class:

```python
class TestRunDeploy(unittest.TestCase):
    @patch("deploy_webhook.subprocess.run")
    def test_runs_all_steps_on_success(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        run_deploy()
        self.assertEqual(mock_run.call_count, 5)
        first_cmd = mock_run.call_args_list[0].args[0]
        self.assertEqual(first_cmd, ["git", "fetch", "origin", "main"])
        last_cmd = mock_run.call_args_list[-1].args[0]
        self.assertEqual(last_cmd, ["docker", "image", "prune", "-f"])

    @patch("deploy_webhook.subprocess.run")
    def test_stops_on_first_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "boom"
        run_deploy()
        self.assertEqual(mock_run.call_count, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/insidedcpulse-world-model/scripts && python3 -m unittest test_deploy_webhook -v`
Expected: `ImportError: cannot import name 'run_deploy' from 'deploy_webhook'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/deploy_webhook.py` (after the imports, add `import subprocess`; after `should_deploy`, add):

```python
import subprocess

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/insidedcpulse-world-model/scripts && python3 -m unittest test_deploy_webhook -v`
Expected: `OK` (8 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/insidedcpulse-world-model
git add scripts/deploy_webhook.py scripts/test_deploy_webhook.py
git commit -m "feat: add deploy command sequence"
```

---

### Task 3: HTTP handler + server entrypoint

**Files:**
- Modify: `scripts/deploy_webhook.py`
- Modify: `scripts/test_deploy_webhook.py`

- [ ] **Step 1: Write the failing tests**

Update the imports at the top of `scripts/test_deploy_webhook.py` to:

```python
import hashlib
import hmac
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from deploy_webhook import (
    DeployWebhookHandler,
    run_deploy,
    should_deploy,
    verify_signature,
)
```

Add this test class at the end of the file (before `if __name__ == "__main__":`):

```python
class TestDeployWebhookHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        DeployWebhookHandler.secret = SECRET
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DeployWebhookHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()

    def _post(self, path, body, headers):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_healthz(self):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read(), b"ok")

    def test_invalid_signature_rejected(self):
        body = b'{"ref": "refs/heads/main"}'
        status, _ = self._post(
            "/hooks/deploy",
            body,
            {
                "X-Hub-Signature-256": "sha256=deadbeef",
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 401)

    @patch("deploy_webhook.run_deploy")
    def test_valid_push_to_main_triggers_deploy(self, mock_deploy):
        body = b'{"ref": "refs/heads/main"}'
        status, resp_body = self._post(
            "/hooks/deploy",
            body,
            {
                "X-Hub-Signature-256": sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(resp_body, b"ok")
        for _ in range(50):
            if mock_deploy.called:
                break
            threading.Event().wait(0.01)
        mock_deploy.assert_called_once()

    @patch("deploy_webhook.run_deploy")
    def test_valid_push_to_other_branch_no_deploy(self, mock_deploy):
        body = b'{"ref": "refs/heads/feature-x"}'
        status, _ = self._post(
            "/hooks/deploy",
            body,
            {
                "X-Hub-Signature-256": sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 200)
        threading.Event().wait(0.05)
        mock_deploy.assert_not_called()

    def test_unknown_path(self):
        status, _ = self._post(
            "/other",
            b"{}",
            {
                "X-Hub-Signature-256": sign(b"{}"),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/insidedcpulse-world-model/scripts && python3 -m unittest test_deploy_webhook -v`
Expected: `ImportError: cannot import name 'DeployWebhookHandler' from 'deploy_webhook'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/deploy_webhook.py` (add `import json`, `import os`, `import threading` to imports, and `from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer`; append at the end of the file):

```python
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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
    server = ThreadingHTTPServer(("127.0.0.1", 9001), DeployWebhookHandler)
    print("[webhook] listening on 127.0.0.1:9001", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/insidedcpulse-world-model/scripts && python3 -m unittest test_deploy_webhook -v`
Expected: `OK` (13 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/insidedcpulse-world-model
git add scripts/deploy_webhook.py scripts/test_deploy_webhook.py
git commit -m "feat: add webhook HTTP handler and server entrypoint"
```

---

### Task 4: nginx — route to the host webhook listener

**Files:**
- Modify: `docker/docker-compose.yml`
- Modify: `docker/nginx/conf.d/insidedcpulse.conf.ssl`

- [ ] **Step 1: Add `extra_hosts` to the nginx service**

In `docker/docker-compose.yml`, find the `nginx` service:

```yaml
  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
```

Change it to:

```yaml
  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "80:80"
      - "443:443"
```

- [ ] **Step 2: Add the `/hooks/deploy` location to the SSL config**

In `docker/nginx/conf.d/insidedcpulse.conf.ssl`, inside the `listen 443 ssl http2` server block, add a new location **before** the `# Public API` / `location /` block:

```nginx
    # Deploy webhook (forwarded to host listener on port 9001)
    location /hooks/deploy {
        proxy_pass http://host.docker.internal:9001/hooks/deploy;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-GitHub-Event $http_x_github_event;
        proxy_set_header X-Hub-Signature-256 $http_x_hub_signature_256;
    }

    # Public API
    location / {
```

- [ ] **Step 3: Validate YAML and nginx syntax**

Run: `cd /root/insidedcpulse-world-model/docker && python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml'))" && echo "yaml ok"`
Expected: `yaml ok`

(nginx config syntax will be validated on the VPS in Task 6, where `docker compose exec nginx nginx -t` can run against the real container.)

- [ ] **Step 4: Commit**

```bash
cd /root/insidedcpulse-world-model
git add docker/docker-compose.yml docker/nginx/conf.d/insidedcpulse.conf.ssl
git commit -m "feat: proxy /hooks/deploy to host webhook listener"
```

---

### Task 5: Push to GitHub

**Files:** none (git operation only)

- [ ] **Step 1: Push the branch**

```bash
cd /root/insidedcpulse-world-model
git push origin main
```

Expected: push succeeds (this is a plain `git push`, not Actions — billing lock does not block git operations).

---

### Task 6: VPS — webhook secret + pull latest code

**Files (VPS only, not tracked):**
- Create: `/root/insidedcpulse-secrets/webhook.env` (on VPS and locally)

- [ ] **Step 1: Generate the shared secret locally**

```bash
WEBHOOK_SECRET=$(openssl rand -hex 32)
echo "WEBHOOK_SECRET=$WEBHOOK_SECRET" | tee /root/insidedcpulse-secrets/webhook.env
```

Keep this terminal's `$WEBHOOK_SECRET` value — it's needed again in Task 9.

- [ ] **Step 2: Copy the secret file to the VPS**

```bash
scp -i /root/.ssh/insidedcpulse_deploy /root/insidedcpulse-secrets/webhook.env root@2.25.169.27:/root/insidedcpulse-secrets/webhook.env
```

- [ ] **Step 3: Pull the new code on the VPS**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "cd /opt/insidedcpulse-world-model && git pull origin main"
```

Expected: fast-forward merge bringing in `scripts/deploy_webhook.py`, `scripts/test_deploy_webhook.py`, `docker/docker-compose.yml`, `docker/nginx/conf.d/insidedcpulse.conf.ssl`.

- [ ] **Step 4: Run the unit tests on the VPS**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "cd /opt/insidedcpulse-world-model/scripts && python3 -m unittest test_deploy_webhook -v"
```

Expected: `OK` (13 tests)

---

### Task 7: VPS — systemd service for the webhook listener

**Files (VPS only, not tracked):**
- Create: `/etc/systemd/system/insidedcpulse-webhook.service`

- [ ] **Step 1: Create the systemd unit file**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "cat > /etc/systemd/system/insidedcpulse-webhook.service" <<'EOF'
[Unit]
Description=InsideDCPulse deploy webhook
After=network.target docker.service

[Service]
Type=simple
User=root
EnvironmentFile=/root/insidedcpulse-secrets/webhook.env
ExecStart=/usr/bin/python3 /opt/insidedcpulse-world-model/scripts/deploy_webhook.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
```

- [ ] **Step 2: Enable and start the service**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "systemctl daemon-reload && systemctl enable --now insidedcpulse-webhook"
```

- [ ] **Step 3: Verify it's listening**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "systemctl is-active insidedcpulse-webhook && curl -s http://127.0.0.1:9001/healthz"
```

Expected: `active` then `ok`

- [ ] **Step 4: Check the logs**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "journalctl -u insidedcpulse-webhook -n 5 --no-pager"
```

Expected: last line is `[webhook] listening on 127.0.0.1:9001`

---

### Task 8: VPS — apply nginx config (extra_hosts + /hooks/deploy route)

**Files (VPS only):**
- Modify: active `/opt/insidedcpulse-world-model/docker/nginx/conf.d/insidedcpulse.conf` (untracked, currently a copy of `insidedcpulse.conf.ssl`)

- [ ] **Step 1: Copy the updated SSL config over the active config**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "cd /opt/insidedcpulse-world-model/docker && cp nginx/conf.d/insidedcpulse.conf.ssl nginx/conf.d/insidedcpulse.conf"
```

- [ ] **Step 2: Recreate nginx with the new compose config (extra_hosts) and validate config syntax**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "cd /opt/insidedcpulse-world-model/docker && docker compose up -d nginx && docker compose exec nginx nginx -t"
```

Expected: nginx container recreated, `nginx -t` reports `syntax is ok` / `test is successful`

- [ ] **Step 3: Confirm the new route resolves to the listener**

```bash
curl -s -o /dev/null -w "%{http_code}\n" --resolve insidedcpulse.com:443:2.25.169.27 -X POST https://insidedcpulse.com/hooks/deploy
```

Expected: `401` (no signature header sent — proves nginx reached the listener and the listener correctly rejected the unsigned request)

---

### Task 9: GitHub — register the webhook

**Files:** none (GitHub API via `gh` CLI)

- [ ] **Step 1: Register the webhook**

Use the same `$WEBHOOK_SECRET` value generated in Task 6, Step 1:

```bash
gh api repos/insidedcpulse-spec/insidedcpulse-world-model/hooks \
  -f name=web \
  -f config[url]=https://insidedcpulse.com/hooks/deploy \
  -f config[content_type]=json \
  -f config[secret]="$WEBHOOK_SECRET" \
  -f config[insecure_ssl]=0 \
  -f events[]=push \
  -F active=true
```

Expected: JSON response with `"id": <hook_id>` and `"active": true`. Note the `id` for the next step.

- [ ] **Step 2: Send a ping test**

```bash
gh api repos/insidedcpulse-spec/insidedcpulse-world-model/hooks/<hook_id>/tests -X POST
```

(Replace `<hook_id>` with the id from Step 1.)

- [ ] **Step 3: Verify the listener received it**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "journalctl -u insidedcpulse-webhook -n 10 --no-pager"
```

Expected: a line like `[webhook] ignoring event=ping ref=None` and `[http] "POST /hooks/deploy HTTP/1.1" 200 -`

---

### Task 10: End-to-end test — real push triggers a deploy

**Files:** none (verification only)

- [ ] **Step 1: Note current `api` container creation time**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "docker inspect -f '{{.Created}}' docker-api-1"
```

- [ ] **Step 2: Make a trivial commit and push to main**

```bash
cd /root/insidedcpulse-world-model
git commit --allow-empty -m "chore: trigger webhook deploy test"
git push origin main
```

- [ ] **Step 3: Watch the deploy run**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "journalctl -u insidedcpulse-webhook -f"
```

Expected (within ~30s of the push): `[webhook] push to refs/heads/main, deploying`, followed by the five `[deploy] $ ...` lines, ending with `[deploy] done`. Press `Ctrl+C` once seen.

- [ ] **Step 4: Confirm the `api` container was recreated**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 "docker inspect -f '{{.Created}}' docker-api-1 && docker compose -f /opt/insidedcpulse-world-model/docker/docker-compose.yml ps api"
```

Expected: `Created` timestamp is newer than Step 1's, `api` service is `Up` and `healthy`.

- [ ] **Step 5: Confirm the site still responds**

```bash
curl -s -o /dev/null -w "%{http_code}\n" --resolve insidedcpulse.com:443:2.25.169.27 https://insidedcpulse.com/docs
```

Expected: `200`