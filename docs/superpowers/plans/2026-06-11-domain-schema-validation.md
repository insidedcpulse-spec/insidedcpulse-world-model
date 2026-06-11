# Domain Key Schema + Rigid Consistency Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the first real `world_state` domain (regions and services)
with a fixed key namespace + field schema, and add hard-fail validation
rules in the backend that reject any op violating that schema.

**Architecture:** New `backend/app/world_schema.py` declares
`ENTITY_SCHEMAS` (region/service field specs) and `parse_key()` for the
`<entity>.<id>.<field>` namespace. `backend/app/validation.py` gets a new
`check_domain_consistency(op, current)` (namespace/field/type/enum/bounds,
including increment-overflow projection) that
`check_op_consistency` calls after its existing generic checks. README
gains a schema section so external agents know the valid key namespace.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode = auto`), no new
dependencies.

Spec: `docs/superpowers/specs/2026-06-11-domain-schema-validation-design.md`

---

### Task 1: Domain key schema module

**Files:**
- Create: `backend/app/world_schema.py`
- Test: `backend/tests/test_world_schema.py`

- [ ] **Step 1: Write the failing tests**

```python
from app.world_schema import KeyParts, get_field_spec, parse_key


def test_parse_key_valid_region():
    assert parse_key("region.eu.status") == KeyParts("region", "eu", "status")


def test_parse_key_valid_service():
    assert parse_key("service.api.load") == KeyParts("service", "api", "load")


def test_parse_key_rejects_two_segments():
    assert parse_key("demo.counter") is None


def test_parse_key_rejects_four_segments():
    assert parse_key("region.eu.status.extra") is None


def test_parse_key_rejects_unknown_entity():
    assert parse_key("foo.bar.baz") is None


def test_parse_key_rejects_uppercase_id():
    assert parse_key("region.EU.status") is None


def test_parse_key_rejects_invalid_id_chars():
    assert parse_key("region.e!u.status") is None


def test_get_field_spec_known_field():
    assert get_field_spec("region", "status") == {
        "type": "enum",
        "values": ["stable", "growing", "declining", "critical"],
    }


def test_get_field_spec_unknown_field():
    assert get_field_spec("region", "nonexistent") is None


def test_get_field_spec_unknown_entity():
    assert get_field_spec("foo", "bar") is None
```

Save as `backend/tests/test_world_schema.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.world_schema'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/world_schema.py`:

```python
"""Domain schema for world_state keys: <entity>.<id>.<field>.

This is the first real domain for InsideDCPulse: an
infrastructure/project status world (regions and services). Only keys
matching this schema pass app.validation.check_domain_consistency.
"""

import re
from typing import NamedTuple, TypedDict

KEY_PATTERN = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z0-9_]{1,32})\.([a-z][a-z0-9_]*)$")


class FieldSpec(TypedDict, total=False):
    type: str  # "number" | "integer" | "string" | "enum" | "object"
    min: float
    max: float
    values: list[str]


ENTITY_SCHEMAS: dict[str, dict[str, FieldSpec]] = {
    "region": {
        "capacity_forecast": {"type": "number", "min": 0},
        "population": {"type": "integer", "min": 0},
        "status": {"type": "enum", "values": ["stable", "growing", "declining", "critical"]},
        "notes": {"type": "object"},
    },
    "service": {
        "status": {"type": "enum", "values": ["healthy", "degraded", "down"]},
        "load": {"type": "number", "min": 0, "max": 100},
        "version": {"type": "string"},
        "capacity": {"type": "number", "min": 0},
    },
}


class KeyParts(NamedTuple):
    entity: str
    entity_id: str
    field: str


def parse_key(key: str) -> KeyParts | None:
    """Parse '<entity>.<id>.<field>' if entity is a known schema, else None."""
    match = KEY_PATTERN.match(key)
    if not match:
        return None
    entity, entity_id, field = match.groups()
    if entity not in ENTITY_SCHEMAS:
        return None
    return KeyParts(entity, entity_id, field)


def get_field_spec(entity: str, field: str) -> FieldSpec | None:
    return ENTITY_SCHEMAS.get(entity, {}).get(field)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_world_schema.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/world_schema.py backend/tests/test_world_schema.py
git commit -m "Add domain key schema (region/service entities)"
```

