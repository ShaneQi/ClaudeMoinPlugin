# MoinMoin 1.9 wiki markup cheat sheet

| Element | Markup |
|---|---|
| Headings | `= H1 =`, `== H2 ==`, `=== H3 ===` (spaces and matching `=` required) |
| Bold / italic | `'''bold'''`, `''italic''`, `'''''both'''''` |
| Monospace | `` `code` `` or `{{{code}}}` |
| Code block | `{{{#!python` … `}}}` (or plain `{{{` … `}}}`) |
| Link to page | `[[PageName]]`, `[[PageName|label]]`, `[[/SubPage]]`, CamelCase auto-links |
| Suppress CamelCase link | `!CamelCase` |
| External link | `[[https://example.com|label]]` |
| Attachment link / image | `[[attachment:file.pdf]]`, `{{attachment:pic.png}}` |
| Bullet list | ` * item` (leading space required; indent more for nesting) |
| Numbered list | ` 1. item` |
| Definition list | ` term:: definition` |
| Table | `||cell||cell||` ; header style `||'''H'''||'''H'''||` |
| Horizontal rule | `----` |
| Line break | `<<BR>>` |
| Macros | `<<TableOfContents>>`, `<<Date(2026-10-30)>>`, `<<FullSearch(term)>>`, `<<Include(Page)>>` |
| Comment line | `## comment` |
| Category | `CategoryFoo` on a line after `----` at the bottom |

Processing instructions go at the very top: `#format wiki`, `#acl User:read,write All:read`, `#pragma section-numbers off`. Pages starting with `#format markdown` (if the wiki supports it) should stay Markdown.
