# moinmoin plugin

Skills-only Claude plugin for operating a MoinMoin 1.9 wiki via XML-RPC v2. No MCP server.

- `skills/moinmoin` — how Claude reads/searches/edits the wiki, plus `scripts/moin.py` (Python stdlib CLI).
- `skills/moinmoin-check` — verifies the configured URL, username and password.
- `hooks/` — a SessionStart hook that hands the plugin options to `moin.py`.

## Install

In Claude Code (2.1.275 or later):

```
/plugin install moinmoin --marketplace ShaneQi/ClaudeMoinPlugin
```

On older versions, add the marketplace first: `/plugin marketplace add ShaneQi/ClaudeMoinPlugin`, then `/plugin install moinmoin@claude-moin-plugin`.

Claude Code then asks for the wiki URL, username and password; change them later with `/plugin configure moinmoin@claude-moin-plugin`. Start a new session afterwards so the hook picks them up, and ask Claude to check the wiki connection.

## Configuration

The wiki URL, username and password are plugin options (`userConfig` in `.claude-plugin/plugin.json`). Claude Code asks for them when the plugin is enabled; the password is marked sensitive, so it is masked and kept in the system credential store rather than in `settings.json`.

Commands Claude runs through Bash don't receive plugin options, so at the start of each session `hooks/write_config.py` writes them to `config.json` (mode 0600) in the plugin's data directory (`$CLAUDE_PLUGIN_DATA`, else `~/.config/moinmoin-plugin`) and exports `MOIN_CONFIG` with that path through `$CLAUDE_ENV_FILE`, so `moin.py` finds it wherever the client keeps plugin data. Only the path is exported, not the password. The hook prints nothing, so the password never enters Claude's context. It also writes a `status.json` (no secrets) that the `moinmoin-check` skill uses to explain failures.

Options are stored per machine by the client and aren't synced between devices or into cloud sessions; set them on each machine you use.

Other ways to configure, in order of precedence: `--url/--user/--password` flags, `MOIN_URL`/`MOIN_USER`/`MOIN_PASSWORD` env vars, `--config PATH`/`MOIN_CONFIG`.

## Wiki requirements

XML-RPC must be enabled (remove `xmlrpc` from `actions_excluded` in wikiconfig.py), and the wiki's domain must be reachable from where Claude runs (in cloud sessions, on the network allowlist).