---

### Task 2: `check_domain_consistency` (pure validation function)

**Files:**
- Modify: `backend/app/validation.py`
- Test: `backend/tests/test_domain_validation.py`

- [ ] **Step 1: Write the failing tests**

Save as `backend/tests/test_domain_validation.py`:

```python
from app.schemas import WorldOp
from app.validation import check_domain_consistency


def test_set_region_status_valid():
    op = WorldOp(op="set", key="region.eu.status", value="stable")
    assert check_domain_consistency(op, None) == (True, None)


def test_set_service_load_valid():
    op = WorldOp(op="set", key="service.api.load", value=42)
    assert check_domain_consistency(op, None) == (True, None)


def test_merge_region_notes_valid():
    op = WorldOp(op="merge", key="region.eu.notes", value={"last_proposal_by": "agent-x"})
    assert check_domain_consistency(op, {"a": 1}) == (True, None)


def test_increment_service_load_within_bounds():
    op = WorldOp(op="increment", key="service.api.load", value=10)
    assert check_domain_consistency(op, 20) == (True, None)


def test_delete_bypasses_namespace_check():
    op = WorldOp(op="delete", key="not.a.real.key")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_two_segment_key():
    op = WorldOp(op="set", key="demo.counter", value=1)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown key namespace 'demo.counter'"


def test_rejects_unknown_entity():
    op = WorldOp(op="set", key="world.status", value="building")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "unknown key namespace" in msg


def test_rejects_unknown_field():
    op = WorldOp(op="set", key="region.eu.unknown_field", value=1)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown field 'unknown_field' for entity 'region'"


def test_rejects_invalid_enum_value():
    op = WorldOp(op="set", key="region.eu.status", value="exploding")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "must be one of" in msg


def test_rejects_wrong_type_for_number_field():
    op = WorldOp(op="set", key="service.api.load", value="high")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "must be a number" in msg


def test_rejects_value_above_max():
    op = WorldOp(op="set", key="service.api.load", value=150)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "above maximum 100" in msg


def test_rejects_value_below_min():
    op = WorldOp(op="set", key="region.eu.population", value=-1)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "below minimum 0" in msg


def test_rejects_non_integer_population():
    op = WorldOp(op="set", key="region.eu.population", value=10.5)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert "must be an integer" in msg


def test_rejects_increment_overflow_projected():
    op = WorldOp(op="increment", key="service.api.load", value=1000)
    ok, msg = check_domain_consistency(op, 50)
    assert ok is False
    assert "above maximum 100" in msg


def test_rejects_merge_on_enum_field():
    op = WorldOp(op="merge", key="service.api.status", value={"x": 1})
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'merge' not allowed on field 'status' (type 'enum')"


def test_rejects_increment_on_object_field():
    op = WorldOp(op="increment", key="region.eu.notes", value=1)
    ok, msg = check_domain_consistency(op, {"a": 1})
    assert ok is False
    assert msg == "op 'increment' not allowed on field 'notes' (type 'object')"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_domain_validation.py -v`
Expected: FAIL with `ImportError: cannot import name 'check_domain_consistency' from 'app.validation'`

- [ ] **Step 3: Write the implementation**

In `backend/app/validation.py`, add the import alongside the existing
`from app.schemas import VisionRequest, WorldOp` (line 15):

```python
from app.schemas import VisionRequest, WorldOp
from app.world_schema import ENTITY_SCHEMAS, parse_key
```

Then insert these two new functions immediately after `_is_numeric`
(currently lines 38-39), **before** `check_op_consistency`:

