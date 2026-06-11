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


def test_parse_key_valid_incident():
    assert parse_key("incident.inc1.severity") == KeyParts("incident", "inc1", "severity")


def test_get_field_spec_incident_severity():
    assert get_field_spec("incident", "severity") == {
        "type": "enum",
        "values": ["low", "medium", "high", "critical"],
    }


def test_parse_key_valid_deployment():
    assert parse_key("deployment.dep1.status") == KeyParts("deployment", "dep1", "status")


def test_get_field_spec_deployment_progress():
    assert get_field_spec("deployment", "progress") == {"type": "number", "min": 0, "max": 100}


def test_parse_key_valid_team():
    assert parse_key("team.sre.on_call") == KeyParts("team", "sre", "on_call")


def test_get_field_spec_team_headcount():
    assert get_field_spec("team", "headcount") == {"type": "integer", "min": 0}


def test_parse_key_valid_alert():
    assert parse_key("alert.a1.severity") == KeyParts("alert", "a1", "severity")


def test_get_field_spec_alert_status():
    assert get_field_spec("alert", "status") == {"type": "enum", "values": ["firing", "resolved"]}
