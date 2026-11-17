import asyncio
import base64
import socket
import threading
import time
import xml.parsers.expat
from dataclasses import replace

import httpx
import pytest
import uvicorn
from access_keys import ISSUER, OTHER_KEY, public_jwks, sign
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from starlette.testclient import TestClient

from moinmoin_mcp import access
from moinmoin_mcp.client import Moin
from moinmoin_mcp.server import create_app

AUTH = {"Authorization": f"Bearer {sign()}"}


@pytest.fixture
def client(wiki):
    with TestClient(create_app(wiki.settings), base_url="http://localhost") as client:
        yield client


def call(client, name, arguments=None):
    response = client.post(
        "/mcp",
        headers={**AUTH, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    assert response.status_code == 200, response.text
    return CallToolResult.model_validate(response.json()["result"])


def text(result):
    return "\n".join(item.text for item in result.content if item.type == "text")


def mcp_status(client, headers):
    return client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream", **headers},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )


def tamper(token):
    header, payload, signature = token.split(".")
    other = sign(sub="attacker").split(".")[1]
    return ".".join((header, other, signature))


@pytest.mark.parametrize(
    "headers",
    [
        {"Cf-Access-Jwt-Assertion": sign()},
        {"Authorization": f"Bearer {sign()}"},
        {"Authorization": f"bearer {sign(aud='test-aud-tag')}"},
        {"Cf-Access-Jwt-Assertion": sign(nbf=int(time.time()) + 30)},  # within leeway
    ],
)
def test_valid_access_tokens_are_accepted(client, headers):
    response = mcp_status(client, headers)
    assert response.status_code == 200, response.text


def test_access_header_takes_precedence_over_authorization(client):
    headers = {"Cf-Access-Jwt-Assertion": sign(), "Authorization": "Bearer junk"}
    assert mcp_status(client, headers).status_code == 200
    headers = {"Cf-Access-Jwt-Assertion": "junk", "Authorization": f"Bearer {sign()}"}
    assert mcp_status(client, headers).status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(sign(exp=int(time.time()) - 120), id="expired"),
        pytest.param(sign(exp=None), id="no-exp"),
        pytest.param(sign(nbf=int(time.time()) + 600), id="nbf-future"),
        pytest.param(sign(aud=["other-aud"]), id="wrong-aud"),
        pytest.param(sign(iss="https://evil.cloudflareaccess.com"), id="wrong-iss"),
        pytest.param(sign(kid="k2"), id="unknown-kid"),
        pytest.param(sign(key=OTHER_KEY), id="other-key"),
        pytest.param(tamper(sign()), id="tampered"),
        pytest.param(sign(alg="HS256", key="secret" * 6), id="hs256"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
@pytest.mark.parametrize("via", ["header", "bearer"])
def test_invalid_access_tokens_are_rejected(client, wiki, token, via, caplog):
    headers = (
        {"Cf-Access-Jwt-Assertion": token}
        if via == "header"
        else {"Authorization": f"Bearer {token}"}
    )
    response = mcp_status(client, headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.text == "Unauthorized"
    assert "access denied: " in caplog.text
    assert not wiki.calls


@pytest.mark.parametrize("header", ["", "Bearer", "Basic abc", "Bearer \u00e9"])
def test_missing_token_and_health(client, wiki, header):
    assert client.get("/health").text == "ok"
    assert client.get("/healthz").text == "ok"
    # ASGI accepts UTF-8 header bytes even though httpx disallows non-ASCII strings.
    response = client.post("/mcp", headers={b"authorization": header.encode()})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert not wiki.calls


def test_missing_access_configuration_is_a_server_error(wiki):
    with TestClient(
        create_app(replace(wiki.settings, access=None)), base_url="http://localhost"
    ) as client:
        assert client.get("/health").text == "ok"
        response = mcp_status(client, AUTH)
        assert response.status_code == 500
        assert "CF_ACCESS_TEAM_DOMAIN" in response.text
    assert not wiki.calls


async def test_keys_are_fetched_and_refreshed_on_rotation(monkeypatch):
    fetches = []
    jwks = {"current": public_jwks()}

    async def fake_fetch(issuer):
        fetches.append(issuer)
        if jwks["current"] is None:
            raise httpx.ConnectError("down")
        return access._import_keys(jwks["current"])

    clock = {"now": 1000.0}
    monkeypatch.setattr(access, "_fetch_keys", fake_fetch)
    monkeypatch.setattr(access.time, "monotonic", lambda: clock["now"])
    access.clear_access_keys()
    config = access.AccessConfig(ISSUER, ("test-aud-tag",))

    await access.verify_access_jwt(sign(), config)
    await access.verify_access_jwt(sign(), config)
    assert fetches == [ISSUER]  # cached

    # A new kid within a minute of the last fetch is not re-fetched.
    jwks["current"] = public_jwks(OTHER_KEY, kid="k2")
    with pytest.raises(ValueError, match="unknown signing key"):
        await access.verify_access_jwt(sign(key=OTHER_KEY, kid="k2"), config)
    assert len(fetches) == 1
    clock["now"] += 61
    await access.verify_access_jwt(sign(key=OTHER_KEY, kid="k2"), config)
    assert len(fetches) == 2

    # A failed refresh keeps using the cached keys.
    jwks["current"] = None
    clock["now"] += 3601
    await access.verify_access_jwt(sign(key=OTHER_KEY, kid="k2"), config)
    assert len(fetches) == 3

    # Without cached keys, a failed fetch is an error.
    access.clear_access_keys()
    with pytest.raises(ValueError, match="could not fetch"):
        await access.verify_access_jwt(sign(), config)


def test_remote_hosts_and_origins_require_only_access_auth(client):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Host": "mcp.example.com",
        "Origin": "https://client.example.com",
    }
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "check_connection", "arguments": {}},
    }
    assert client.post("/mcp", headers=headers, json=request).status_code == 401
    response = client.post("/mcp", headers={**headers, **AUTH}, json=request)
    assert response.status_code == 200
    assert not response.json()["result"].get("isError", False)


def test_read_tools_use_authenticated_xmlrpc(client, wiki):
    result = call(client, "check_connection")
    assert not result.isError
    assert result.structuredContent == {
        "rpc_version": 2,
        "wiki": ["TestWiki", "wiki-id"],
        "authenticated": True,
        "writes_enabled": False,
    }
    page = call(client, "get_page", {"page": "FrontPage", "version": 2})
    assert page.structuredContent == {
        "page": "FrontPage",
        "url": wiki.url + "/FrontPage?action=recall&rev=2",
        "text": "= Wiki =",
    }
    html = call(client, "get_page_html", {"page": "FrontPage"})
    assert html.structuredContent == {
        "page": "FrontPage",
        "url": wiki.url + "/FrontPage",
        "html": "<h1>Wiki</h1>",
    }
    info = call(client, "get_page_info", {"page": "FrontPage", "version": 1})
    assert info.structuredContent["version"] == 1
    assert info.structuredContent["lastModified"] == "2026-11-14T12:00:00"
    assert info.structuredContent["url"] == wiki.url + "/FrontPage?action=recall&rev=1"
    pages = call(client, "list_pages", {"prefix": "A"})
    assert pages.structuredContent == {
        "result": [{"page": "Alpha", "url": wiki.url + "/Alpha"}],
    }
    search = call(client, "search_pages", {"query": "wiki", "titles": True, "limit": 1})
    assert search.structuredContent == {
        "result": [
            {
                "page": "FrontPage",
                "context": "= Wiki =",
                "url": "https://wiki.example/FrontPage",
            }
        ],
    }
    recent = call(client, "recent_changes", {"days": 0.5})
    assert recent.structuredContent["result"][0]["url"] == wiki.url + "/FrontPage"
    links = call(client, "list_links", {"page": "FrontPage"})
    assert links.structuredContent["result"][0]["url"] == wiki.url + "/Alpha"
    diff = call(client, "get_diff", {"page": "FrontPage", "from_revision": 1})
    assert base64.b64decode(diff.structuredContent["diff_base64"]) == b"\x00diff"
    assert diff.structuredContent["url"] == wiki.url + "/FrontPage"
    assert ("getPageVersion", ["FrontPage", 2]) in wiki.calls
    assert ("searchPagesEx", ["wiki", "title", 120, False, False, False]) in wiki.calls
    methods = [method for method, _ in wiki.calls]
    assert methods.count("getAuthToken") == methods.count("applyAuthToken")
    assert methods.count("getAuthToken") > 0


@pytest.mark.parametrize(
    ("row", "expected_url"),
    [
        (["Projects/Road map", "context"], None),
        (["Projects/Road map", "context", ""], None),
        (
            ["Projects/Road map", "context", "https://public.example/Road%20map"],
            "https://public.example/Road%20map",
        ),
    ],
)
def test_search_urls_use_wiki_url_or_encoded_fallback(client, wiki, monkeypatch, row, expected_url):
    monkeypatch.setattr(Moin, "call", lambda *args: [row])
    result = call(client, "search_pages", {"query": "Road"})
    assert result.structuredContent == {
        "result": [
            {
                "page": "Projects/Road map",
                "context": "context",
                "url": expected_url or wiki.url + "/Projects/Road%20map",
            }
        ],
    }


def test_external_link_urls_are_not_prefixed_with_wiki_url(client, monkeypatch):
    monkeypatch.setattr(
        Moin,
        "call",
        lambda *args: [{"name": "https://external.example/page", "type": 1}],
    )
    result = call(client, "list_links", {"page": "FrontPage"})
    assert result.structuredContent["result"][0]["url"] == "https://external.example/page"


def test_page_url_is_included_in_text_content_for_agents(client, wiki):
    page = "Projects/Road map #1"
    wiki.pages[page] = "Content"
    result = call(client, "get_page", {"page": page})
    assert result.structuredContent["url"] == wiki.url + "/Projects/Road%20map%20%231"
    assert wiki.url + "/Projects/Road%20map%20%231" in text(result)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("put_page", {"page": "FrontPage", "text": "replacement"}),
        ("rename_page", {"page": "FrontPage", "new_name": "Renamed"}),
        ("revert_page", {"page": "FrontPage", "revision": 1}),
        ("put_attachment", {"page": "FrontPage", "name": "a", "content_base64": "YQ=="}),
        ("delete_attachment", {"page": "FrontPage", "name": "a"}),
    ],
)
def test_every_write_is_blocked_by_default(client, wiki, name, arguments):
    result = call(client, name, arguments)
    assert result.isError
    assert "WRITES_DISABLED" in text(result)
    assert not wiki.calls


