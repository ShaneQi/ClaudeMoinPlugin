# MoinMoin MCP server

A remote MCP server for MoinMoin 1.9 wikis over XML-RPC v2. Runs as a
non-root Python container on Google Cloud Run or behind an HTTPS reverse
proxy, behind [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/).
Wiki credentials stay on the server; clients authenticate to Cloudflare
Access, and the server verifies the Access JWT on every MCP request.

The deployment follows [OFCloudMCP](https://github.com/ShaneQi/OFCloudMCP):
stateless Streamable HTTP, multi-architecture images in GHCR and Google
Artifact Registry, and image-only Cloud Run updates on `main`.

## Local development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen
cp .env.example .env
chmod 600 .env
# Edit .env: set MOIN_URL, CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD and, for a
# private wiki, both credentials.
uv run --env-file .env moinmoin-mcp
```

The server listens on `0.0.0.0:$PORT` (default `8787`). Do not expose plain HTTP
to the internet; use Cloud Run's HTTPS URL or a TLS reverse proxy.

- `GET /health` and `/healthz`: unauthenticated liveness checks, return `ok`.
  These do not contact the wiki. Use `check_connection` to check wiki access.
- `/mcp`: Cloudflare Access-authenticated MCP Streamable HTTP with JSON responses, no sticky
  sessions or persistent disk required.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MOIN_URL` | required | Wiki base URL, e.g. `https://wiki.example.com/wiki/`; an existing `?action=xmlrpc2` endpoint also works |
| `MOIN_USER`, `MOIN_PASSWORD` | empty | Set both for a private wiki, or neither for anonymous wiki access |
| `CF_ACCESS_TEAM_DOMAIN` | required | Cloudflare Zero Trust team domain, e.g. `myteam.cloudflareaccess.com` (a full `https://` URL or just `myteam` also work). Find it in Zero Trust → Settings → Custom Pages |
| `CF_ACCESS_AUD` | required | The Access application's AUD tag (Zero Trust → Access → Applications → your app → Overview → "Application Audience (AUD) Tag"); comma-separate several to accept more than one application |
| `ALLOW_WRITES` | `false` | Set exactly `true` to enable wiki mutations |
| `PORT` | `8787` | HTTP port; Cloud Run supplies its own `PORT` |

Standard proxy environment variables are honored for outbound wiki requests.
Configure `SSL_CERT_FILE` if your wiki uses a private certificate authority.

Host and Origin headers are not restricted by the application; SDK DNS
rebinding protection is disabled. A valid Cloudflare Access JWT is required
regardless of the hostname.

## Authentication (Cloudflare Access)

Put the server behind a Cloudflare Access self-hosted application (via a
Cloudflare Tunnel or a proxied DNS record). For every request Access lets
through, Cloudflare adds a signed JWT in the `Cf-Access-Jwt-Assertion`
header. The server verifies that JWT itself on every MCP request, so requests
that reach the origin directly (the Cloud Run `run.app` URL, a VPS IP, ...)
without passing through Access are refused:

- The token is read from `Cf-Access-Jwt-Assertion`, or, if absent, from
  `Authorization: Bearer <jwt>` (same checks).
- The signature must be RS256 and match one of the team's public keys from
  `https://<team>/cdn-cgi/access/certs`, selected by `kid`. Keys are cached for
  an hour; an unknown `kid` triggers a re-fetch at most once a minute (key
  rotation). If a refresh fails, cached keys keep working.
- `iss` must equal `https://<CF_ACCESS_TEAM_DOMAIN>`, `aud` must contain one of
  `CF_ACCESS_AUD`, and `exp`/`nbf` are checked with 60 seconds of leeway.

A missing or invalid token gets `401` with `WWW-Authenticate: Bearer`; the
reason is only logged, as `access denied: …`. The server exits at startup if
`CF_ACCESS_TEAM_DOMAIN` or `CF_ACCESS_AUD` is unset. `/health` and `/healthz`
stay unauthenticated.

XML-RPC must be enabled on the wiki: remove `xmlrpc` from `actions_excluded`
in `wikiconfig.py`. The wiki must be reachable from the container, including
any necessary Cloud Run VPC egress/network configuration. Use HTTPS for a
private wiki; XML-RPC sends its credentials to the configured endpoint.

## MCP client setup

MCP clients cannot complete an interactive Access login, so create an Access
[service token](https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/)
(Zero Trust → Access → Service Auth → Service Tokens) and add a policy with
the **Service Auth** action for it to the Access application. Then use an MCP
client that supports HTTP request headers:

```json
{
  "mcpServers": {
    "moinmoin": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "CF-Access-Client-Id": "YOUR_CLIENT_ID.access",
        "CF-Access-Client-Secret": "YOUR_CLIENT_SECRET"
      }
    }
  }
}
```

This is the Claude Code HTTP configuration shape; other clients may use a
different key for the transport. `mcp.example.com` is the hostname protected
by Access; Access exchanges the service token for the JWT the server checks.
Configure the client secret through your client's secret storage rather than
committing it. Clients that only support OAuth connectors need an
authentication gateway.

Check connectivity with a valid MCP initialization request:

```sh
curl https://mcp.example.com/health
curl https://mcp.example.com/mcp \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1"}}}'
```

## Tools

| Tool | Purpose |
|---|---|
| `check_connection` | Wiki RPC version, interwiki identity `[name, id]`, authentication and write mode |
| `get_page`, `get_page_html`, `get_page_info` | Markup, HTML or metadata; optional `version` |
| `list_pages` | Sorted `{page, url}` entries; optional `prefix`, `include_system` |
| `search_pages` | Content/title search; `query`, optional `titles`, `limit` (1-500) |
| `recent_changes` | Changes in the last `days` (default 7, maximum 3650), UTC |
| `list_links` | Outgoing links |
| `get_diff` | Moin's compressed binary delta as `diff_base64`, with revision/conflict metadata; not a textual unified diff |
| `list_attachments`, `get_attachment` | `{page, url, attachments}` and `{page, url, name, content_base64}` |
| `put_page` | Replace/create a whole page with `page`, `text` |
| `rename_page`, `revert_page` | Rename (`page`, `new_name`) or revert (`page`, `revision`) |
| `put_attachment` | Upload `page`, `name`, `content_base64` |
| `delete_attachment` | Delete `page`, `name` |

Page-related results include a `url` agents can cite. `get_page` returns
`{page, url, text}` and `get_page_html` returns `{page, url, html}` rather than
plain strings. Metadata, diffs, recent changes and link entries also include
URLs; search results preserve the URL supplied by the wiki, falling back to
one built from `MOIN_URL`. Revision reads link to the requested revision.
Successful writes return `{ok, page, url}`; renames link to the new page name.
Attachment tools link to the containing page. Names with spaces, Unicode or
reserved URL characters are percent-encoded, preserving subpage slashes.
These structured output shapes replace the earlier plain-text/name-only
responses; clients should refresh their tool schemas after updating.

All five mutation tools return an explicit error without contacting the wiki
unless `ALLOW_WRITES=true`. Unlike OFCloudMCP, there is no write preview:
disabled writes fail rather than pretending to succeed. Wiki ACLs still
apply. No arbitrary XML-RPC tool is exposed, so it cannot bypass this gate.

Read before editing: `put_page` replaces the entire page. Preserve `#format`,
`#acl` and `#pragma` lines, check metadata for concurrent edits, confirm
destructive changes, and read back after writing. There is no atomic
compare-and-swap protection against simultaneous wiki edits. See the
[MoinMoin markup guide](docs/moin-syntax.md).

Attachment data is base64, not a local filesystem path. MCP requests are
limited to 4 MiB by the SDK (base64 overhead counts); use the wiki directly
for larger uploads.

## Containers / VPS

```sh
cp compose.example.yml compose.yml
docker compose -f compose.yml up -d --build
# Or use the published ghcr.io/shaneqi/moinmoinmcp:latest image.
```

Compose binds `127.0.0.1:8787`; expose it through a Cloudflare Tunnel or a
reverse proxy behind Cloudflare Access.
The container is stateless and needs no volumes. New GHCR packages may be
private: make the package public or authenticate Docker with `read:packages`.

## Google Cloud setup (once)

Defaults match the reference repository's project, region and shared image
repository, but use a separate service, image, runtime identity and secrets:

```sh
PROJECT_ID=serverless-509006
REGION=us-south1
REPOSITORY=images
SERVICE=moinmoin-mcp
RUNTIME_SA="$SERVICE@$PROJECT_ID.iam.gserviceaccount.com"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/$SERVICE:bootstrap"

gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com --project "$PROJECT_ID"
```

Reuse the existing Docker-format `images` Artifact Registry repository. If it
does not exist, create it:

```sh
gcloud artifacts repositories create "$REPOSITORY" --project "$PROJECT_ID" \
  --location "$REGION" --repository-format=docker
```

Create a dedicated runtime identity and two secrets. Supply existing
restricted files containing only the values (no trailing newlines); never
put credential values in workflow YAML, Docker build arguments or source:

```sh
gcloud iam service-accounts create "$SERVICE" --project "$PROJECT_ID"
gcloud secrets create moinmoin-user --project "$PROJECT_ID" \
  --replication-policy=automatic --data-file=/secure/path/wiki-user
gcloud secrets create moinmoin-password --project "$PROJECT_ID" \
  --replication-policy=automatic --data-file=/secure/path/wiki-password

for SECRET in moinmoin-user moinmoin-password; do
  gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT_ID" \
    --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
done
```

Build the first image using Cloud Build, then create the service. Your setup
identity and Cloud Build identity need the usual build/storage permissions,
and the build identity needs Artifact Registry Writer. These are bootstrap
permissions, not permissions for the runtime identity.

```sh
gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE" .
gcloud run deploy "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --image "$IMAGE" --service-account "$RUNTIME_SA" \
  --allow-unauthenticated --port 8787 \
  --cpu 1 --memory 512Mi --concurrency 40 --timeout 300 \
  --min-instances 0 --max-instances 3 \
  --set-env-vars 'MOIN_URL=https://wiki.example.com/,ALLOW_WRITES=false' \
  --set-env-vars 'CF_ACCESS_TEAM_DOMAIN=myteam.cloudflareaccess.com,CF_ACCESS_AUD=YOUR_AUD_TAG' \
  --set-secrets 'MOIN_USER=moinmoin-user:latest,MOIN_PASSWORD=moinmoin-password:latest'

SERVICE_URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" \
  --region "$REGION" --format='value(status.url)')
curl "$SERVICE_URL/health"
```

The service can be `--allow-unauthenticated` at the Cloud Run layer because
the container itself enforces Cloudflare Access: **every MCP request,
including one sent straight to the `run.app` URL, must carry a valid Access
JWT.** Point Cloudflare (a Tunnel or a proxied custom domain) at the service
and protect that hostname with the Access application whose AUD tag you set.
The AUD tag and team domain are not secrets. Health checks are intentionally
public. If your org forbids public Cloud Run services, use an approved
authenticated gateway.

For an anonymous wiki, omit the two wiki credential secrets and their
bindings. Secret references above use `latest`; changing
an environment-injected secret requires deploying a new revision to refresh
running instances. Pin numeric secret versions if you need reproducible
rollbacks.

### GitHub Actions

[The workflow](.github/workflows/container.yml) runs tests, Ruff and mypy,
builds `linux/amd64` and `linux/arm64`, and publishes to:

- `ghcr.io/shaneqi/moinmoinmcp`
- `us-south1-docker.pkg.dev/serverless-509006/images/moinmoin-mcp`

On `main`, it updates the **existing** Cloud Run service by immutable image
digest, preserving its environment, secrets, IAM settings and scaling.
Pull requests build without publishing or accessing GCP credentials.
`v*` tags publish versions without deploying; Markdown-only changes are
skipped. A manual workflow run can publish/deploy `main` as well.

Set the repository Actions secret **`GCP_SA_KEY`** to the deployment service
account's JSON key, as in OFCloudMCP. Repository secrets are not automatically
shared between repositories. Prefer an existing organization secret if
available; never commit a key. The built-in `GITHUB_TOKEN` handles GHCR.
The deployment identity needs:

- `roles/artifactregistry.writer` on the `images` repository;
- `roles/run.developer` on the `moinmoin-mcp` service;
- `roles/iam.serviceAccountUser` on the runtime service account.

It does not need Secret Manager access or project Owner. For an existing
deployment identity, grant the scoped roles after bootstrapping the service:

```sh
DEPLOYER_SA=YOUR-EXISTING-DEPLOYER@serverless-509006.iam.gserviceaccount.com
gcloud artifacts repositories add-iam-policy-binding "$REPOSITORY" \
  --project "$PROJECT_ID" --location "$REGION" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/artifactregistry.writer
gcloud run services add-iam-policy-binding "$SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/run.developer
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --project "$PROJECT_ID" --member="serviceAccount:$DEPLOYER_SA" \
  --role=roles/iam.serviceAccountUser
```

To use another project/region, set repository Actions variables
`GAR_PROJECT_ID`, `GAR_REGISTRY`, `GAR_REPOSITORY`, `CLOUD_RUN_REGION` and use
matching values in bootstrap commands. Configure registry cleanup to retain
recent multi-architecture images rather than deleting all untagged manifests.

## Validation

```sh
uv sync --frozen
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
uv run --frozen mypy
uv run --frozen pytest
docker build -t moinmoin-mcp .
```

Tests use a local XML-RPC fixture and a real MCP client over loopback HTTP;
they never connect to a live wiki or Google Cloud.
