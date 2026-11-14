---
name: moinmoin-setup
description: Configures or changes the MoinMoin wiki URL, username and password stored in the moinmoin plugin. Use when moin.py reports NOT_CONFIGURED or AUTH_FAILED, or the user says "set up the wiki", "change wiki password", "point the wiki plugin at a new URL".
---

# MoinMoin plugin setup

Credentials live in `config.json` at the plugin root (two directories above this skill's base directory): `{"url": "...", "username": "...", "password": "..."}`. Installed plugin files are a read-only synced copy, so storing new credentials means rebuilding the plugin and having the user re-accept it — it then syncs to every Cowork session on their claude.ai account.

## Steps

1. Ask the user (AskUserQuestion, free-text) for: wiki base URL (e.g. `https://wiki.example.com/`), username, password. Leave username empty for anonymous access.
2. Test before saving, without writing the password to any file or echoing it:
   ```bash
   MOIN_URL='<url>' MOIN_USER='<user>' MOIN_PASSWORD='<pw>' python3 <plugin-root>/skills/moinmoin/scripts/moin.py --config /dev/null check
   ```
   If it fails with `NETWORK`/proxy 403, follow the "unreachable" guidance in the moinmoin skill (try via the linked computer, or tell the user to allowlist the domain) — the credentials can still be saved if the user wants. If `FAULT` says the method/action is unknown, xmlrpc is disabled on the wiki: tell the user to remove `xmlrpc` from `actions_excluded` in wikiconfig.py.
3. Copy the plugin root into the scratchpad, write the new `config.json` there (JSON, exact keys above), bump the patch version in `.claude-plugin/plugin.json`.
4. Package and deliver:
   ```bash
   cd <scratch-copy> && zip -r /tmp/moinmoin.plugin . -x "*.DS_Store"
   ```
   Then SendUserFile `/tmp/moinmoin.plugin` and tell the user to accept it to replace the installed version. Remind them the file contains their password, so not to share it.
5. For the current session, keep using the env-var form from step 2 until the new plugin is installed.