def test_writes_and_binary_attachments_round_trip(wiki):
    with TestClient(
        create_app(replace(wiki.settings, allow_writes=True)), base_url="http://localhost"
    ) as client:
        created = call(client, "put_page", {"page": "FrontPage", "text": "#format wiki\nNew"})
        assert created.structuredContent == {
            "ok": True,
            "page": "FrontPage",
            "url": wiki.url + "/FrontPage",
        }
        assert call(client, "get_page", {"page": "FrontPage"}).structuredContent["text"] == (
            "#format wiki\nNew"
        )
        data = base64.b64encode(b"\x00\xffattachment").decode()
        args = {"page": "FrontPage", "name": "data.bin"}
        uploaded = call(client, "put_attachment", {**args, "content_base64": data})
        assert uploaded.structuredContent["url"] == wiki.url + "/FrontPage"
        downloaded = call(client, "get_attachment", args)
        assert downloaded.structuredContent["content_base64"] == data
        assert downloaded.structuredContent["url"] == wiki.url + "/FrontPage"
        assert call(client, "list_attachments", {"page": "FrontPage"}).structuredContent == {
            "page": "FrontPage",
            "url": wiki.url + "/FrontPage",
            "attachments": ["data.bin"],
        }
        deleted = call(client, "delete_attachment", args)
        assert deleted.structuredContent["url"] == wiki.url + "/FrontPage"
        assert not wiki.attachments
        renamed = call(client, "rename_page", {"page": "FrontPage", "new_name": "NewPage"})
        assert renamed.structuredContent == {
            "ok": True,
            "page": "NewPage",
            "url": wiki.url + "/NewPage",
        }
        assert "FrontPage" not in wiki.pages
        reverted = call(client, "revert_page", {"page": "NewPage", "revision": 1})
        assert reverted.structuredContent["url"] == wiki.url + "/NewPage"
        assert wiki.pages["NewPage"] == "reverted"
        before = len(wiki.calls)
        invalid = call(client, "put_attachment", {**args, "content_base64": "%invalid"})
        assert invalid.isError and "INVALID_ATTACHMENT" in text(invalid)
        assert len(wiki.calls) == before


