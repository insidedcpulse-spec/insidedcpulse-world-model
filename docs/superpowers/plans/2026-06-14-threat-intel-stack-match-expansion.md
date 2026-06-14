# Threat-Intel Agent STACK_MATCHES Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the hand-maintained `STACK_MATCHES` correlation table in `scripts/agents/threat_intel_agent.py` from 7 to 13 rows so future CISA KEV entries that mention any component of the deployed InsideDCPulse stack get a non-empty `stack_match`/`affected_service`.

**Architecture:** Single-file, single-list change — no new functions, no schema/engine/graph changes. `match_stack()` already does first-match-wins case-insensitive substring scanning over `vendor + product + name + description`; this plan only edits the data it scans against.

**Tech Stack:** Python 3 (system `python3`, no venv needed — `threat_intel_agent.py` only needs stdlib + `requests` for this change, and `match_stack`/`STACK_MATCHES` have zero external deps).

---

### Task 1: Expand STACK_MATCHES table

**Files:**
- Modify: `scripts/agents/threat_intel_agent.py:36-44`

- [ ] **Step 1: Edit the `STACK_MATCHES` list**

Replace the current table (lines 36-44):

```python
STACK_MATCHES: list[tuple[list[str], str, str]] = [
    (["nginx"], "nginx:1.27-alpine", "service.checkout"),
    (["postgres", "postgresql"], "postgres:16-alpine", "service.payments_db"),
    (["redis"], "redis:7-alpine", "service.checkout"),
    (["grafana"], "grafana:13.0.2", "team.sre"),
    (["prometheus"], "prometheus:v3.12.0", "team.sre"),
    (["certbot", "let's encrypt", "acme"], "certbot:v5.6.0", "team.sre"),
    (["fastapi", "starlette", "uvicorn", "mcp"], "fastapi/starlette/mcp", "team.sre"),
]
```

with:

```python
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
```

- [ ] **Step 2: Verify ordering and matches with a manual smoke check**

Run (from `scripts/agents/`, system `python3` — no venv needed):

```bash
cd /root/insidedcpulse-world-model/scripts/agents && python3 -c "
from threat_intel_agent import match_stack, STACK_MATCHES

assert len(STACK_MATCHES) == 13, len(STACK_MATCHES)

cases = [
    (('Linux', 'Linux Kernel', 'cgroups release_agent', 'improper authentication'), ('linux kernel (host OS)', 'team.sre')),
    (('PostgreSQL', 'PostgreSQL', '', 'sql injection'), ('postgres:16-alpine', 'service.payments_db')),
    (('PyPI', 'asyncpg', '', 'connection pool exhaustion'), ('asyncpg==0.30.0', 'service.payments_db')),
    (('PyPI', 'pydantic', '', 'regex denial of service'), ('fastapi/starlette/mcp', 'team.sre')),
    (('Docker', 'containerd', '', 'container escape'), ('docker (container runtime)', 'team.sre')),
    (('Alpine', 'Alpine Linux', '', 'musl libc overflow'), ('alpine (nginx/redis/postgres base images)', 'team.sre')),
    (('OpenSSL', 'OpenSSL', '', 'heap buffer overflow'), ('openssl (TLS)', 'team.sre')),
    (('Debian', 'Debian GNU/Linux', '', 'apt signature bypass'), ('debian (python:3.12-slim base)', 'team.sre')),
    (('Android', 'Android Framework', '', 'integer overflow'), ('', '')),
]

for args, expected in cases:
    result = match_stack(*args)
    assert result == expected, (args, result, expected)
    print('OK', args[1], '->', result)

print('all', len(cases), 'cases passed')
"
```

Expected output (13 lines):

```
OK Linux Kernel -> ('linux kernel (host OS)', 'team.sre')
OK PostgreSQL -> ('postgres:16-alpine', 'service.payments_db')
OK asyncpg -> ('asyncpg==0.30.0', 'service.payments_db')
OK pydantic -> ('fastapi/starlette/mcp', 'team.sre')
OK containerd -> ('docker (container runtime)', 'team.sre')
OK Alpine Linux -> ('alpine (nginx/redis/postgres base images)', 'team.sre')
OK OpenSSL -> ('openssl (TLS)', 'team.sre')
OK Debian GNU/Linux -> ('debian (python:3.12-slim base)', 'team.sre')
OK Android Framework -> ('', '')
all 9 cases passed
```

If any `assert` fails, the `AssertionError` shows `(args, result, expected)` —
check row ordering in `STACK_MATCHES` (an earlier broad row may be shadowing
a later specific one) before re-running.

- [ ] **Step 3: Commit**

```bash
git add scripts/agents/threat_intel_agent.py
git commit -m "feat: expand STACK_MATCHES to cover full deployed stack

Adds asyncpg, docker/containerd/runc/moby, alpine, openssl, debian, and
linux kernel rows, plus pydantic/orjson/sse-starlette to the existing
fastapi row. Covers 13/14 components of the deployed InsideDCPulse
stack (was 7/14), per docs/superpowers/specs/2026-06-14-threat-intel-stack-match-expansion-design.md."
```

---

### Task 2: Push, PR, merge, deploy

