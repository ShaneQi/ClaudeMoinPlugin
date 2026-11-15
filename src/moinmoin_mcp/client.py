"""MoinMoin XML-RPC v2 access, with per-call authentication and no shared sessions."""

from __future__ import annotations

import datetime as dt
import os
import urllib.request
import xmlrpc.client as xc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
    from _typeshed import SizedBuffer


@dataclass(frozen=True)
class Settings:
    url: str
    username: str
    password: str
    access_token: str
    allow_writes: bool = False
    port: int = 8787

    @classmethod
    def from_env(cls) -> Settings:
        url = os.environ.get("MOIN_URL", "").strip()
        endpoint(url)
        token = os.environ.get("ACCESS_TOKEN", "")
        if not token.strip():
            raise ValueError("ACCESS_TOKEN must be set to a long random bearer token")
        username = os.environ.get("MOIN_USER", "")
        password = os.environ.get("MOIN_PASSWORD", "")
        if bool(username) != bool(password):
            raise ValueError(
                "Set both MOIN_USER and MOIN_PASSWORD, or neither for anonymous access"
            )
        writes = os.environ.get("ALLOW_WRITES", "false").lower()
        if writes not in ("true", "false"):
            raise ValueError("ALLOW_WRITES must be true or false")
        port = int(os.environ.get("PORT", "8787"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return cls(url, username, password, token, writes == "true", port)


def endpoint(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("MOIN_URL must be an HTTP(S) wiki URL without credentials or a fragment")
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "action"]
    query.append(("action", "xmlrpc2"))
    return urlunsplit(parts._replace(path=parts.path or "/", query=urlencode(query)))


class UrllibTransport(xc.Transport):
    """Use urllib to honor proxy environment variables and bound network waits."""

    def __init__(self, scheme: str) -> None:
        super().__init__(use_builtin_types=True)
        self.scheme = scheme

    def request(
        self,
        host: str | tuple[str, dict[str, str]],
        handler: str,
        request_body: SizedBuffer,
        verbose: bool = False,
    ) -> Any:
        host, _, _ = self.get_host_info(host)
        request = urllib.request.Request(
            f"{self.scheme}://{host}{handler}",
            data=bytes(request_body),
            headers={"Content-Type": "text/xml", "User-Agent": "moinmoin-mcp/0.1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            self.verbose = verbose
            return self.parse_response(response)


class WikiError(Exception):
    """An explicit wiki operation failure suitable for an MCP tool error."""


class Moin:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = endpoint(settings.url)

    def call(self, method: str, *params: Any) -> Any:
        # A proxy and token per call avoid sharing mutable transport/auth state across threads.
        with xc.ServerProxy(
            self.url,
            transport=UrllibTransport(urlsplit(self.url).scheme),
            allow_none=True,
            use_builtin_types=True,
        ) as proxy:
            if self.settings.username:
                token = proxy.getAuthToken(self.settings.username, self.settings.password)
                if not token:
                    raise WikiError("AUTH_FAILED: wiki rejected the username or password")
                multicall = xc.MultiCall(proxy)
                multicall.applyAuthToken(token)
                getattr(multicall, method)(*params)
                results = multicall()
                if results[0] != "SUCCESS":
                    raise WikiError("AUTH_FAILED: wiki rejected the authentication token")
                result = results[1]
            else:
                result = getattr(proxy, method)(*params)
        if isinstance(result, dict) and "faultCode" in result and "faultString" in result:
            raise WikiError(f"FAULT {result['faultCode']}: {result['faultString']}")
        return result


def jsonable(value: Any) -> Any:
    if isinstance(value, xc.DateTime):
        return value.value
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value
