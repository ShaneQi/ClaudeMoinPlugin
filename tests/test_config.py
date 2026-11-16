import pytest

from moinmoin_mcp.access import AccessConfig, access_config
from moinmoin_mcp.client import Settings, endpoint, page_url


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (
        "MOIN_URL",
        "MOIN_USER",
        "MOIN_PASSWORD",
        "CF_ACCESS_TEAM_DOMAIN",
        "CF_ACCESS_AUD",
        "ALLOW_WRITES",
        "PORT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("MOIN_URL", "https://wiki.example/wiki")
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
        ("MOIN_USER", "missing-password", "both"),
        ("MOIN_PASSWORD", "missing-user", "both"),
        ("ALLOW_WRITES", "yes", "ALLOW_WRITES"),
        ("PORT", "0", "PORT"),
    ],
)
def test_invalid_configuration_fails_at_startup(monkeypatch, key, value, message):
    monkeypatch.setenv("MOIN_URL", "https://wiki.example/")
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


@pytest.mark.parametrize(
    ("base", "page", "version", "expected"),
    [
        ("https://wiki.example", "FrontPage", None, "https://wiki.example/FrontPage"),
        (
            "https://wiki.example/wiki/?action=xmlrpc2",
            "Projects/Road map",
            None,
            "https://wiki.example/wiki/Projects/Road%20map",
        ),
        (
            "https://wiki.example/wiki?lang=en&action=xmlrpc2&rev=99",
            "Caf\u00e9/#?100%",
            3,
            "https://wiki.example/wiki/Caf%C3%A9/%23%3F100%25?lang=en&action=recall&rev=3",
        ),
        (
            "https://wiki.example/wiki/",
            "//other.example/Page",
            None,
            "https://wiki.example/wiki///other.example/Page",
        ),
    ],
)
def test_page_urls_preserve_wiki_base_and_encode_page_names(base, page, version, expected):
    assert page_url(base, page, version) == expected


def test_access_configuration_is_optional_at_startup(monkeypatch):
    monkeypatch.setenv("MOIN_URL", "https://wiki.example/")
    assert Settings.from_env().access is None
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "myteam")
    monkeypatch.setenv("CF_ACCESS_AUD", "aud1")
    assert Settings.from_env().access == AccessConfig(
        "https://myteam.cloudflareaccess.com", ("aud1",)
    )


@pytest.mark.parametrize(
    ("team", "aud", "expected"),
    [
        (
            "myteam.cloudflareaccess.com",
            "a",
            AccessConfig("https://myteam.cloudflareaccess.com", ("a",)),
        ),
        (
            "https://myteam.cloudflareaccess.com/",
            " a , b ,",
            AccessConfig("https://myteam.cloudflareaccess.com", ("a", "b")),
        ),
        ("myteam", "a", AccessConfig("https://myteam.cloudflareaccess.com", ("a",))),
        ("", "a", None),
        ("myteam", " , ", None),
    ],
)
def test_access_config_normalization(team, aud, expected):
    assert access_config({"CF_ACCESS_TEAM_DOMAIN": team, "CF_ACCESS_AUD": aud}) == expected
