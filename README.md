# moinmoin plugin

Skills-only Cowork plugin for operating a MoinMoin 1.9 wiki via XML-RPC v2. No MCP server.

- `skills/moinmoin` — how Claude reads/searches/edits the wiki, plus `scripts/moin.py` (Python stdlib CLI).
- `skills/moinmoin-setup` — collects URL/username/password, tests them, and rebuilds this plugin with them in `config.json`.
- `config.json` — wiki URL and credentials. Because the plugin syncs through your claude.ai account, this works from any Cowork session. **It contains your password: don't share the .plugin file.**

Wiki requirement: XML-RPC must be enabled (remove `xmlrpc` from `actions_excluded` in wikiconfig.py). The cloud sandbox can only reach domains on your network allowlist; otherwise Claude runs the script on your linked computer.

Overrides: `MOIN_URL`, `MOIN_USER`, `MOIN_PASSWORD`, `MOIN_CONFIG` env vars, or `--url/--user/--password/--config` flags.
