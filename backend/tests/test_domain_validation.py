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


def test_set_incident_severity_valid():
    op = WorldOp(op="set", key="incident.inc1.severity", value="high")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_invalid_incident_severity():
    op = WorldOp(op="set", key="incident.inc1.severity", value="exploding")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "'severity' must be one of ['low', 'medium', 'high', 'critical'], got 'exploding'"
