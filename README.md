# MoinMoin MCP

A Cloudflare Worker that lets Claude read and write a MoinMoin 1.9 wiki through
the wiki's XML-RPC interface (`?action=xmlrpc2`). It speaks MCP over stateless
Streamable HTTP at `POST /mcp` and has no dependencies.

Tools: `check_connection`, `get_page`, `get_page_html`, `get_page_info`,
`list_pages`, `search_pages`, `recent_changes`, `list_links`, `get_diff`,
`list_attachments`, `get_attachment`, and the write tools `put_page`,
`rename_page`, `revert_page`, `put_attachment`, `delete_attachment`. Write
tools return `WRITES_DISABLED` unless `ALLOW_WRITES=true`.

| File | |
|---|---|
| `src/index.js` | Routes, Cloudflare Access check, settings |
| `src/access.js` | Cloudflare Access JWT verification (same file as OFCloudMCP and LinkCliMCP) |
| `src/mcp.js` | MCP JSON-RPC handling |
| `src/tools.js` | Tool definitions, argument and result checks |
| `src/moin.js` | Wiki calls (per-call auth token + `system.multicall`), page URLs |
| `src/xmlrpc.js` | XML-RPC encoder/decoder |

## Configuration

Set these in the Cloudflare dashboard (Workers → `moinmoin-mcp` → Settings →
Variables and Secrets). `wrangler.toml` keeps them on every deploy
(`keep_bindings`), so a deploy only replaces the code.

| Name | Type | |
|---|---|---|
| `MOIN_URL` | variable | Wiki base URL, e.g. `https://wiki.example.com/` |
| `ALLOW_WRITES` | variable | `true` enables the write tools (default `false`) |
| `CF_ACCESS_TEAM_DOMAIN` | variable | `<team>.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | variable | The Access application's AUD tag (comma-separate several) |
| `MOIN_USER`, `MOIN_PASSWORD` | secrets | Wiki account; leave both unset for an anonymous wiki |

If the Access or wiki settings are missing or invalid, `/mcp` answers `500`
instead of opening up. `xmlrpc` must not be in the wiki's `actions_excluded`.

## Authentication

Put the Worker's hostname behind a Cloudflare Access application. The Worker
also verifies the Access JWT itself on every `/mcp` request, from
`Cf-Access-Jwt-Assertion` (or `Authorization: Bearer <jwt>`): RS256 against
`https://<team>/cdn-cgi/access/certs`, issuer, audience, and `exp`/`nbf` with
60 s leeway. A missing or invalid token gets `401`; the reason is only logged,
as `access denied: …`. So the `workers.dev` URL refuses requests that didn't
come through Access. `/health` and `/healthz` stay open.

## Deploy

Every push to `main` deploys with `wrangler deploy`
(`.github/workflows/deploy.yml`). It needs the repository secrets
`CLOUDFLARE_API_TOKEN` (the "Edit Cloudflare Workers" template) and
`CLOUDFLARE_ACCOUNT_ID`. Custom domains and routes are managed in the
dashboard.
