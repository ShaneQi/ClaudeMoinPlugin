"""Cloudflare Access-authenticated, stateless Streamable HTTP MCP server."""

import base64
import binascii
import datetime as dt
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
from starlette.types import ASGIApp

from moinmoin_mcp.access import CloudflareAccessAuth
from moinmoin_mcp.client import Moin, Settings, WikiError, jsonable, page_url

logger = logging.getLogger(__name__)
Revision = Annotated[int, Field(ge=1)]


def create_server(settings: Settings) -> FastMCP:
    wiki = Moin(settings)
    mcp = FastMCP(
        "MoinMoin",
        instructions=(
            "Operate a MoinMoin 1.9 wiki. Read pages before editing; put_page replaces the "
            "entire page. Use MoinMoin markup, not Markdown, and preserve #format, #acl and "
            "#pragma lines. Check page metadata for concurrent edits and read back after "
            "writes. Confirm destructive operations with the user. Attachment content is base64. "
            "Use the returned url fields to give the user links to relevant wiki pages."
        ),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
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

    def page_details(page: str, version: int | None = None) -> dict[str, str]:
        return {"page": page, "url": page_url(settings.url, page, version)}

    async def write(method: str, page: str, *params: Any) -> dict[str, Any]:
        require_writes()
        result = await rpc(bool, method, page, *params)
        if result is not True:
            raise ToolError(f"WRITE_FAILED: {method} did not confirm success")
        return {"ok": True, **page_details(page)}

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
    async def get_page(page: str, version: Revision | None = None) -> dict[str, str]:
        """Read raw MoinMoin markup, optionally at a specific revision."""
        text = (
            await rpc(str, "getPageVersion", page, version)
            if version
            else await rpc(str, "getPage", page)
        )
        return {**page_details(page, version), "text": text}

    @tool()
    async def get_page_html(page: str, version: Revision | None = None) -> dict[str, str]:
        """Read rendered HTML, optionally at a specific revision."""
        html = (
            await rpc(str, "getPageHTMLVersion", page, version)
            if version
            else await rpc(str, "getPageHTML", page)
        )
        return {**page_details(page, version), "html": html}

    @tool()
    async def get_page_info(page: str, version: Revision | None = None) -> dict[str, Any]:
        """Get page author, lastModified and version; use before and after edits."""
        info = (
            await rpc(dict[str, Any], "getPageInfoVersion", page, version)
            if version
            else await rpc(dict[str, Any], "getPageInfo", page)
        )
        return {**info, **page_details(page, version)}

    @tool(writes=True, destructive=True)
    async def put_page(page: str, text: str) -> dict[str, Any]:
        """Create or replace an entire page. Read it first and preserve processing instructions."""
        return await write("putPage", page, text)

    @tool()
    async def list_pages(
        prefix: str | None = None, include_system: bool = False
    ) -> list[dict[str, str]]:
        """List sorted wiki page names, optionally limited to a prefix."""
        options: dict[str, Any] = {"include_system": include_system}
        if prefix:
            options["prefix"] = prefix
        return [
            page_details(page) for page in sorted(await rpc(list[str], "getAllPagesEx", options))
        ]

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
        return [
            {
                **page_details(row[0]),
                "context": row[1],
                "url": row[2] if len(row) > 2 and row[2] else page_url(settings.url, row[0]),
            }
            for row in rows[:limit]
        ]

    @tool()
    async def recent_changes(
        days: Annotated[float, Field(gt=0, le=3650, allow_inf_nan=False)] = 7,
    ) -> list[dict[str, Any]]:
        """List changes since the given number of days ago (UTC)."""
        since = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=days)
        changes = await rpc(list[dict[str, Any]], "getRecentChanges", xc.DateTime(since))
        return [{**change, **page_details(change["name"])} for change in changes]

    @tool()
    async def list_links(page: str) -> list[dict[str, Any]]:
        """List a page's outgoing links."""
        links = await rpc(list[dict[str, Any]], "listLinks", page)
        return [
            {
                **link,
                "url": link["name"] if link["type"] == 1 else page_url(settings.url, link["name"]),
            }
            for link in links
        ]

    @tool()
    async def get_diff(
        page: str, from_revision: Revision | None = None, to_revision: Revision | None = None
    ) -> dict[str, Any]:
        """Get a base64-encoded Moin binary diff; omitted revisions use the wiki's defaults."""
        result = await rpc(dict[str, Any], "getDiff", page, from_revision, to_revision)
        data = result.pop("diff", None)
        if not isinstance(data, bytes):
            raise ToolError("INVALID_RESPONSE: getDiff did not return a binary diff")
        return {
            **result,
            **page_details(page, to_revision),
            "diff_base64": base64.b64encode(data).decode("ascii"),
        }

    @tool(writes=True, destructive=True)
    async def rename_page(page: str, new_name: str) -> dict[str, Any]:
        """Rename a page. Confirm this change with the user first."""
        result = await write("renamePage", page, new_name)
        return {**result, **page_details(new_name)}

    @tool(writes=True, destructive=True)
    async def revert_page(page: str, revision: Revision) -> dict[str, Any]:
        """Revert a page to a revision. Confirm this change with the user first."""
        return await write("revertPage", page, str(revision))

    @tool()
    async def list_attachments(page: str) -> dict[str, Any]:
        """List attachment filenames on a page."""
        attachments = await rpc(list[str], "listAttachments", page)
        return {**page_details(page), "attachments": attachments}

    @tool()
    async def get_attachment(page: str, name: str) -> dict[str, str]:
        """Download an attachment as base64, without writing server-local files."""
        data = await rpc(bytes, "getAttachment", page, name)
        return {
            **page_details(page),
            "name": name,
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

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
    return CloudflareAccessAuth(create_server(settings).streamable_http_app(), settings.access)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    if settings.access is None:
        logger.error(
            "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD are not set; every MCP request will fail "
            "with HTTP 500 until they are configured"
        )
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
