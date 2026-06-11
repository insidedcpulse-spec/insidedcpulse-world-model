# Domain key schema + rigid consistency validation

## Context

`world_state` is currently a free-form key-value store: any `WorldOp.key`
(string, 1-256 chars) is accepted as long as it passes the existing
*generic* type checks in `validation.check_op_consistency` (e.g. `merge`
needs an object, `increment` needs a numeric value/current). There is no
notion of a domain — agents can write to any key with any shape.

This design introduces the first real domain for InsideDCPulse: an
**infrastructure/project status world** (regions and services), matching
the example already used in `README.md`
(`region.eu.capacity_forecast`). It defines:

1. A fixed key namespace and field schema for this domain.
2. New rigid (deterministic, hard-fail) consistency rules layered on top
   of the existing generic checks, enforced in `check_op_consistency`.

This is the first domain schema. Future domains/entities can be added by
extending the same registry — out of scope for this design.

## Key namespace

All domain keys MUST have exactly 3 dot-separated segments:

```
<entity>.<id>.<field>
```

- `entity` — one of `region`, `service` (fixed set for this iteration)
- `id` — matches `^[a-z0-9_]{1,32}$`
- `field` — must be a field declared in that entity's schema (see below)

Any key that does not match this 3-segment pattern, or whose `entity` is
not in the registry, or whose `id` doesn't match the id pattern, is
**rejected** by `check_op_consistency` with a descriptive reason — it
never reaches `world_state`.

### Legacy keys

Pre-existing non-domain keys (e.g. `demo.counter`, written during MCP
testing) remain readable via `GET /api/v1/world/state` — `world_state`
rows are not deleted or migrated. However, any *new* op targeting
`demo.*` (or any other non-matching key) is rejected under these new
rules. This is intentional: the domain schema is enforced going forward
only.

## Field schema registry

New module `backend/app/world_schema.py`, a plain dict, no new
dependencies:

```python
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
```

`FieldSpec` is a `TypedDict`-like shape: `type` (`"number" | "integer" |
"string" | "enum" | "object"`), optional `min`/`max` (numeric types
only), optional `values` (enum type only).

A module-level helper `ID_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")` and
`KEY_PATTERN = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z0-9_]{1,32})\.([a-z][a-z0-9_]*)$")`
back the namespace check.

## Validation rules

New function `check_domain_consistency(op: WorldOp) -> tuple[bool, str |
None]` in `backend/app/validation.py`, called from
`check_op_consistency` *after* the existing generic checks (so existing
type-vs-current-value checks still run, then domain rules add stricter,
schema-aware checks). Any failure here is a **hard fail** — same
contract as the existing checks (no scoring/soft-fail tier for domain
violations in this iteration).

Order of checks for a given op:

1. **Namespace check** — `op.key` must match `KEY_PATTERN`; `entity`
   must be a key in `ENTITY_SCHEMAS`. Failure:
   `"unknown key namespace '<key>'"`.
2. **Field check** — `field` must be a key in
   `ENTITY_SCHEMAS[entity]`. Failure: `"unknown field '<field>' for
   entity '<entity>'"`.
3. **Op/type compatibility** — based on the field's `type`:
   - `merge` is only valid on `object` fields.
   - `increment` is only valid on `number`/`integer` fields.
   - `set` is valid on all field types (subject to check 4).
   - `delete` is always valid (no further checks).
   Failure: `"op '<op>' not allowed on field '<field>' (type
   '<type>')"`.
4. **Value/result validation** — compute the *resulting* value (mirrors
   `world_state.apply_op_to_value`) and validate it against the field
   spec:
   - `number`: must be `int`/`float` (not `bool`); if `min`/`max` set,
     resulting value must be within `[min, max]`.
   - `integer`: same as `number`, plus must have no fractional part.
   - `string`: must be `str`.
   - `enum`: must be one of `values`.
   - `object`: must be `dict` (already covered by generic merge check,
     but re-asserted for `set`).
   For `increment`, the resulting value is `(current or 0) + op.value`
   — bounds are checked against this *projected* value, so an increment
   that would push e.g. `service.api.load` above 100 is rejected before
   it happens (no partial/clamped writes).
   Failure messages are field/type-specific, e.g. `"value <x> for
   'service.api.load' out of range [0, 100]"` or `"'region.eu.status'
   must be one of ['stable', 'growing', 'declining', 'critical'], got
   '<x>'"`.

`check_domain_consistency` needs read access to the current value for
`increment` projection — `check_op_consistency` already fetches `current`
from `world_state`, so it's passed through (no extra query).

## Integration points

- `validation.check_op_consistency`: after existing checks pass, call
  `check_domain_consistency(op, current)`. This function is used by:
  - `validation.evaluate` (per-op consistency scoring/hard-fail for
    `/world/vision` and `/world/evaluate`)
  - `world_state.simulate_ops` (`/world/simulate`)
  - the worker's re-validation before `commit_ops`
  No call-site changes needed beyond the one shared function.
- `world_state.commit_ops` / `apply_op_to_value`: unchanged — by the time
  an op reaches commit it has already passed `check_op_consistency`
  (worker re-validates), so domain rules are already satisfied.

## Testing

New test module `backend/tests/test_domain_schema.py`:

- Valid ops accepted: `set region.eu.status = "stable"`, `set
  service.api.load = 42`, `merge region.eu.notes = {...}`, `increment
  service.api.load by 10` (within bounds).
- Namespace rejections: single-segment key, 4-segment key, unknown
  entity (`foo.bar.baz`), invalid id (`region.EU!.status`).
- Field rejections: `region.eu.unknown_field`.
- Type/enum rejections: `set region.eu.status = "exploding"`, `set
  service.api.load = "high"`.
- Bounds rejections: `set service.api.load = 150`, `increment
  service.api.load by 1000` (when current + delta > 100).
- Op/type compatibility rejections: `merge service.api.status = {...}`,
  `increment region.eu.notes by 1`.
- `delete` always accepted regardless of key (existing behavior,
  unchanged).

Existing `backend/tests/` suite (26 tests as of 2026-06-11) must continue
to pass unmodified — none of those tests currently exercise
`check_op_consistency`/`check_domain_consistency` against a real pool
(keys like `world.status`/`blob` only appear in fully-mocked
`test_mcp_server.py` cases where the validation/state layer is patched
out), so the new domain rules don't affect them.

## Out of scope

- Cross-key dependencies/invariants (e.g. "service.api must exist before
  service.api.load") — no notion of entity *existence* beyond the keys
  present in `world_state`; each field key is independent.
- Additional entity types beyond `region`/`service`.
- Migrating/removing legacy `demo.*` data.
- Soft scoring changes — domain violations are hard fails, same as
  existing inconsistency checks.
