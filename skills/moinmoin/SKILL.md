---
name: moinmoin
description: Operates Shane's MoinMoin wiki over XML-RPC v2 — reads, searches, lists, creates, edits, renames, reverts pages and manages attachments. Use when the user mentions "the wiki", "moin", "MoinMoin", a wiki page name (CamelCase like FrontPage), or asks to look something up in, add to, update, or publish to the wiki.
---

# MoinMoin wiki operations

All wiki access goes through the bundled stdlib-only CLI `scripts/moin.py` (in this skill's base directory). It reads the wiki URL and credentials from `config.json` at the plugin root (two directories above this skill), logs in with `getAuthToken`, and wraps every call in a multicall with `applyAuthToken`. Never print or echo the password.

```bash
MOIN="python3 <this-skill-base-dir>/scripts/moin.py"
$MOIN check                       # connectivity + auth; run this first each session
```

## If not configured or unreachable

- Output `NOT_CONFIGURED` → load and follow the **moinmoin-setup** skill, then retry.
- `AUTH_FAILED` → credentials are wrong or changed; run moinmoin-setup to replace them.
- `NETWORK` / proxy `403` / connection refused from the cloud sandbox → the wiki's domain is probably not on the cloud egress allowlist. If the user's computer is linked, copy the script and config there (stage nothing — write them with `device_bash` into the session's scratch home, outside `mnt/`) and run the same commands with `device_bash`; otherwise tell the user the domain must be added to the network allowlist in Claude settings (Settings → Capabilities / network access).

## Commands

| Task | Command |
|---|---|
| Read page markup | `$MOIN get PageName [--version N]` |
| Rendered HTML | `$MOIN html PageName` |
| Metadata (author, lastModified, version) | `$MOIN info PageName` |
| Create / overwrite page | `$MOIN put PageName --file page.txt` (or `--text "..."`, or stdin) |
| List pages | `$MOIN list [--prefix Foo] [--include-system]` |
| Full-text search | `$MOIN search "query" [--titles] [--limit 50]` |
| Recent changes | `$MOIN recent [--days 7]` |
| Outgoing links | `$MOIN links PageName` |
| Diff revisions | `$MOIN diff PageName --from 3 --to 5` |
| Rename | `$MOIN rename OldName NewName` |
| Revert | `$MOIN revert PageName 4` |
| Attachments | `attachments Page`, `get-attachment Page file --out path`, `put-attachment Page ./file [--name x]`, `delete-attachment Page file` |
| Anything else | `$MOIN raw methodName '"jsonArg1"' '42'` (params are JSON literals) |

Page names: use the exact wiki name, including `/` for subpages (`Projects/Roadmap`). Quote names with spaces.

## Editing rules

1. **Always read before writing.** `putPage` replaces the whole page. For edits: `get` → save to a file in the scratchpad → modify with Edit → `put --file`. Never retype a page from memory.
2. Write markup in MoinMoin 1.9 syntax, not Markdown — see `references/moin-syntax.md`. Preserve any `#format`, `#acl`, `#pragma` processing-instruction lines at the top of the page.
3. Before overwriting a page someone else may have edited, check `info` for `lastModified`/`author` and mention it if it changed during the session.
4. Confirm with the user before `rename`, `revert`, `delete-attachment`, or overwriting a page that has substantial content they did not ask to replace.
5. After a write, `get` the page back (or `info`) to confirm the new version, and give the user the page URL: `<wiki url>/<PageName>`.

## Errors

`FAULT n: ...` comes from the wiki (e.g. no such page, permission denied by ACL, page unchanged). Report it plainly. XML-RPC method details are in `references/xmlrpc-api.md`.
