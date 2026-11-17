// The MCP tools. Names, arguments, results and error codes match the earlier
// Python server, so Claude sees no difference.
import { callWiki, pageUrl, WikiError } from "./moin.js";
import { base64ToBytes, bytesToBase64, XmlRpcDateTime } from "./xmlrpc.js";

/** A tool failure, reported to Claude as the tool's error text. */
export class ToolError extends Error {}

// ---------- argument schemas and checks ----------

const str = (description) => ({ type: "string", ...(description && { description }) });
const revision = { anyOf: [{ type: "integer", minimum: 1 }, { type: "null" }], default: null };

function checkArgs(schema, args) {
  if (!args || typeof args !== "object" || Array.isArray(args)) throw new ToolError("INVALID_ARGUMENTS: arguments must be an object");
  const out = {};
  for (const [name, spec] of Object.entries(schema.properties)) {
    let v = args[name];
    if (v === undefined) {
      if (schema.required?.includes(name)) throw new ToolError(`INVALID_ARGUMENTS: ${name} is required`);
      out[name] = spec.default ?? null;
      continue;
    }
    const kinds = spec.anyOf ?? [spec];
    if (v === null && kinds.some((k) => k.type === "null")) {
      out[name] = null;
      continue;
    }
    const kind = kinds.find((k) => k.type !== "null");
    const bad = (why) => new ToolError(`INVALID_ARGUMENTS: ${name} ${why}`);
    switch (kind.type) {
      case "string":
        if (typeof v !== "string") throw bad("must be a string");
        break;
      case "boolean":
        if (typeof v !== "boolean") throw bad("must be true or false");
        break;
      case "integer":
      case "number":
        if (typeof v !== "number" || !Number.isFinite(v)) throw bad("must be a number");
        if (kind.type === "integer" && !Number.isInteger(v)) throw bad("must be an integer");
        if (kind.minimum !== undefined && v < kind.minimum) throw bad(`must be at least ${kind.minimum}`);
        if (kind.exclusiveMinimum !== undefined && v <= kind.exclusiveMinimum) throw bad(`must be greater than ${kind.exclusiveMinimum}`);
        if (kind.maximum !== undefined && v > kind.maximum) throw bad(`must be at most ${kind.maximum}`);
        break;
    }
    out[name] = v;
  }
  return out;
}

// ---------- result checks ----------

const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v) && !(v instanceof Uint8Array) && !(v instanceof XmlRpcDateTime);
const isStr = (v) => typeof v === "string";
const T = {
  str: isStr,
  int: Number.isInteger,
  bool: (v) => typeof v === "boolean",
  obj: isObj,
  bytes: (v) => v instanceof Uint8Array,
  strs: (v) => Array.isArray(v) && v.every(isStr),
  strsOrNull: (v) => Array.isArray(v) && v.every((x) => x === null || isStr(x)),
  objs: (v) => Array.isArray(v) && v.every(isObj),
  rows: (v) => Array.isArray(v) && v.every((r) => Array.isArray(r) && r.length >= 2 && r.every(isStr)),
};

// Dates become "YYYY-MM-DDTHH:MM:SS"; bytes are left for the caller to encode.
function jsonable(v) {
  if (v instanceof XmlRpcDateTime) return v.toISO();
  if (v instanceof Uint8Array) return v;
  if (Array.isArray(v)) return v.map(jsonable);
  if (isObj(v)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, jsonable(x)]));
  return v;
}

// ---------- tools ----------

const page = str();
const READ = { readOnlyHint: true, destructiveHint: false, openWorldHint: true };
const WRITE = { readOnlyHint: false, destructiveHint: true, openWorldHint: true };

const tool = (name, description, properties, required, annotations, run) => ({
  name, description, annotations, run,
  inputSchema: { type: "object", properties, ...(required.length && { required }) },
});

