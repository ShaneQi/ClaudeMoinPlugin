---
name: moinmoin-setup
description: Configures or changes the MoinMoin wiki URL, username and password used by the moinmoin plugin. Use when moin.py reports NOT_CONFIGURED or AUTH_FAILED, or the user says "set up the wiki", "change wiki password", "point the wiki plugin at a new URL".
---

# MoinMoin plugin setup

The wiki URL, username and password are the plugin's options (`url`, `username`, `password` in `userConfig`). A SessionStart hook copies them to `~/.config/moinmoin-plugin/config.json`, which `moin.py` reads. Never read, print or echo that file or the password.

## Steps

1. Ask the user (AskUserQuestion, free-text) for: wiki base URL (e.g. `https://wiki.example.com/`), username, password. Leave username empty for anonymous access.
2. Test them without writing the password to any file or echoing it:
   ```bash
   MOIN_URL='<url>' MOIN_USER='<user>' MOIN_PASSWORD='<pw>' python3 <plugin-root>/skills/moinmoin/scripts/moin.py --config /dev/null check
   ```
   If it fails with `NETWORK`/proxy 403, the wiki's domain isn't reachable from here (follow the "unreachable" guidance in the moinmoin skill) — the credentials can still be saved if the user wants. If `FAULT` says the method/action is unknown, xmlrpc is disabled on the wiki: tell the user to remove `xmlrpc` from `actions_excluded` in wikiconfig.py.
3. Tell the user to save them as the plugin's options: Claude Code asks for them when the plugin is enabled, and URL and username can be changed in `/config`; the password is a sensitive option, so it is changed by reconfiguring the plugin. The new values reach `moin.py` from the next session on.
4. For the current session, keep using the env-var form from step 2.
5. Only if the host can't set plugin options, offer to write the config file directly: create `~/.config/moinmoin-plugin/` (mode 700) and write `config.json` with keys `url`, `username`, `password` (mode 600). Warn that it is plain text, and that in cloud sessions it is lost when the container is reclaimed.
