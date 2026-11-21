import pytest
from app.security.netguard import EndpointRejected, _is_blocked_ip, validate_custom_endpoint


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/v1",
        "https://10.0.0.5/v1",
        "https://user:pw@api.openai.com/v1",
        "https://api.openai.com/v1?x=1",
        "ftp://api.openai.com/v1",
        "https://evil.example/v1",
    ],
)
def test_rejects_bad_endpoints(url):
    with pytest.raises(EndpointRejected):
        validate_custom_endpoint(url, resolve=False)


def test_allowlist_from_settings(monkeypatch):
    monkeypatch.setenv("MT_ENDPOINT_ALLOWLIST", "llm.corp.example")
    from app.config import reset_settings_cache

    reset_settings_cache()
    t = validate_custom_endpoint("https://llm.corp.example/v1", resolve=False)
    assert t.host == "llm.corp.example" and t.path == "/v1"
    t2 = validate_custom_endpoint("https://sub.llm.corp.example:8443/api/v1/", resolve=False)
    assert t2.port == 8443 and t2.path == "/api/v1"


def test_blocked_ip_ranges():
    for ip in (
        "127.0.0.1",
        "10.1.2.3",
        "172.16.5.5",
        "192.168.0.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
        "fd00::1",
        "0.0.0.0",
        "100.64.0.1",
        "::ffff:127.0.0.1",
    ):
        assert _is_blocked_ip(ip), ip
    for ip in ("8.8.8.8", "2606:4700::1111"):
        assert not _is_blocked_ip(ip), ip


def test_dns_resolution_to_private_is_rejected(monkeypatch):
    monkeypatch.setenv("MT_ENDPOINT_ALLOWLIST", "rebind.example")
    from app.config import reset_settings_cache

    reset_settings_cache()
    import app.security.netguard as ng

    monkeypatch.setattr(ng, "resolve_all", lambda host, port: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(EndpointRejected):
        validate_custom_endpoint("https://rebind.example/v1")
    monkeypatch.setattr(ng, "resolve_all", lambda host, port: ["93.184.216.34"])
    t = validate_custom_endpoint("https://rebind.example/v1")
    assert t.ip == "93.184.216.34" and t.pinned_url == "https://93.184.216.34:443/v1"
