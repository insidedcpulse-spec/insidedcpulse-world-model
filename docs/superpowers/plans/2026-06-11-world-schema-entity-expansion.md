# World Schema Entity Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four new domain entity types — `incident`, `deployment`, `team`, `alert` — to `ENTITY_SCHEMAS`, matching the existing `region`/`service` pattern, with full test coverage and README docs.

**Architecture:** No new mechanism. `parse_key`, `get_field_spec`, and `check_domain_consistency`/`_validate_field_value` in `backend/app/validation.py` already operate generically over `ENTITY_SCHEMAS`. Each task adds one entity's `FieldSpec` dict entry plus tests proving `parse_key`/`get_field_spec`/`check_domain_consistency` behave correctly for it (valid op, enum/bounds rejection, op-type-compatibility rejection where relevant).

**Tech Stack:** Python 3.12, pytest, `backend/.venv`.

---

### Task 1: `incident` entity

**Files:**
- Modify: `backend/app/world_schema.py`
- Test: `backend/tests/test_world_schema.py`
- Test: `backend/tests/test_domain_validation.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_world_schema.py`:

```python
def test_parse_key_valid_incident():
    assert parse_key("incident.inc1.severity") == KeyParts("incident", "inc1", "severity")


def test_get_field_spec_incident_severity():
    assert get_field_spec("incident", "severity") == {
        "type": "enum",
        "values": ["low", "medium", "high", "critical"],
    }
```

Append to `backend/tests/test_domain_validation.py`:

```python
def test_set_incident_severity_valid():
    op = WorldOp(op="set", key="incident.inc1.severity", value="high")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_invalid_incident_severity():
    op = WorldOp(op="set", key="incident.inc1.severity", value="exploding")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "'severity' must be one of ['low', 'medium', 'high', 'critical'], got 'exploding'"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py::test_parse_key_valid_incident tests/test_world_schema.py::test_get_field_spec_incident_severity tests/test_domain_validation.py::test_set_incident_severity_valid tests/test_domain_validation.py::test_rejects_invalid_incident_severity -v`

Expected: all 4 FAIL — `parse_key`/`get_field_spec` return `None` for unknown entity `incident`, and `check_domain_consistency` returns `(False, "unknown key namespace 'incident.inc1.severity'")` instead of the expected results.

- [ ] **Step 3: Add the `incident` schema entry**

Edit `backend/app/world_schema.py`. Find the end of the `"service"` entry inside `ENTITY_SCHEMAS`:

```python
    "service": {
        "status": {"type": "enum", "values": ["healthy", "degraded", "down"]},
        "load": {"type": "number", "min": 0, "max": 100},
        "version": {"type": "string"},
        "capacity": {"type": "number", "min": 0},
    },
}
```

Replace with:

```python
    "service": {
        "status": {"type": "enum", "values": ["healthy", "degraded", "down"]},
        "load": {"type": "number", "min": 0, "max": 100},
        "version": {"type": "string"},
        "capacity": {"type": "number", "min": 0},
    },
    "incident": {
        "severity": {"type": "enum", "values": ["low", "medium", "high", "critical"]},
        "status": {"type": "enum", "values": ["open", "mitigated", "resolved"]},
        "affected_service": {"type": "string"},
        "affected_region": {"type": "string"},
        "notes": {"type": "object"},
    },
}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py::test_parse_key_valid_incident tests/test_world_schema.py::test_get_field_spec_incident_severity tests/test_domain_validation.py::test_set_incident_severity_valid tests/test_domain_validation.py::test_rejects_invalid_incident_severity -v`

Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/world_schema.py backend/tests/test_world_schema.py backend/tests/test_domain_validation.py
git commit -m "Add incident entity schema"
```

---

### Task 2: `deployment` entity

**Files:**
- Modify: `backend/app/world_schema.py`
- Test: `backend/tests/test_world_schema.py`
- Test: `backend/tests/test_domain_validation.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_world_schema.py`:

```python
def test_parse_key_valid_deployment():
    assert parse_key("deployment.dep1.status") == KeyParts("deployment", "dep1", "status")


def test_get_field_spec_deployment_progress():
    assert get_field_spec("deployment", "progress") == {"type": "number", "min": 0, "max": 100}
```

Append to `backend/tests/test_domain_validation.py`:

```python
def test_set_deployment_status_valid():
    op = WorldOp(op="set", key="deployment.dep1.status", value="in_progress")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_invalid_deployment_status():
    op = WorldOp(op="set", key="deployment.dep1.status", value="exploding")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "must be one of" in msg


def test_rejects_deployment_progress_above_max():
    op = WorldOp(op="set", key="deployment.dep1.progress", value=150)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "value 150 for 'progress' above maximum 100"


