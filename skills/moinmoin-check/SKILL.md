---
name: moinmoin-check
description: Checks that the moinmoin plugin's wiki URL, username and password are valid — the wiki is reachable, XML-RPC is enabled, and the login works. Use when the user asks to check, test or verify the wiki connection, settings or credentials, or after they change the plugin's options.
---

# Check the MoinMoin settings

Run the check with the configured settings only — no `--url/--user/--password` overrides, and never ask the user to type the password into the chat:

```bash
python3 <this-skill-base-dir>/../moinmoin/scripts/moin.py check
```

Never read or print `~/.config/moinmoin-plugin/config.json`; it holds the password.

## Report

On success the output is JSON with `endpoint`, `username`, `password_set`, `authenticated`, `rpc_version` and `wiki`. Tell the user the settings are valid and list the wiki name, endpoint and username. If `username` is null, say the wiki is being used anonymously (read-only on most wikis).

On failure, report one line per the table and what to fix:

| Output | Meaning | Fix |
|---|---|---|
| `NOT_CONFIGURED` | No wiki URL set | Set the plugin's options (Claude Code asks for them when the plugin is enabled). Options set during this session apply from the next session. |
| `AUTH_FAILED` | Username or password rejected; "No password is set" means the password option is empty | Correct the username or password in the plugin's options, then start a new session. |
| `NOT_XMLRPC` | The URL answered, but not with XML-RPC | Check the URL is the wiki's base URL (e.g. `https://wiki.example.com/`). If it is, XML-RPC is disabled: remove `xmlrpc` from `actions_excluded` in `wikiconfig.py`. |
| `HTTP <code>` | The server returned an HTTP error | 404: wrong URL. 401/403: the server or a proxy in front of the wiki refused the request. |
| `NETWORK` | Couldn't connect (DNS, refused, TLS, proxy 403) | Check the URL's host. In a cloud session, add the wiki's domain to the environment's allowed hosts. |
| `FAULT n: ...` | The wiki raised an error | Quote it to the user. |

The check only reads (`getAuthToken`, `getRPCVersionSupported`, `interwikiName`); it doesn't prove the account can edit pages.
