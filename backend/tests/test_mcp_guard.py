from app.mcp_guard import _client_ip_var, _extract_client_ip, get_client_ip


def test_extract_client_ip_from_x_forwarded_for():
    scope = {
        "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
        "client": ("10.0.0.1", 12345),
    }
    assert _extract_client_ip(scope) == "203.0.113.5"


def test_extract_client_ip_falls_back_to_scope_client():
    scope = {"headers": [], "client": ("198.51.100.7", 54321)}
    assert _extract_client_ip(scope) == "198.51.100.7"


def test_extract_client_ip_unknown_when_nothing_available():
    scope = {"headers": [], "client": None}
    assert _extract_client_ip(scope) == "unknown"


def test_get_client_ip_default_is_unknown():
    token = _client_ip_var.set("unknown")
    try:
        assert get_client_ip() == "unknown"
    finally:
        _client_ip_var.reset(token)


def test_get_client_ip_reads_contextvar():
    token = _client_ip_var.set("203.0.113.5")
    try:
        assert get_client_ip() == "203.0.113.5"
    finally:
        _client_ip_var.reset(token)