def test_rejects_deployment_progress_increment_overflow():
    op = WorldOp(op="increment", key="deployment.dep1.progress", value=1000)
    ok, msg = check_domain_consistency(op, 50)
    assert ok is False
    assert msg == "value 1050 for 'progress' above maximum 100"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py::test_parse_key_valid_deployment tests/test_world_schema.py::test_get_field_spec_deployment_progress tests/test_domain_validation.py -k deployment -v`

Expected: all 6 deployment-related tests FAIL (unknown entity `deployment`).

- [ ] **Step 3: Add the `deployment` schema entry**

Edit `backend/app/world_schema.py`. Find the end of the `"incident"` entry added in Task 1:

```python
    "incident": {
        "severity": {"type": "enum", "values": ["low", "medium", "high", "critical"]},
        "status": {"type": "enum", "values": ["open", "mitigated", "resolved"]},
        "affected_service": {"type": "string"},
        "affected_region": {"type": "string"},
        "notes": {"type": "object"},
    },
}
```

Replace with:

```python
    "incident": {
        "severity": {"type": "enum", "values": ["low", "medium", "high", "critical"]},
        "status": {"type": "enum", "values": ["open", "mitigated", "resolved"]},
        "affected_service": {"type": "string"},
        "affected_region": {"type": "string"},
        "notes": {"type": "object"},
    },
    "deployment": {
        "status": {"type": "enum", "values": ["pending", "in_progress", "done", "failed", "rolled_back"]},
        "version": {"type": "string"},
        "target_service": {"type": "string"},
        "progress": {"type": "number", "min": 0, "max": 100},
    },
}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py -k deployment tests/test_domain_validation.py -k deployment -v`

Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/world_schema.py backend/tests/test_world_schema.py backend/tests/test_domain_validation.py
git commit -m "Add deployment entity schema"
```

---

### Task 3: `team` entity

**Files:**
- Modify: `backend/app/world_schema.py`
- Test: `backend/tests/test_world_schema.py`
- Test: `backend/tests/test_domain_validation.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_world_schema.py`:

```python
def test_parse_key_valid_team():
    assert parse_key("team.sre.on_call") == KeyParts("team", "sre", "on_call")


def test_get_field_spec_team_headcount():
    assert get_field_spec("team", "headcount") == {"type": "integer", "min": 0}
```

Append to `backend/tests/test_domain_validation.py`:

```python
def test_set_team_on_call_valid():
    op = WorldOp(op="set", key="team.sre.on_call", value="active")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_invalid_team_on_call():
    op = WorldOp(op="set", key="team.sre.on_call", value="exploding")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "must be one of" in msg


def test_increment_team_headcount_valid():
    op = WorldOp(op="increment", key="team.sre.headcount", value=2)
    assert check_domain_consistency(op, 5) == (True, None)


def test_rejects_team_headcount_increment_below_min():
    op = WorldOp(op="increment", key="team.sre.headcount", value=-10)
    ok, msg = check_domain_consistency(op, 5)
    assert ok is False
    assert msg == "value -5 for 'headcount' below minimum 0"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py -k team tests/test_domain_validation.py -k team -v`

Expected: all 6 team-related tests FAIL (unknown entity `team`).

- [ ] **Step 3: Add the `team` schema entry**

Edit `backend/app/world_schema.py`. Find the end of the `"deployment"` entry added in Task 2:

```python
    "deployment": {
        "status": {"type": "enum", "values": ["pending", "in_progress", "done", "failed", "rolled_back"]},
        "version": {"type": "string"},
        "target_service": {"type": "string"},
        "progress": {"type": "number", "min": 0, "max": 100},
    },
}
```

Replace with:

```python
    "deployment": {
        "status": {"type": "enum", "values": ["pending", "in_progress", "done", "failed", "rolled_back"]},
        "version": {"type": "string"},
        "target_service": {"type": "string"},
        "progress": {"type": "number", "min": 0, "max": 100},
    },
    "team": {
        "on_call": {"type": "enum", "values": ["active", "off"]},
        "headcount": {"type": "integer", "min": 0},
        "owned_services": {"type": "object"},
    },
}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py -k team tests/test_domain_validation.py -k team -v`

Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/world_schema.py backend/tests/test_world_schema.py backend/tests/test_domain_validation.py
git commit -m "Add team entity schema"
```

---

### Task 4: `alert` entity

**Files:**
- Modify: `backend/app/world_schema.py`
- Test: `backend/tests/test_world_schema.py`
- Test: `backend/tests/test_domain_validation.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_world_schema.py`:

```python
def test_parse_key_valid_alert():
    assert parse_key("alert.a1.severity") == KeyParts("alert", "a1", "severity")


def test_get_field_spec_alert_status():
    assert get_field_spec("alert", "status") == {"type": "enum", "values": ["firing", "resolved"]}