@pytest.mark.parametrize("page", ["Missing", "FaultDict"])
def test_wiki_faults_are_tool_errors(client, page):
    result = call(client, "get_page", {"page": page})
    assert result.isError and "FAULT 404" in text(result)


def test_invalid_wiki_response_is_an_error(client, wiki):
    wiki.pages["FrontPage"] = 42
    result = call(client, "get_page", {"page": "FrontPage"})
    assert result.isError and "INVALID_RESPONSE" in text(result)


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (OSError("internal connection details"), "NETWORK"),
        (xml.parsers.expat.ExpatError("HTML instead of XML"), "NOT_XMLRPC"),
    ],
)
def test_transport_errors_are_explicit(client, monkeypatch, exception, message):
    def fail(*args):
        raise exception

    monkeypatch.setattr(Moin, "call", fail)
    result = call(client, "check_connection")
    assert result.isError and message in text(result)
    assert str(exception) not in text(result)


def test_bad_credentials_and_rejected_tokens(wiki):
    with TestClient(
        create_app(replace(wiki.settings, password="wrong")), base_url="http://localhost"
    ) as client:
        result = call(client, "check_connection")
        assert result.isError and "AUTH_FAILED" in text(result)
        assert "wiki-password" not in text(result)
    wiki.reject_token = True
    with TestClient(create_app(wiki.settings), base_url="http://localhost") as client:
        result = call(client, "check_connection")
        assert result.isError and "Invalid token" in text(result)


