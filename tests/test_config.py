import pytest

from moinmoin_mcp.client import Settings, endpoint


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (
        "MOIN_URL",
        "MOIN_USER",
        "MOIN_PASSWORD",
        "ACCESS_TOKEN",
        "ALLOW_WRITES",
        "PORT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("MOIN_URL", "https://wiki.example/wiki")
    monkeypatch.setenv("ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("PORT", "8080")
    settings = Settings.from_env()
    assert settings.port == 8080
    assert not settings.allow_writes
    assert settings.username == settings.password == ""
    monkeypatch.setenv("MOIN_USER", "user")
    monkeypatch.setenv("MOIN_PASSWORD", "password")
    monkeypatch.setenv("ALLOW_WRITES", "true")
    assert Settings.from_env().allow_writes


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("MOIN_URL", "", "MOIN_URL"),
        ("MOIN_URL", "file:///tmp/wiki", "MOIN_URL"),
        ("MOIN_URL", "https://user:password@wiki.example/", "MOIN_URL"),
        ("ACCESS_TOKEN", "", "ACCESS_TOKEN"),
        ("MOIN_USER", "missing-password", "both"),
        ("MOIN_PASSWORD", "missing-user", "both"),
        ("ALLOW_WRITES", "yes", "ALLOW_WRITES"),
        ("PORT", "0", "PORT"),
    ],
)
def test_invalid_configuration_fails_at_startup(monkeypatch, key, value, message):
    monkeypatch.setenv("MOIN_URL", "https://wiki.example/")
    monkeypatch.setenv("ACCESS_TOKEN", "test-token")
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message):
        Settings.from_env()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://wiki.example", "https://wiki.example/?action=xmlrpc2"),
        ("https://wiki.example/wiki", "https://wiki.example/wiki?action=xmlrpc2"),
        ("https://wiki.example/wiki/?action=xmlrpc2", "https://wiki.example/wiki/?action=xmlrpc2"),
        (
            "https://wiki.example/wiki?lang=en&action=show",
            "https://wiki.example/wiki?lang=en&action=xmlrpc2",
        ),
    ],
)
def test_xmlrpc_endpoint(url, expected):
    assert endpoint(url) == expected