export const TOOLS = [
  tool("check_connection", "Check wiki connectivity and credentials, and report the server's write mode.",
    {}, [], READ, async ({ ctx }) => ({
      rpc_version: await ctx.rpc(T.int, "getRPCVersionSupported"),
      wiki: await ctx.rpc(T.strsOrNull, "interwikiName"),
      authenticated: !!ctx.settings.username,
      writes_enabled: ctx.settings.allowWrites,
    })),

  tool("get_page", "Read raw MoinMoin markup, optionally at a specific revision.",
    { page, version: revision }, ["page"], READ, async ({ args, ctx }) => ({
      ...ctx.details(args.page, args.version),
      text: args.version
        ? await ctx.rpc(T.str, "getPageVersion", args.page, args.version)
        : await ctx.rpc(T.str, "getPage", args.page),
    })),

  tool("get_page_html", "Read rendered HTML, optionally at a specific revision.",
    { page, version: revision }, ["page"], READ, async ({ args, ctx }) => ({
      ...ctx.details(args.page, args.version),
      html: args.version
        ? await ctx.rpc(T.str, "getPageHTMLVersion", args.page, args.version)
        : await ctx.rpc(T.str, "getPageHTML", args.page),
    })),

  tool("get_page_info", "Get page author, lastModified and version; use before and after edits.",
    { page, version: revision }, ["page"], READ, async ({ args, ctx }) => ({
      ...(args.version
        ? await ctx.rpc(T.obj, "getPageInfoVersion", args.page, args.version)
        : await ctx.rpc(T.obj, "getPageInfo", args.page)),
      ...ctx.details(args.page, args.version),
    })),

  tool("put_page", "Create or replace an entire page. Read it first and preserve processing instructions.",
    { page, text: str() }, ["page", "text"], WRITE,
    ({ args, ctx }) => ctx.write("putPage", args.page, args.text)),

  tool("list_pages", "List sorted wiki page names, optionally limited to a prefix.",
    { prefix: { anyOf: [{ type: "string" }, { type: "null" }], default: null }, include_system: { type: "boolean", default: false } },
    [], READ, async ({ args, ctx }) => {
      const options = { include_system: args.include_system };
      if (args.prefix) options.prefix = args.prefix;
      const pages = await ctx.rpc(T.strs, "getAllPagesEx", options);
      return [...pages].sort(byCodePoint).map((p) => ctx.details(p));
    }),

  tool("search_pages", "Search page content or titles, returning matching names and context.",
    { query: str(), titles: { type: "boolean", default: false }, limit: { type: "integer", minimum: 1, maximum: 500, default: 50 } },
    ["query"], READ, async ({ args, ctx }) => {
      const rows = await ctx.rpc(T.rows, "searchPagesEx", args.query, args.titles ? "title" : "text", 120, false, false, false);
      return rows.slice(0, args.limit).map((row) => ({
        ...ctx.details(row[0]),
        context: row[1],
        url: row[2] || pageUrl(ctx.settings.url, row[0]),
      }));
    }),

  tool("recent_changes", "List changes since the given number of days ago (UTC).",
    { days: { type: "number", exclusiveMinimum: 0, maximum: 3650, default: 7 } }, [], READ,
    async ({ args, ctx }) => {
      const since = XmlRpcDateTime.fromDate(new Date(Date.now() - args.days * 86_400_000));
      const changes = await ctx.rpc(T.objs, "getRecentChanges", since);
      return changes.map((c) => ({ ...c, ...ctx.details(c.name) }));
    }),

  tool("list_links", "List a page's outgoing links.",
    { page }, ["page"], READ, async ({ args, ctx }) => {
      const links = await ctx.rpc(T.objs, "listLinks", args.page);
      return links.map((l) => ({ ...l, url: l.type === 1 ? l.name : pageUrl(ctx.settings.url, l.name) }));
    }),

  tool("get_diff", "Get a base64-encoded Moin binary diff; omitted revisions use the wiki's defaults.",
    { page, from_revision: revision, to_revision: revision }, ["page"], READ, async ({ args, ctx }) => {
      const { diff, ...rest } = await ctx.rpc(T.obj, "getDiff", args.page, args.from_revision, args.to_revision);
      if (!(diff instanceof Uint8Array)) throw new ToolError("INVALID_RESPONSE: getDiff did not return a binary diff");
      return { ...rest, ...ctx.details(args.page, args.to_revision), diff_base64: bytesToBase64(diff) };
    }),

  tool("rename_page", "Rename a page. Confirm this change with the user first.",
    { page, new_name: str() }, ["page", "new_name"], WRITE, async ({ args, ctx }) => ({
      ...(await ctx.write("renamePage", args.page, args.new_name)),
      ...ctx.details(args.new_name),
    })),

  tool("revert_page", "Revert a page to a revision. Confirm this change with the user first.",
    { page, revision: { type: "integer", minimum: 1 } }, ["page", "revision"], WRITE,
    // MoinMoin's revertPage expects the revision as a string.
    ({ args, ctx }) => ctx.write("revertPage", args.page, String(args.revision))),

  tool("list_attachments", "List attachment filenames on a page.",
    { page }, ["page"], READ, async ({ args, ctx }) => ({
      ...ctx.details(args.page),
      attachments: await ctx.rpc(T.strs, "listAttachments", args.page),
    })),

  tool("get_attachment", "Download an attachment as base64, without writing server-local files.",
    { page, name: str() }, ["page", "name"], READ, async ({ args, ctx }) => ({
      ...ctx.details(args.page),
      name: args.name,
      content_base64: bytesToBase64(await ctx.rpc(T.bytes, "getAttachment", args.page, args.name)),
    })),

  tool("put_attachment", "Upload base64 attachment content. May overwrite an existing attachment.",
    { page, name: str(), content_base64: str() }, ["page", "name", "content_base64"], WRITE,
    ({ args, ctx }) => {
      ctx.requireWrites();
      const b64 = args.content_base64;
      let data;
      if (b64.length % 4 === 0 && /^[A-Za-z0-9+/]*={0,2}$/.test(b64)) {
        try { data = base64ToBytes(b64); } catch { data = null; }
      }
      if (!data) throw new ToolError("INVALID_ATTACHMENT: content_base64 must be valid base64");
      return ctx.write("putAttachment", args.page, args.name, data);
    }),

  tool("delete_attachment", "Delete a page attachment. Confirm this change with the user first.",
    { page, name: str() }, ["page", "name"], WRITE,
    ({ args, ctx }) => ctx.write("deleteAttachment", args.page, args.name)),
];

