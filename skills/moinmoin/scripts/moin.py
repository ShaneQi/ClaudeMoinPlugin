#!/usr/bin/env python3
"""MoinMoin 1.9 XML-RPC v2 command-line client (stdlib only).

Config resolution (first non-empty wins, per field):
  1. --url / --user / --password flags
  2. MOIN_URL / MOIN_USER / MOIN_PASSWORD environment variables
  3. --config PATH or MOIN_CONFIG, else the first existing of
     ~/.config/moinmoin-plugin/config.json  (written by the plugin's SessionStart
     hook from its userConfig options) and <plugin root>/config.json (legacy)
     -- keys: url, username, password

Auth: calls getAuthToken(user, password) once per invocation, then runs every
call inside system.multicall prefixed by applyAuthToken(token). With no
username the wiki is used anonymously.

Output is JSON on stdout. Errors go to stderr with exit code 1.
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
import xmlrpc.client as xc

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
USER_CONFIG = os.path.join(os.path.expanduser("~"), ".config", "moinmoin-plugin", "config.json")
LEGACY_CONFIG = os.path.join(PLUGIN_ROOT, "config.json")


def die(msg):
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def load_config(args):
    path = args.config or os.environ.get("MOIN_CONFIG")
    if not path:
        path = next((p for p in (USER_CONFIG, LEGACY_CONFIG) if os.path.isfile(p)), USER_CONFIG)
    cfg = {}
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        try:
            with open(path) as f:
                cfg = json.load(f)
        except Exception as e:  # noqa: BLE001
            die(f"cannot read config {path}: {e}")
    url = args.url or os.environ.get("MOIN_URL") or cfg.get("url", "")
    user = args.user or os.environ.get("MOIN_USER") or cfg.get("username", "")
    pw = args.password or os.environ.get("MOIN_PASSWORD") or cfg.get("password", "")
    if not url:
        die("NOT_CONFIGURED: no wiki URL. Set the plugin's options or run the moinmoin-setup skill.")
    return url, user, pw


def endpoint(url):
    if "action=xmlrpc2" in url:
        return url
    base = url.split("?")[0]
    if not base.endswith("/"):
        base += "/"
    return base + "?action=xmlrpc2"


class UrllibTransport(xc.Transport):
    """Transport via urllib so HTTPS_PROXY / SSL_CERT_FILE are honored
    (xmlrpc.client's default transport ignores proxy env vars)."""

    def __init__(self, scheme):
        super().__init__(use_builtin_types=True)
        self.scheme = scheme

    def request(self, host, handler, request_body, verbose=False):
        import urllib.request
        req = urllib.request.Request(
            f"{self.scheme}://{host}{handler}", data=request_body,
            headers={"Content-Type": "text/xml", "User-Agent": "moin-plugin/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.verbose = verbose
            return self.parse_response(resp)


class Moin:
    def __init__(self, url, user, pw):
        self.url = endpoint(url)
        scheme = self.url.split(":", 1)[0]
        self.proxy = xc.ServerProxy(self.url, transport=UrllibTransport(scheme),
                                    allow_none=True, use_builtin_types=True)
        self.token = None
        if user:
            tok = self.proxy.getAuthToken(user, pw)
            if not tok:
                die("AUTH_FAILED: getAuthToken returned empty token (bad username/password?)")
            self.token = tok

    def call(self, method, *params):
        if not self.token:
            return getattr(self.proxy, method)(*params)
        mc = xc.MultiCall(self.proxy)
        mc.applyAuthToken(self.token)
        getattr(mc, method)(*params)
        results = mc()
        auth = results[0]  # raises Fault if auth failed
        if auth != "SUCCESS":
            die(f"AUTH_FAILED: applyAuthToken returned {auth!r}")
        return results[1]  # raises xc.Fault on server error


def jsonable(o):
    if isinstance(o, (bytes, bytearray)):
        return base64.b64encode(o).decode()
    if isinstance(o, (xc.DateTime, dt.datetime)):
        return str(o)
    return str(o)


def read_text(args):
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    if args.text is not None:
        return args.text
    return sys.stdin.read()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    p.add_argument("--url")
    p.add_argument("--user")
    p.add_argument("--password")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify connectivity + auth; prints wiki name and RPC version")
    s = sub.add_parser("get", help="raw wiki markup of a page"); s.add_argument("page"); s.add_argument("--version", type=int)
    s = sub.add_parser("html", help="rendered HTML of a page"); s.add_argument("page"); s.add_argument("--version", type=int)
    s = sub.add_parser("info", help="page metadata (author, lastModified, version)"); s.add_argument("page"); s.add_argument("--version", type=int)
    s = sub.add_parser("put", help="create/overwrite a page (text from --file, --text, or stdin)")
    s.add_argument("page"); s.add_argument("--file"); s.add_argument("--text")
    s = sub.add_parser("list", help="list all page names"); s.add_argument("--prefix"); s.add_argument("--include-system", action="store_true")
    s = sub.add_parser("search", help="full-text search"); s.add_argument("query"); s.add_argument("--titles", action="store_true", help="title search only")
    s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("recent", help="recent changes"); s.add_argument("--days", type=float, default=7)
    s = sub.add_parser("links", help="outgoing links of a page"); s.add_argument("page")
    s = sub.add_parser("diff", help="diff between versions"); s.add_argument("page"); s.add_argument("--from", dest="frm", type=int); s.add_argument("--to", type=int)
    s = sub.add_parser("rename", help="rename a page"); s.add_argument("page"); s.add_argument("newname")
    s = sub.add_parser("revert", help="revert page to an older revision"); s.add_argument("page"); s.add_argument("revision", type=int)
    s = sub.add_parser("attachments", help="list attachments"); s.add_argument("page")
    s = sub.add_parser("get-attachment", help="download attachment"); s.add_argument("page"); s.add_argument("name"); s.add_argument("--out", required=True)
    s = sub.add_parser("put-attachment", help="upload attachment"); s.add_argument("page"); s.add_argument("path"); s.add_argument("--name")
    s = sub.add_parser("delete-attachment", help="delete attachment"); s.add_argument("page"); s.add_argument("name")
    s = sub.add_parser("raw", help="call any XML-RPC method; params are JSON values"); s.add_argument("method"); s.add_argument("params", nargs="*")

    a = p.parse_args()
    url, user, pw = load_config(a)
    try:
        m = Moin(url, user, pw)
        c = a.cmd
        if c == "check":
            out = {"endpoint": m.url, "authenticated": bool(m.token),
                   "rpc_version": m.call("getRPCVersionSupported"),
                   "wiki": m.call("interwikiName")}
        elif c == "get":
            out = m.call("getPageVersion", a.page, a.version) if a.version else m.call("getPage", a.page)
            if isinstance(out, dict):  # Fault-like dict for missing page
                die(f"PAGE_ERROR: {out}")
            sys.stdout.write(out)
            return
        elif c == "html":
            out = m.call("getPageHTMLVersion", a.page, a.version) if a.version else m.call("getPageHTML", a.page)
            sys.stdout.write(out)
            return
        elif c == "info":
            out = m.call("getPageInfoVersion", a.page, a.version) if a.version else m.call("getPageInfo", a.page)
        elif c == "put":
            out = {"ok": m.call("putPage", a.page, read_text(a)), "page": a.page}
        elif c == "list":
            opts = {"include_system": a.include_system}
            if a.prefix:
                opts["prefix"] = a.prefix
            out = sorted(m.call("getAllPagesEx", opts))
        elif c == "search":
            res = m.call("searchPagesEx", a.query, "title" if a.titles else "text", 120, False, False, False)
            out = [{"page": r[0], "context": r[1]} for r in res][: a.limit]
        elif c == "recent":
            since = xc.DateTime(dt.datetime.utcnow() - dt.timedelta(days=a.days))
            out = m.call("getRecentChanges", since)
        elif c == "links":
            out = m.call("listLinks", a.page)
        elif c == "diff":
            out = m.call("getDiff", a.page, a.frm, a.to)
        elif c == "rename":
            out = {"ok": m.call("renamePage", a.page, a.newname)}
        elif c == "revert":
            out = {"ok": m.call("revertPage", a.page, a.revision)}
        elif c == "attachments":
            out = m.call("listAttachments", a.page)
        elif c == "get-attachment":
            data = m.call("getAttachment", a.page, a.name)
            with open(a.out, "wb") as f:
                f.write(data if isinstance(data, (bytes, bytearray)) else bytes(data.data))
            out = {"ok": True, "saved": a.out}
        elif c == "put-attachment":
            with open(a.path, "rb") as f:
                data = xc.Binary(f.read())
            name = a.name or os.path.basename(a.path)
            out = {"ok": m.call("putAttachment", a.page, name, data), "name": name}
        elif c == "delete-attachment":
            out = {"ok": m.call("deleteAttachment", a.page, a.name)}
        elif c == "raw":
            out = m.call(a.method, *[json.loads(x) for x in a.params])
        print(json.dumps(out, default=jsonable, indent=2, ensure_ascii=False))
    except xc.Fault as e:
        die(f"FAULT {e.faultCode}: {e.faultString}")
    except xc.ProtocolError as e:
        die(f"HTTP {e.errcode} {e.errmsg} at {e.url}")
    except OSError as e:
        die(f"NETWORK: {e}")


if __name__ == "__main__":
    main()
