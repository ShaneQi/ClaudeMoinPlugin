"""Cloudflare Access JWT verification for every MCP request.

Cloudflare Access adds a signed RS256 JWT in ``Cf-Access-Jwt-Assertion`` to each request it
lets through. Verifying it here means a request that reaches the origin directly (Cloud Run
``run.app`` URL, VPS IP, ...) without passing through Access is refused.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

KEY_TTL_S = 60 * 60
MIN_REFETCH_S = 60
LEEWAY_S = 60
FETCH_TIMEOUT_S = 10
UNAUTHENTICATED_PATHS = frozenset({"/health", "/healthz"})


@dataclass(frozen=True)
class AccessConfig:
    issuer: str
    audiences: tuple[str, ...]


def access_config(env: Mapping[str, str]) -> AccessConfig | None:
    """Build the Access configuration, or None if either variable is missing."""
    team = env.get("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/")
    audiences = tuple(a.strip() for a in env.get("CF_ACCESS_AUD", "").split(",") if a.strip())
    if not team or not audiences:
        return None
    if not re.match(r"^https?://", team):
        team = f"https://{team if '.' in team else f'{team}.cloudflareaccess.com'}"
    return AccessConfig(team, audiences)


@dataclass
class _KeySet:
    keys: dict[str, Any]
    fetched_at: float


@dataclass
class _Cache:
    sets: dict[str, _KeySet] = field(default_factory=dict)
    locks: dict[str, anyio.Lock] = field(default_factory=dict)


_cache = _Cache()


def _import_keys(jwks: Any) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    for jwk in (jwks or {}).get("keys", []) if isinstance(jwks, dict) else []:
        if not isinstance(jwk, dict) or jwk.get("kty") != "RSA" or not jwk.get("kid"):
            continue
        try:
            keys[jwk["kid"]] = RSAAlgorithm.from_jwk({"kty": "RSA", "n": jwk["n"], "e": jwk["e"]})
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping unusable Cloudflare Access key %s: %s", jwk.get("kid"), exc)
    return keys


def set_access_keys(issuer: str, jwks: dict[str, Any]) -> None:
    """Install a key set without fetching it (for tests)."""
    _cache.sets[issuer] = _KeySet(_import_keys(jwks), time.monotonic())


def clear_access_keys() -> None:
    _cache.sets.clear()
    _cache.locks.clear()


async def _fetch_keys(issuer: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
        response = await client.get(f"{issuer}/cdn-cgi/access/certs")
        response.raise_for_status()
        return _import_keys(response.json())


async def _key_for(issuer: str, kid: str) -> Any:
    def needs_fetch(entry: _KeySet | None, now: float) -> bool:
        if entry is None or now - entry.fetched_at > KEY_TTL_S:
            return True
        return kid not in entry.keys and now - entry.fetched_at > MIN_REFETCH_S

    entry = _cache.sets.get(issuer)
    if needs_fetch(entry, time.monotonic()):
        lock = _cache.locks.setdefault(issuer, anyio.Lock())
        async with lock:
            # Another request may have refreshed the keys while we waited.
            entry = _cache.sets.get(issuer)
            now = time.monotonic()
            if needs_fetch(entry, now):
                try:
                    entry = _KeySet(await _fetch_keys(issuer), now)
                    _cache.sets[issuer] = entry
                except (httpx.HTTPError, ValueError) as exc:
                    if entry is None:
                        raise ValueError(
                            f"could not fetch Cloudflare Access keys from {issuer}: {exc}"
                        ) from exc
                    logger.warning(
                        "Cloudflare Access key refresh failed, using cached keys: %s", exc
                    )
    assert entry is not None
    return entry.keys.get(kid)


async def verify_access_jwt(token: str, config: AccessConfig) -> dict[str, Any]:
    """Return the verified claims, or raise ValueError/jwt.InvalidTokenError."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"malformed JWT: {exc}") from exc
    if header.get("alg") != "RS256":
        raise ValueError(f"unexpected alg {header.get('alg')!r}")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise ValueError("missing kid")
    key = await _key_for(config.issuer, kid)
    if key is None:
        raise ValueError("unknown signing key")
    claims: dict[str, Any] = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=list(config.audiences),
        issuer=config.issuer,
        leeway=LEEWAY_S,
        options={"require": ["exp", "iss", "aud"]},
    )
    return claims


def access_token(request: Request) -> str | None:
    header = request.headers.get("cf-access-jwt-assertion", "").strip()
    if header:
        return header
    match = re.fullmatch(r"Bearer\s+(\S+)", request.headers.get("authorization", "").strip(), re.I)
    return match[1] if match else None


class CloudflareAccessAuth:
    """ASGI middleware requiring a valid Cloudflare Access JWT on every non-health request."""

    def __init__(self, app: ASGIApp, config: AccessConfig | None) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in UNAUTHENTICATED_PATHS:
            await self.app(scope, receive, send)
            return
        if self.config is None:
            response = PlainTextResponse(
                "Server is missing its Cloudflare Access settings "
                "(CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD).",
                status_code=500,
            )
            await response(scope, receive, send)
            return
        token = access_token(Request(scope))
        reason = "no Cloudflare Access token"
        if token:
            try:
                await verify_access_jwt(token, self.config)
            except (ValueError, jwt.InvalidTokenError) as exc:
                reason = str(exc) or type(exc).__name__
            else:
                await self.app(scope, receive, send)
                return
        logger.warning("access denied: %s (%s)", reason, scope["path"])
        response = PlainTextResponse(
            "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
        await response(scope, receive, send)
