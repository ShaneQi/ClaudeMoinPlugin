"""Authenticated, stateless Streamable HTTP MCP server."""

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import logging
import urllib.error
import xml.parsers.expat
import xmlrpc.client as xc
from collections.abc import Callable
from typing import Annotated, Any

import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, TypeAdapter, ValidationError
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from moinmoin_mcp.client import Moin, Settings, WikiError, jsonable

logger = logging.getLogger(__name__)
Revision = Annotated[int, Field(ge=1)]


class BearerAuth:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.digest = hashlib.sha256(token.encode()).digest()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in ("/health", "/healthz"):
            header = Request(scope).headers.get("authorization", "")
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                hashlib.sha256(token.encode()).digest(), self.digest
            ):
                response = PlainTextResponse(
                    "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_server(settings: Settings) -> FastMCP:
    wiki = Moin(settings)
    mcp = FastMCP(
        "MoinMoin",
        instructions=(
            "Operate a MoinMoin 1.9 wiki. Read pages before editing; put_page replaces the "
            "entire page. Use MoinMoin markup, not Markdown, and preserve #format, #acl and "
            "#pragma lines. Check page metadata for concurrent edits and read back after "
            "writes. Confirm destructive operations with the user. Attachment content is base64."
        ),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
        ),
    )

    async def rpc[T](result_type: type[T], method: str, *params: Any) -> T:
        try:
            result = await anyio.to_thread.run_sync(lambda: wiki.call(method, *params))
            return TypeAdapter(result_type).validate_python(jsonable(result), strict=True)
        except ValidationError as exc:
            raise ToolError(f"INVALID_RESPONSE: unexpected result from {method}") from exc
        except WikiError as exc:
            raise ToolError(str(exc)) from exc
        except xc.Fault as exc:
            raise ToolError(f"FAULT {exc.faultCode}: {exc.faultString}") from exc
        except (xc.ProtocolError, urllib.error.HTTPError) as exc:
            code = exc.errcode if isinstance(exc, xc.ProtocolError) else exc.code
            raise ToolError(
                f"Wiki returned HTTP {code}; check MOIN_URL and XML-RPC access"
            ) from exc
        except (xc.ResponseError, xml.parsers.expat.ExpatError) as exc:
            raise ToolError("NOT_XMLRPC: check MOIN_URL and enable XML-RPC on the wiki") from exc
        except OSError as exc:
            logger.warning("Wiki network request failed (%s)", type(exc).__name__)
            raise ToolError(
                "NETWORK: could not reach the wiki; check network/proxy settings"
            ) from exc

    def tool(
        *, writes: bool = False, destructive: bool = False
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return mcp.tool(
            annotations=ToolAnnotations(
                readOnlyHint=not writes,
                destructiveHint=destructive,
                openWorldHint=True,
            )
        )

    def require_writes() -> None:
        if not settings.allow_writes:
            raise ToolError("WRITES_DISABLED: set ALLOW_WRITES=true on the server to enable writes")

    async def write(method: str, *params: Any) -> dict[str, Any]:
        require_writes()
        result = await rpc(bool, method, *params)
        if result is not True:
            raise ToolError(f"WRITE_FAILED: {method} did not confirm success")
        return {"ok": True}

    async def health(request: Request) -> Response:
        return PlainTextResponse("ok")

    mcp.custom_route("/health", methods=["GET"])(health)
    mcp.custom_route("/healthz", methods=["GET"])(health)

    @tool()
    async def check_connection() -> dict[str, Any]:
        """Check wiki connectivity and credentials, and report the server's write mode."""
        return {
            "rpc_version": await rpc(int, "getRPCVersionSupported"),
            "wiki": await rpc(list[str | None], "interwikiName"),
            "authenticated": bool(settings.username),
            "writes_enabled": settings.allow_writes,
        }

    @tool()
    async def get_page(page: str, version: Revision | None = None) -> str:
        """Read raw MoinMoin markup, optionally at a specific revision."""
        return (
            await rpc(str, "getPageVersion", page, version)
            if version
            else await rpc(str, "getPage", page)
        )

    @tool()
    async def get_page_html(page: str, version: Revision | None = None) -> str:
        """Read rendered HTML, optionally at a specific revision."""
        return (
            await rpc(str, "getPageHTMLVersion", page, version)
            if version
            else await rpc(str, "getPageHTML", page)
        )

    @tool()
    async def get_page_info(page: str, version: Revision | None = None) -> dict[str, Any]:
        """Get page author, lastModified and version; use before and after edits."""
        return (
            await rpc(dict[str, Any], "getPageInfoVersion", page, version)
            if version
            else await rpc(dict[str, Any], "getPageInfo", page)
        )

    @tool(writes=True, destructive=True)
    async def put_page(page: str, text: str) -> dict[str, Any]:
        """Create or replace an entire page. Read it first and preserve processing instructions."""
        return await write("putPage", page, text)

    @tool()
    async def list_pages(prefix: str | None = None, include_system: bool = False) -> list[str]:
        """List sorted wiki page names, optionally limited to a prefix."""
        options: dict[str, Any] = {"include_system": include_system}
        if prefix:
            options["prefix"] = prefix
        return sorted(await rpc(list[str], "getAllPagesEx", options))

    @tool()
    async def search_pages(
        query: str, titles: bool = False, limit: Annotated[int, Field(ge=1, le=500)] = 50
    ) -> list[dict[str, str]]:
        """Search page content or titles, returning matching names and context."""
        rows = await rpc(
            list[Annotated[list[str], Field(min_length=2)]],
            "searchPagesEx",
            query,
            "title" if titles else "text",
            120,
            False,
            False,
            False,
        )
        return [{"page": row[0], "context": row[1]} for row in rows[:limit]]

    @tool()
    async def recent_changes(
        days: Annotated[float, Field(gt=0, le=3650, allow_inf_nan=False)] = 7,
    ) -> list[dict[str, Any]]:
        """List changes since the given number of days ago (UTC)."""
        since = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=days)
        return await rpc(list[dict[str, Any]], "getRecentChanges", xc.DateTime(since))

    @tool()
    async def list_links(page: str) -> list[dict[str, Any]]:
        """List a page's outgoing links."""
        return await rpc(list[dict[str, Any]], "listLinks", page)

    @tool()
    async def get_diff(
        page: str, from_revision: Revision | None = None, to_revision: Revision | None = None
    ) -> dict[str, Any]:
        """Get a base64-encoded Moin binary diff; omitted revisions use the wiki's defaults."""
        result = await rpc(dict[str, Any], "getDiff", page, from_revision, to_revision)
        data = result.pop("diff", None)
        if not isinstance(data, bytes):
            raise ToolError("INVALID_RESPONSE: getDiff did not return a binary diff")
        return {**result, "diff_base64": base64.b64encode(data).decode("ascii")}

    @tool(writes=True, destructive=True)
    async def rename_page(page: str, new_name: str) -> dict[str, Any]:
        """Rename a page. Confirm this change with the user first."""
        return await write("renamePage", page, new_name)

    @tool(writes=True, destructive=True)
    async def revert_page(page: str, revision: Revision) -> dict[str, Any]:
        """Revert a page to a revision. Confirm this change with the user first."""
        return await write("revertPage", page, str(revision))

    @tool()
    async def list_attachments(page: str) -> list[str]:
        """List attachment filenames on a page."""
        return await rpc(list[str], "listAttachments", page)

    @tool()
    async def get_attachment(page: str, name: str) -> dict[str, str]:
        """Download an attachment as base64, without writing server-local files."""
        data = await rpc(bytes, "getAttachment", page, name)
        return {"name": name, "content_base64": base64.b64encode(data).decode("ascii")}

    @tool(writes=True, destructive=True)
    async def put_attachment(page: str, name: str, content_base64: str) -> dict[str, Any]:
        """Upload base64 attachment content. May overwrite an existing attachment."""
        require_writes()
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ToolError("INVALID_ATTACHMENT: content_base64 must be valid base64") from exc
        return await write("putAttachment", page, name, xc.Binary(data))

    @tool(writes=True, destructive=True)
    async def delete_attachment(page: str, name: str) -> dict[str, Any]:
        """Delete a page attachment. Confirm this change with the user first."""
        return await write("deleteAttachment", page, name)

    return mcp


def create_app(settings: Settings | None = None) -> ASGIApp:
    settings = settings or Settings.from_env()
    return BearerAuth(create_server(settings).streamable_http_app(), settings.access_token)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
