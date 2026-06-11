# World schema: incident, deployment, team, alert entities

## Context

`backend/app/world_schema.py` currently defines two entity types,
`region` and `service`, in `ENTITY_SCHEMAS`. The validation engine
(`check_domain_consistency`, `_validate_field_value`,
`parse_key`/`KEY_PATTERN`/`ID_PATTERN`) is fully generic over
`ENTITY_SCHEMAS` — it has no `region`/`service`-specific logic. As noted
in the previous design's "Out of scope": "Additional entity types beyond
`region`/`service`" was deferred to a future iteration. This is that
iteration.

This design adds four new entity types to the same infra/datacenter
domain: `incident`, `deployment`, `team`, `alert`. No engine changes —
purely new `ENTITY_SCHEMAS` entries plus tests and docs.

## New entity schemas

Added to `ENTITY_SCHEMAS` in `backend/app/world_schema.py`, following
the existing `FieldSpec` shape (`type`: `"number" | "integer" | "string"
| "enum" | "object"`, optional `min`/`max`/`values`):

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
```

Notes on choices:

- `affected_service`, `affected_region`, `target_service`,
  `source_service`: plain `"string"` fields. They look like references to
  `service.<id>`/`region.<id>` ids, but **no existence/format validation
  is performed against those entities** — same "out of scope" as the
  original design's cross-key dependencies. Any non-empty string passes.
- `team.on_call`: modeled as `enum ["active", "off"]` rather than adding
  a new `boolean` `FieldSpec` type, keeping the type system unchanged
  (`number`/`integer`/`string`/`enum`/`object` only).
- `progress` bounds (`[0, 100]`) mirror `service.load`'s existing
  `[0, 100]` pattern, including `increment` projection checks (an
  increment that would push `progress` above 100 is hard-rejected,
  same as `service.api.load`).

## Validation / integration

None. `parse_key`, `check_domain_consistency`, and
`_validate_field_value` already iterate `ENTITY_SCHEMAS` generically —
adding dict entries is sufficient for all four new entities to get full
namespace/field/op-type/bounds/enum validation, identical to
`region`/`service`.

## Testing

Extend `backend/tests/test_domain_validation.py` (and
`backend/tests/test_world_schema.py` for `parse_key`/`get_field_spec`)
with one representative case per new entity, mirroring the existing
`region`/`service` coverage:

- `test_world_schema.py`: `parse_key("incident.inc1.severity")`,
  `parse_key("deployment.dep1.status")`,
  `parse_key("team.sre.on_call")`, `parse_key("alert.a1.severity")` all
  resolve correctly; `get_field_spec` returns the right spec for one
  field per entity.
- `test_domain_validation.py`, per entity:
  - One valid `set` accepted (e.g. `set incident.inc1.severity =
    "high"`).
  - One enum rejection (e.g. `set deployment.dep1.status =
    "exploding"`).
  - One bounds rejection for the entity with numeric bounds
    (`deployment.dep1.progress = 150`, increment over 100).
  - One op/type-compatibility rejection (e.g. `increment
    incident.inc1.severity by 1` — `increment` not allowed on an
    `enum` field).
  - `team.headcount`: `increment` accepted (integer type), bounds
    (`min: 0`) rejection on negative result.

Existing 60/60 backend test suite must continue to pass unmodified.

## README update

Add the four new entity types to the schema table in `README.md`
(alongside the existing `region`/`service` rows), with one example op
each in the "Vision / op format" example section, e.g.:

```json
{ "op": "set", "key": "incident.inc1.severity", "value": "high" },
{ "op": "set", "key": "deployment.dep1.status", "value": "in_progress" }
```

## Out of scope

- Cross-entity existence/reference validation for `affected_service`,
  `target_service`, etc. (still no notion of entity existence beyond
  keys present in `world_state`).
- New `FieldSpec` types (e.g. `boolean`, `entity_ref`).
- Seeding `world_state` with example incident/deployment/team/alert
  data — schema-only change, agents populate via normal vision flow.
- Further entity types beyond these four.