def test_anonymous_wiki_and_failed_writes(wiki):
    wiki.anonymous = True
    wiki.fail_write = True
    with TestClient(
        create_app(replace(wiki.settings, username="", password="", allow_writes=True)),
        base_url="http://localhost",
    ) as client:
        assert not call(client, "get_page", {"page": "FrontPage"}).isError
        result = call(client, "put_page", {"page": "FrontPage", "text": "new"})
        assert result.isError and "WRITE_FAILED" in text(result)
    assert not any(method == "getAuthToken" for method, _ in wiki.calls)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_page", {"page": "FrontPage", "version": 0}),
        ("search_pages", {"query": "wiki", "limit": -1}),
        ("recent_changes", {"days": -1}),
        ("recent_changes", {"days": 10000}),
    ],
)
def test_invalid_arguments_never_reach_wiki(client, wiki, name, arguments):
    assert call(client, name, arguments).isError
    assert not wiki.calls


async def test_real_mcp_client_over_http(wiki):
    app = create_app(wiki.settings)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert server.started
            async with httpx.AsyncClient(headers=AUTH) as http:
                assert (await http.get(url + "/health")).text == "ok"
                async with streamable_http_client(url + "/mcp", http_client=http) as streams:
                    read, write, session_id = streams
                    async with ClientSession(read, write) as session:
                        init = await session.initialize()
                        assert init.serverInfo.name == "MoinMoin"
                        tools = (await session.list_tools()).tools
                        assert any(tool.name == "get_page" for tool in tools)
                        assert not any(tool.name == "raw" for tool in tools)
                        assert next(
                            tool for tool in tools if tool.name == "put_page"
                        ).annotations.destructiveHint
                        result = await session.call_tool("get_page", {"page": "FrontPage"})
                        assert not result.isError
                        assert result.structuredContent == {
                            "page": "FrontPage",
                            "url": wiki.url + "/FrontPage",
                            "text": "= Wiki =",
                        }
                        assert session_id() is None
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