**Files:** none (process task)

- [ ] **Step 1: Create branch, push**

```bash
cd /root/insidedcpulse-world-model
git checkout -b feature/threat-intel-stack-match-expansion
```

(Task 1's commit should be made on this branch — if Task 1 was done on
`main`, instead run `git branch feature/threat-intel-stack-match-expansion`
then `git reset --hard origin/main` on `main` and `git checkout
feature/threat-intel-stack-match-expansion`.)

Push using the project's documented push pattern (fresh classic PAT at
`/root/insidedcpulse-secrets/github_pat.env`, verify via `/user` first):

```bash
TOK=$(grep -oP 'GITHUB_PAT_CLASSIC=\K.*' /root/insidedcpulse-secrets/github_pat.env)
curl -s -H "Authorization: Bearer $TOK" https://api.github.com/user | grep login
git -c credential.helper= push -u "https://x-access-token:${TOK}@github.com/insidedcpulse-spec/insidedcpulse-world-model.git" feature/threat-intel-stack-match-expansion
git config --local branch.feature/threat-intel-stack-match-expansion.remote origin
```

- [ ] **Step 2: Open PR**

```bash
TOK=$(grep -oP 'GITHUB_PAT_CLASSIC=\K.*' /root/insidedcpulse-secrets/github_pat.env)
GH_TOKEN=$TOK gh pr create --repo insidedcpulse-spec/insidedcpulse-world-model \
  --head feature/threat-intel-stack-match-expansion --base main \
  --title "feat: expand threat-intel-agent STACK_MATCHES to full deployed stack" \
  --body "$(cat <<'EOF'
## Summary
- Expands the hand-maintained `STACK_MATCHES` table in `scripts/agents/threat_intel_agent.py` from 7 to 13 rows, covering 13/14 components of the deployed InsideDCPulse stack (was 7/14).
- New rows: asyncpg, docker/containerd/runc/moby, alpine, openssl, debian, linux kernel. Existing fastapi row extended with pydantic/orjson/sse-starlette.
- No schema/engine/graph changes — `stack_match`/`affected_service` fields and the `REFERENCES`-edge logic already existed.

## Test plan
- [x] Manual smoke check (`match_stack` against 9 sample inputs covering all new rows + one no-match case) — see plan Task 1 Step 2.
EOF
)"
```

- [ ] **Step 3: Merge PR**

```bash
TOK=$(grep -oP 'GITHUB_PAT_CLASSIC=\K.*' /root/insidedcpulse-secrets/github_pat.env)
GH_TOKEN=$TOK gh pr merge --repo insidedcpulse-spec/insidedcpulse-world-model <PR_NUMBER> --merge --delete-branch
```

- [ ] **Step 4: Sync local main, clean up branch**

```bash
git checkout main
git -c credential.helper= fetch "https://x-access-token:$(grep -oP 'GITHUB_PAT_CLASSIC=\K.*' /root/insidedcpulse-secrets/github_pat.env)@github.com/insidedcpulse-spec/insidedcpulse-world-model.git" main
git merge --ff-only FETCH_HEAD
git branch -d feature/threat-intel-stack-match-expansion
```

- [ ] **Step 5: Verify webhook deploy + smoke**

This change touches `scripts/agents/` only — the running `docker-api-1`
container does NOT include `scripts/` (only `backend/` is in the API
build context, per project memory), so no rebuild is strictly required for
this change to take effect. However, the webhook still fires on every push
to `main` and runs `docker compose build/up api` + smoke checks. Confirm it
completes cleanly (no regressions):

```bash
until curl -s localhost:9001/smoke | grep -q '"timestamp"'; do sleep 3; done
curl -s localhost:9001/smoke | python3 -m json.tool
```

Expected: all 6 checks `"ok": true` (`healthz`, `status_page`,
`landing_page`, `grafana_no_redirect_loop`, `mcp_tools_list`,
`mcp_unknown_method_handled`), `timestamp` recent (within the last minute).

- [ ] **Step 6: Confirm cron picks up the new code**

The cron entry runs `python3
/root/insidedcpulse-world-model/scripts/agents/threat_intel_agent.py
/root/insidedcpulse-secrets/agents/threat-intel-agent.env` directly from
this working copy (not from the Docker image) — since `git merge --ff-only`
in Step 4 already updated the working copy, the next `:15` cron run (or a
manual run) automatically uses the expanded `STACK_MATCHES`. No further
action needed; do NOT manually re-run `threat_intel_agent.py` just to
"test" this — it would consume that hour's KEV dedup slot for a CVE that
may or may not match any new row (expected per spec).

---

## Self-Review Notes

- **Spec coverage**: Task 1 implements the full table from the spec
  verbatim (13 rows, same order). Task 2 covers the spec's implicit
  "ship it" requirement (push/PR/merge/deploy, following established
  project pattern).
- **No automated tests**: matches spec's "Out of scope" — Task 1 Step 2 is
  the manual verification the spec calls for.
- **No retroactive backfill**: not implemented, per spec (existing 10
  `vulnerability.*` entries keep empty `stack_match`/`affected_service`
  until FIFO-evicted) — no task does this, correctly.