```python
def _validate_field_value(field: str, spec: dict, value) -> str | None:
    """Return an error message if value violates the field spec, else None."""
    field_type = spec["type"]

    if field_type in ("number", "integer"):
        if not _is_numeric(value):
            return f"'{field}' must be a number, got {value!r}"
        if field_type == "integer" and float(value) != int(value):
            return f"'{field}' must be an integer, got {value!r}"
        if "min" in spec and value < spec["min"]:
            return f"value {value} for '{field}' below minimum {spec['min']}"
        if "max" in spec and value > spec["max"]:
            return f"value {value} for '{field}' above maximum {spec['max']}"
        return None

    if field_type == "string":
        if not isinstance(value, str):
            return f"'{field}' must be a string, got {value!r}"
        return None

    if field_type == "enum":
        if value not in spec["values"]:
            return f"'{field}' must be one of {spec['values']}, got {value!r}"
        return None

    if field_type == "object":
        if not isinstance(value, dict):
            return f"'{field}' must be an object, got {value!r}"
        return None

    return None


def check_domain_consistency(op: WorldOp, current) -> tuple[bool, str | None]:
    """Check an op against the domain key schema (app.world_schema).

    `current` is the already-deserialized current value of op.key (or
    None). `delete` is always allowed regardless of namespace.
    """
    if op.op == "delete":
        return True, None

    parts = parse_key(op.key)
    if parts is None:
        return False, f"unknown key namespace '{op.key}'"

    spec = ENTITY_SCHEMAS[parts.entity].get(parts.field)
    if spec is None:
        return False, f"unknown field '{parts.field}' for entity '{parts.entity}'"

    field_type = spec["type"]

    if op.op == "merge" and field_type != "object":
        return False, f"op 'merge' not allowed on field '{parts.field}' (type '{field_type}')"
    if op.op == "increment" and field_type not in ("number", "integer"):
        return False, f"op 'increment' not allowed on field '{parts.field}' (type '{field_type}')"

    if op.op == "increment":
        result = (current or 0) + op.value
    elif op.op == "merge":
        base = current if isinstance(current, dict) else {}
        result = {**base, **op.value}
    else:
        result = op.value

    error = _validate_field_value(parts.field, spec, result)
    if error:
        return False, error

    return True, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_domain_validation.py -v`
Expected: 16 passed

- [ ] **Step 5: Run full suite to check nothing else broke**

Run: `cd backend && .venv/bin/pytest -v`
Expected: all previously-passing tests still pass (new functions are not
yet called from `check_op_consistency`, so no behavior change yet)

- [ ] **Step 6: Commit**

```bash
git add backend/app/validation.py backend/tests/test_domain_validation.py
git commit -m "Add check_domain_consistency: schema-aware op validation"
```

---

### Task 3: Wire `check_domain_consistency` into `check_op_consistency`

**Files:**
- Modify: `backend/app/validation.py:42-72`
- Test: `backend/tests/test_check_op_consistency.py`

- [ ] **Step 1: Write the failing tests**

Save as `backend/tests/test_check_op_consistency.py`:

```python
import json
from unittest.mock import AsyncMock

import pytest

from app.schemas import WorldOp
from app.validation import check_op_consistency


@pytest.mark.asyncio
async def test_accepts_valid_domain_set_with_no_current_value():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    op = WorldOp(op="set", key="service.api.status", value="healthy")
    ok, msg = await check_op_consistency(pool, op)

    assert (ok, msg) == (True, None)


@pytest.mark.asyncio
async def test_rejects_unknown_namespace():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    op = WorldOp(op="set", key="world.status", value="building")
    ok, msg = await check_op_consistency(pool, op)

    assert ok is False
    assert msg == "unknown key namespace 'world.status'"


@pytest.mark.asyncio
async def test_rejects_increment_overflow_against_current_value():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"value": json.dumps(95)})

    op = WorldOp(op="increment", key="service.api.load", value=10)
    ok, msg = await check_op_consistency(pool, op)

    assert ok is False
    assert "above maximum 100" in msg


@pytest.mark.asyncio
async def test_generic_type_check_runs_before_domain_check():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"value": json.dumps("healthy")})

    op = WorldOp(op="increment", key="service.api.status", value=1)
    ok, msg = await check_op_consistency(pool, op)

    assert ok is False
    assert "is not numeric, cannot increment" in msg


@pytest.mark.asyncio
async def test_delete_always_allowed_regardless_of_namespace():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    op = WorldOp(op="delete", key="not.a.domain.key")
    ok, msg = await check_op_consistency(pool, op)

    assert (ok, msg) == (True, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_check_op_consistency.py -v`
Expected: `test_accepts_valid_domain_set_with_no_current_value`,
`test_rejects_unknown_namespace`, and
`test_rejects_increment_overflow_against_current_value` FAIL (current
`check_op_consistency` returns `(True, None)` for any `set`/`increment`
that passes the generic checks, regardless of key namespace). The other
two already pass.

- [ ] **Step 3: Write the implementation**