```

Append to `backend/tests/test_domain_validation.py`:

```python
def test_set_alert_severity_valid():
    op = WorldOp(op="set", key="alert.a1.severity", value="critical")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_increment_on_alert_severity():
    op = WorldOp(op="increment", key="alert.a1.severity", value=1)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'increment' not allowed on field 'severity' (type 'enum')"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py -k alert tests/test_domain_validation.py -k alert -v`

Expected: all 4 alert-related tests FAIL (unknown entity `alert`).

- [ ] **Step 3: Add the `alert` schema entry**

Edit `backend/app/world_schema.py`. Find the end of the `"team"` entry added in Task 3:

```python
    "team": {
        "on_call": {"type": "enum", "values": ["active", "off"]},
        "headcount": {"type": "integer", "min": 0},
        "owned_services": {"type": "object"},
    },
}
```

Replace with:

```python
    "team": {
        "on_call": {"type": "enum", "values": ["active", "off"]},
        "headcount": {"type": "integer", "min": 0},
        "owned_services": {"type": "object"},
    },
    "alert": {
        "severity": {"type": "enum", "values": ["info", "warning", "critical"]},
        "status": {"type": "enum", "values": ["firing", "resolved"]},
        "source_service": {"type": "string"},
        "message": {"type": "object"},
    },
}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py -k alert tests/test_domain_validation.py -k alert -v`

Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/world_schema.py backend/tests/test_world_schema.py backend/tests/test_domain_validation.py
git commit -m "Add alert entity schema"
```

---

### Task 5: README docs + full suite verification

**Files:**
- Modify: `README.md:91-96`

- [ ] **Step 1: Add the four new entity rows to the schema table**

Edit `README.md`. Find:

```markdown
| Entity | `id` | Fields |
|---|---|---|
| `region` | `^[a-z0-9_]{1,32}$` | `capacity_forecast` (number, >=0), `population` (integer, >=0), `status` (enum: `stable`\|`growing`\|`declining`\|`critical`), `notes` (object) |
| `service` | `^[a-z0-9_]{1,32}$` | `status` (enum: `healthy`\|`degraded`\|`down`), `load` (number, 0-100), `version` (string), `capacity` (number, >=0) |

Any op on a key outside this schema (wrong shape, unknown entity/field,
wrong type, out-of-range value, or an `op` incompatible with the field's
type — e.g. `merge` on an enum field) is rejected as inconsistent.
```

Replace with:

```markdown
| Entity | `id` | Fields |
|---|---|---|
| `region` | `^[a-z0-9_]{1,32}$` | `capacity_forecast` (number, >=0), `population` (integer, >=0), `status` (enum: `stable`\|`growing`\|`declining`\|`critical`), `notes` (object) |
| `service` | `^[a-z0-9_]{1,32}$` | `status` (enum: `healthy`\|`degraded`\|`down`), `load` (number, 0-100), `version` (string), `capacity` (number, >=0) |
| `incident` | `^[a-z0-9_]{1,32}$` | `severity` (enum: `low`\|`medium`\|`high`\|`critical`), `status` (enum: `open`\|`mitigated`\|`resolved`), `affected_service` (string), `affected_region` (string), `notes` (object) |
| `deployment` | `^[a-z0-9_]{1,32}$` | `status` (enum: `pending`\|`in_progress`\|`done`\|`failed`\|`rolled_back`), `version` (string), `target_service` (string), `progress` (number, 0-100) |
| `team` | `^[a-z0-9_]{1,32}$` | `on_call` (enum: `active`\|`off`), `headcount` (integer, >=0), `owned_services` (object) |
| `alert` | `^[a-z0-9_]{1,32}$` | `severity` (enum: `info`\|`warning`\|`critical`), `status` (enum: `firing`\|`resolved`), `source_service` (string), `message` (object) |

Any op on a key outside this schema (wrong shape, unknown entity/field,
wrong type, out-of-range value, or an `op` incompatible with the field's
type — e.g. `merge` on an enum field) is rejected as inconsistent.

`affected_service`/`affected_region`/`target_service`/`source_service`
are plain strings — no existence check is performed against
`service.*`/`region.*` entities.

Example ops for the new entities:

```json
[
  { "op": "set", "key": "incident.inc1.severity", "value": "high" },
  { "op": "set", "key": "deployment.dep1.status", "value": "in_progress" },
  { "op": "set", "key": "team.sre.on_call", "value": "active" },
  { "op": "set", "key": "alert.a1.severity", "value": "warning" }
]
```
```

- [ ] **Step 2: Run the full backend test suite**

Run: `cd backend && .venv/bin/pytest -q`

Expected: `80 passed` (60 existing + 20 new: 8 in `test_world_schema.py`, 12 in `test_domain_validation.py`)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document incident/deployment/team/alert entity schemas"
```
