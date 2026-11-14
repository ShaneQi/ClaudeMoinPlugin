# moinmoin plugin

Skills-only Claude plugin for operating a MoinMoin 1.9 wiki via XML-RPC v2. No MCP server.

- `skills/moinmoin` — how Claude reads/searches/edits the wiki, plus `scripts/moin.py` (Python stdlib CLI).
- `hooks/` — a SessionStart hook that hands the plugin options to `moin.py`.

## Configuration

The wiki URL, username and password are plugin options (`userConfig` in `.claude-plugin/plugin.json`). Claude Code asks for them when the plugin is enabled; the password is marked sensitive, so it is masked and kept in the system credential store rather than in `settings.json`.

Commands Claude runs through Bash don't receive plugin options, so at the start of each session `hooks/write_config.py` copies them to `~/.config/moinmoin-plugin/config.json` (mode 0600), which `moin.py` reads. The hook prints nothing, so the password never enters Claude's context. That file is plain text on disk, readable only by your user.

Other ways to configure, in order of precedence: `--url/--user/--password` flags, `MOIN_URL`/`MOIN_USER`/`MOIN_PASSWORD` env vars, `--config PATH`/`MOIN_CONFIG`.

## Wiki requirements

XML-RPC must be enabled (remove `xmlrpc` from `actions_excluded` in wikiconfig.py), and the wiki's domain must be reachable from where Claude runs (in cloud sessions, on the network allowlist).
