# MoinMoin MCP server

A remote MCP server for MoinMoin 1.9 wikis over XML-RPC v2. Runs as a
non-root Python container on Google Cloud Run or behind an HTTPS reverse
proxy. Wiki credentials stay on the server; clients only receive a separate
bearer token.

The deployment follows [OFCloudMCP](https://github.com/ShaneQi/OFCloudMCP):
stateless Streamable HTTP, multi-architecture images in GHCR and Google
Artifact Registry, and image-only Cloud Run updates on `main`.

## Local development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen
cp .env.example .env
chmod 600 .env
# Edit .env: set MOIN_URL, ACCESS_TOKEN and, for a private wiki, both credentials.
# Generate ACCESS_TOKEN with: openssl rand -base64 32
uv run --env-file .env moinmoin-mcp
```

The server listens on `0.0.0.0:$PORT` (default `8787`). Do not expose plain HTTP
to the internet; use Cloud Run's HTTPS URL or a TLS reverse proxy.

- `GET /health` and `/healthz`: unauthenticated liveness checks, return `ok`.
  These do not contact the wiki. Use `check_connection` to check wiki access.
- `/mcp`: authenticated MCP Streamable HTTP with JSON responses, no sticky
  sessions or persistent disk required.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MOIN_URL` | required | Wiki base URL, e.g. `https://wiki.example.com/wiki/`; an existing `?action=xmlrpc2` endpoint also works |
| `MOIN_USER`, `MOIN_PASSWORD` | empty | Set both for a private wiki, or neither for anonymous wiki access |
| `ACCESS_TOKEN` | required | Long random bearer token required by every MCP request, even with an anonymous wiki |
| `ALLOW_WRITES` | `false` | Set exactly `true` to enable wiki mutations |
| `PORT` | `8787` | HTTP port; Cloud Run supplies its own `PORT` |
| `ALLOWED_HOSTS` | localhost and `127.0.0.1`, any port | Comma-separated MCP server hostnames (not wiki hostnames). Set the exact Cloud Run/custom hostname for remote access; `:*` allows any port |

No plugin configuration files or host credentials are read. Standard proxy
environment variables are honored for outbound wiki requests. Configure
`SSL_CERT_FILE` if your wiki uses a private certificate authority.

XML-RPC must be enabled on the wiki: remove `xmlrpc` from `actions_excluded`
in `wikiconfig.py`. The wiki must be reachable from the container, including
any necessary Cloud Run VPC egress/network configuration. Use HTTPS for a
private wiki; XML-RPC sends its credentials to the configured endpoint.

## MCP client setup

Use an MCP client that supports HTTP request headers:

```json
{
  "mcpServers": {
    "moinmoin": {
      "type": "http",
      "url": "https://YOUR-SERVICE.run.app/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_ACCESS_TOKEN"
      }
    }
  }
}
```

This is the Claude Code HTTP configuration shape; other clients may use a
different key for the transport. Configure the token through your client's
secret storage rather than committing it. This server uses static bearer
authentication, not OAuth; clients that only support OAuth connectors need
an authentication gateway.

Check connectivity with a valid MCP initialization request:

```sh
curl https://YOUR-SERVICE.run.app/health
curl https://YOUR-SERVICE.run.app/mcp \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1"}}}'
```

## Tools

| Tool | Purpose |
|---|---|
| `check_connection` | Wiki RPC version, interwiki identity `[name, id]`, authentication and write mode |
| `get_page`, `get_page_html`, `get_page_info` | Markup, HTML or metadata; optional `version` |
| `list_pages` | Sorted names; optional `prefix`, `include_system` |
| `search_pages` | Content/title search; `query`, optional `titles`, `limit` (1-500) |
| `recent_changes` | Changes in the last `days` (default 7, maximum 3650), UTC |
| `list_links` | Outgoing links |
| `get_diff` | Moin's compressed binary delta as `diff_base64`, with revision/conflict metadata; not a textual unified diff |
| `list_attachments`, `get_attachment` | Filenames and `{name, content_base64}` |
| `put_page` | Replace/create a whole page with `page`, `text` |
| `rename_page`, `revert_page` | Rename (`page`, `new_name`) or revert (`page`, `revision`) |
| `put_attachment` | Upload `page`, `name`, `content_base64` |
| `delete_attachment` | Delete `page`, `name` |

All five mutation tools return an explicit error without contacting the wiki
unless `ALLOW_WRITES=true`. Unlike OFCloudMCP, there is no write preview:
disabled writes fail rather than pretending to succeed. Wiki ACLs still
apply. No arbitrary XML-RPC tool is exposed, so it cannot bypass this gate.

Read before editing: `put_page` replaces the entire page. Preserve `#format`,
`#acl` and `#pragma` lines, check metadata for concurrent edits, confirm
destructive changes, and read back after writing. There is no atomic
compare-and-swap protection against simultaneous wiki edits. See the
[MoinMoin markup guide](skills/moinmoin/references/moin-syntax.md).

Attachment data is base64, not a local filesystem path. MCP requests are
limited to 4 MiB by the SDK (base64 overhead counts); use the wiki directly
for larger uploads.

## Containers / VPS

```sh
cp compose.example.yml compose.yml
docker compose -f compose.yml up -d --build
# Or use the published ghcr.io/shaneqi/moinmoinmcp:latest image.
```

Compose binds `127.0.0.1:8787`; terminate HTTPS in a reverse proxy, preserve
the public `Host` header, and add that hostname to `ALLOWED_HOSTS` in `.env`.
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

Create a dedicated runtime identity and three secrets. Supply existing
restricted files containing only the values (no trailing newlines); never
put credential values in workflow YAML, Docker build arguments or source:

```sh
gcloud iam service-accounts create "$SERVICE" --project "$PROJECT_ID"
gcloud secrets create moinmoin-user --project "$PROJECT_ID" \
  --replication-policy=automatic --data-file=/secure/path/wiki-user
gcloud secrets create moinmoin-password --project "$PROJECT_ID" \
  --replication-policy=automatic --data-file=/secure/path/wiki-password
gcloud secrets create moinmoin-access-token --project "$PROJECT_ID" \
  --replication-policy=automatic --data-file=/secure/path/access-token

for SECRET in moinmoin-user moinmoin-password moinmoin-access-token; do
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
  --set-secrets 'MOIN_USER=moinmoin-user:latest,MOIN_PASSWORD=moinmoin-password:latest,ACCESS_TOKEN=moinmoin-access-token:latest'

SERVICE_URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" \
  --region "$REGION" --format='value(status.url)')
gcloud run services update "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --update-env-vars "ALLOWED_HOSTS=${SERVICE_URL#https://}"
curl "$SERVICE_URL/health"
```

`--allow-unauthenticated` disables Google's IAM login requirement so ordinary
MCP clients can connect. **The application still requires `ACCESS_TOKEN` on
every MCP request.** Health checks are intentionally public. If your org
forbids public Cloud Run services, use an approved authenticated gateway.
Only localhost MCP requests are accepted until `ALLOWED_HOSTS` is updated.
For a custom domain, add its exact hostname too.

For an anonymous wiki, omit the two wiki credential secrets and their
bindings; keep `ACCESS_TOKEN`. Secret references above use `latest`; changing
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

## Migration from the plugin

The legacy plugin files remain for compatibility, but are not included in
the container or imported by the server. Disable/uninstall the old plugin
in your client to avoid duplicate wiki capabilities, deploy this server,
and add the HTTP MCP connection instead. Set wiki credentials in the
server environment/Secret Manager; plugin options and `MOIN_CONFIG` are
not used by MCP. After migrating, remove the old plaintext
`~/.config/moinmoin-plugin/config.json` yourself if no longer needed.

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
