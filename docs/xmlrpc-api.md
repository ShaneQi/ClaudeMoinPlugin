# MoinMoin 1.9 XML-RPC v2 reference

Endpoint: `<wiki base url>/?action=xmlrpc2`. Strings are UTF-8 (v2). Auth: `getAuthToken(user, password)` → token; then `system.multicall([{methodName:"applyAuthToken",params:[token]}, {methodName:..., params:[...]}])`. The MCP server handles authentication automatically. Arbitrary XML-RPC calls are not exposed.

| Method | Params | Returns |
|---|---|---|
| getRPCVersionSupported | – | 2 |
| interwikiName | – | wiki name |
| getPage / getPageVersion | name [, rev] | raw markup |
| getPageHTML / getPageHTMLVersion | name [, rev] | HTML |
| getPageInfo / getPageInfoVersion | name [, rev] | {name, lastModified, author, version} |
| putPage | name, text | true |
| getAllPages | – | [names] |
| getAllPagesEx | {include_system, include_revno, include_deleted, include_underlay, prefix, mark_deleted} | [names] |
| searchPages | query | [[name, context]] |
| searchPagesEx | query, "text"/"title", context_len, case, mapping, regexp | [[name, context]] |
| getRecentChanges | DateTime since (UTC) | [{name, lastModified, author, version}] |
| listLinks | name | [{name, type}] |
| getDiff | name, from_rev, to_rev | {current, conflict, diff} |
| renamePage | old, new | true |
| revertPage | name, rev | true |
| listAttachments | name | [filenames] |
| getAttachment | name, file | binary |
| putAttachment | name, file, binary | true |
| deleteAttachment | name, file | true |
| getProcessingInstruction | name, pi | value |
| getAuthToken | user, password | token ("" on failure) |
| applyAuthToken | token | "SUCCESS" or Fault |

Requires `actions_excluded` in the wiki config to NOT exclude `xmlrpc` (Moin excludes it by default).
