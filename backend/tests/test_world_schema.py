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


def test_parse_key_valid_research():
    assert parse_key("research.2506_01234.title") == KeyParts("research", "2506_01234", "title")


def test_get_field_spec_research_title():
    assert get_field_spec("research", "title") == {"type": "string"}


def test_get_field_spec_research_fetched_at():
    assert get_field_spec("research", "fetched_at") == {"type": "string"}


def test_parse_key_valid_finding():
    assert parse_key("finding.2506_01234.title") == KeyParts("finding", "2506_01234", "title")


def test_get_field_spec_finding_relevance_score():
    assert get_field_spec("finding", "relevance_score") == {"type": "number", "min": 0, "max": 1}


def test_get_field_spec_finding_notes():
    assert get_field_spec("finding", "notes") == {"type": "object"}


def test_parse_key_valid_vulnerability():
    assert parse_key("vulnerability.cve_2026_35273.severity") == KeyParts(
        "vulnerability", "cve_2026_35273", "severity"
    )


def test_get_field_spec_vulnerability_cve_id():
    assert get_field_spec("vulnerability", "cve_id") == {"type": "string"}


def test_get_field_spec_vulnerability_severity():
    assert get_field_spec("vulnerability", "severity") == {
        "type": "enum",
        "values": ["high", "critical"],
    }


def test_get_field_spec_vulnerability_affected_service():
    assert get_field_spec("vulnerability", "affected_service") == {"type": "string"}


def test_parse_key_valid_proposal():
    assert parse_key("proposal.2506_01234.status") == KeyParts(
        "proposal", "2506_01234", "status"
    )


def test_get_field_spec_proposal_title():
    assert get_field_spec("proposal", "title") == {"type": "string"}


def test_get_field_spec_proposal_relevance_score():
    assert get_field_spec("proposal", "relevance_score") == {
        "type": "number",
        "min": 0,
        "max": 1,
    }


def test_get_field_spec_proposal_status():
    assert get_field_spec("proposal", "status") == {
        "type": "enum",
        "values": ["proposed", "reviewed", "accepted", "rejected"],
    }


def test_get_field_spec_proposal_context():
    assert get_field_spec("proposal", "context") == {"type": "object"}


def test_parse_key_valid_page():
    assert parse_key("page.home.status") == KeyParts("page", "home", "status")


def test_get_field_spec_page_status():
    assert get_field_spec("page", "status") == {
        "type": "enum",
        "values": ["live", "draft", "planned"],
    }


def test_parse_key_valid_keyword():
    assert parse_key("keyword.wa_username.locale") == KeyParts(
        "keyword", "wa_username", "locale"
    )


def test_get_field_spec_keyword_priority():
    assert get_field_spec("keyword", "priority") == {
        "type": "enum",
        "values": ["high", "medium", "low"],
    }


def test_get_field_spec_keyword_search_intent():
    assert get_field_spec("keyword", "search_intent") == {
        "type": "enum",
        "values": ["informational", "transactional", "navigational"],
    }


def test_parse_key_valid_content_gap():
    assert parse_key("content_gap.producthunt.status") == KeyParts(
        "content_gap", "producthunt", "status"
    )


def test_get_field_spec_content_gap_effort():
    assert get_field_spec("content_gap", "effort") == {
        "type": "enum",
        "values": ["low", "medium", "high"],
    }


def test_parse_key_valid_backlink():
    assert parse_key("backlink.devto1.status") == KeyParts("backlink", "devto1", "status")


def test_get_field_spec_backlink_type():
    assert get_field_spec("backlink", "type") == {
        "type": "enum",
        "values": ["guest_post", "directory", "press", "organic"],
    }