// Python's sorted() order for strings (by code point, not UTF-16 unit).
function byCodePoint(a, b) {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0);
    if (d) return d;
  }
  return x.length - y.length;
}

/** Runs a tool; returns an MCP CallToolResult. */
export async function runTool(t, rawArgs, settings, call = callWiki) {
  const ctx = {
    settings,
    details: (p, version) => ({ page: p, url: pageUrl(settings.url, p, version) }),
    async rpc(check, method, ...params) {
      const result = jsonable(await call(settings, method, params));
      if (!check(result)) throw new ToolError(`INVALID_RESPONSE: unexpected result from ${method}`);
      return result;
    },
    requireWrites() {
      if (!settings.allowWrites) throw new ToolError("WRITES_DISABLED: set ALLOW_WRITES=true on the server to enable writes");
    },
    async write(method, p, ...params) {
      ctx.requireWrites();
      if ((await ctx.rpc(T.bool, method, p, ...params)) !== true) {
        throw new ToolError(`WRITE_FAILED: ${method} did not confirm success`);
      }
      return { ok: true, ...ctx.details(p) };
    },
  };
  try {
    const args = checkArgs(t.inputSchema, rawArgs ?? {});
    const out = await t.run({ args, ctx });
    const structured = Array.isArray(out) ? { result: out } : out;
    return { content: [{ type: "text", text: JSON.stringify(structured) }], structuredContent: structured };
  } catch (e) {
    if (e instanceof ToolError || e instanceof WikiError) return { content: [{ type: "text", text: e.message }], isError: true };
    console.error(e);
    return { content: [{ type: "text", text: `Unexpected error: ${e?.message ?? e}` }], isError: true };
  }
}
