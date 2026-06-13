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


def test_set_alert_severity_valid():
    op = WorldOp(op="set", key="alert.a1.severity", value="critical")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_increment_on_alert_severity():
    op = WorldOp(op="increment", key="alert.a1.severity", value=1)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'increment' not allowed on field 'severity' (type 'enum')"


def test_set_research_title_valid():
    op = WorldOp(op="set", key="research.2506_01234.title", value="A Paper About SRE")
    assert check_domain_consistency(op, None) == (True, None)


def test_set_research_summary_valid():
    op = WorldOp(op="set", key="research.2506_01234.summary", value="An abstract.")
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_research_unknown_field():
    op = WorldOp(op="set", key="research.2506_01234.unknown_field", value="x")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown field 'unknown_field' for entity 'research'"


def test_rejects_merge_on_research_title():
    op = WorldOp(op="merge", key="research.2506_01234.title", value={"x": 1})
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'merge' not allowed on field 'title' (type 'string')"


def test_set_finding_title_valid():
    op = WorldOp(op="set", key="finding.2506_01234.title", value="A Paper About Agent Memory")
    assert check_domain_consistency(op, None) == (True, None)


def test_set_finding_relevance_score_valid():
    op = WorldOp(op="set", key="finding.2506_01234.relevance_score", value=0.82)
    assert check_domain_consistency(op, None) == (True, None)


def test_rejects_finding_relevance_score_above_max():
    op = WorldOp(op="set", key="finding.2506_01234.relevance_score", value=1.5)
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "value 1.5 for 'relevance_score' above maximum 1"


def test_rejects_finding_unknown_field():
    op = WorldOp(op="set", key="finding.2506_01234.unknown_field", value="x")
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "unknown field 'unknown_field' for entity 'finding'"


def test_rejects_merge_on_finding_title():
    op = WorldOp(op="merge", key="finding.2506_01234.title", value={"x": 1})
    ok, msg = check_domain_consistency(op, None)
    assert ok is False
    assert msg == "op 'merge' not allowed on field 'title' (type 'string')"


def test_merge_on_finding_notes_valid():
    op = WorldOp(op="merge", key="finding.2506_01234.notes", value={"insight": "use event sourcing for agent memory"})
    assert check_domain_consistency(op, None) == (True, None)