Replace `check_op_consistency` (lines 42-72 of
`backend/app/validation.py`) with:

```python
async def check_op_consistency(pool: asyncpg.Pool, op: WorldOp) -> tuple[bool, str | None]:
    """Check a single op against the current world_state for type consistency."""
    row = await pool.fetchrow("SELECT value FROM world_state WHERE key = $1", op.key)
    current = row["value"] if row else None
    if current is not None:
        current = json.loads(current) if isinstance(current, str) else current

    if op.op == "delete":
        return True, None

    if op.op == "increment":
        if not _is_numeric(op.value):
            return False, f"increment on '{op.key}' requires a numeric value"
        if current is not None and not _is_numeric(current):
            return False, f"key '{op.key}' is not numeric, cannot increment"
    elif op.op == "merge":
        if not isinstance(op.value, dict):
            return False, f"merge on '{op.key}' requires an object value"
        if current is not None and not isinstance(current, dict):
            return False, f"key '{op.key}' is not an object, cannot merge"
    elif op.op == "set":
        if op.value is None:
            return False, f"set on '{op.key}' requires a non-null value"
    else:
        return False, f"unknown op '{op.op}'"

    return check_domain_consistency(op, current)
```

This removes the now-unused inline `import json as _json` (the module
already imports `json` at the top) and the per-branch early `return True,
None` — every branch that passes its generic check now falls through to
`check_domain_consistency`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_check_op_consistency.py -v`
Expected: 5 passed

- [ ] **Step 5: Run full backend suite**

Run: `cd backend && .venv/bin/pytest -v`
Expected: all tests pass (26 pre-existing + new ones from Tasks 1-3)

- [ ] **Step 6: Commit**

```bash
git add backend/app/validation.py backend/tests/test_check_op_consistency.py
git commit -m "Enforce domain key schema in check_op_consistency"
```

---

### Task 4: Document the domain key schema in README

**Files:**
- Modify: `README.md`

This is the public contract external LLM agents read (also linked from
`/llms.txt`). Without this, every vision an agent submits with a
non-`region.*`/`service.*` key now gets hard-rejected with no
explanation in the docs.

- [ ] **Step 1: Add a "World state schema" section after the vision/op format example**

In `README.md`, after the line `` `op` is one of `set | merge | increment
| delete`. `` (line 82) and before the `---` separator (line 84), insert:

```markdown

### World state schema

`world_state` keys MUST follow `<entity>.<id>.<field>`, where `entity` is
one of:

| Entity | `id` | Fields |
|---|---|---|
| `region` | `^[a-z0-9_]{1,32}$` | `capacity_forecast` (number, >=0), `population` (integer, >=0), `status` (enum: `stable`\|`growing`\|`declining`\|`critical`), `notes` (object) |
| `service` | `^[a-z0-9_]{1,32}$` | `status` (enum: `healthy`\|`degraded`\|`down`), `load` (number, 0-100), `version` (string), `capacity` (number, >=0) |

Any op on a key outside this schema (wrong shape, unknown entity/field,
wrong type, out-of-range value, or an `op` incompatible with the field's
type — e.g. `merge` on an enum field) is rejected as inconsistent.
`delete` is always allowed. `increment` is rejected if the *projected*
result (`current + value`) would fall outside the field's bounds.
```

- [ ] **Step 2: Update validation rule 4 to mention the schema**

Replace line 91:

```markdown
4. **Consistency** — each op is checked against the current `world_state` type (e.g. can't `increment` a non-numeric key).
```

with:

```markdown
4. **Consistency** — each op is checked against the current `world_state` type (e.g. can't `increment` a non-numeric key), and against the domain key schema above (namespace, field, type/enum, numeric bounds — see "World state schema").
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document world_state domain key schema in README"
```

---

## Notes

- `demo.counter` (written during earlier MCP testing) remains readable
  via `GET /api/v1/world/state` but can no longer be targeted by new ops
  — any op on it now fails namespace validation. This is intentional
  (see spec, "Legacy keys").
- Deploy: pushing to `main` triggers the webhook auto-deploy
  (`scripts/deploy_webhook.py`) which rebuilds/restarts the `api`
  container and runs smoke checks. No manual VPS steps needed for this
  change (no nginx/docker-compose changes).
