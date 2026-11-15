import threading
import xmlrpc.client as xc
from dataclasses import dataclass, field
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

import pytest

from moinmoin_mcp.client import Settings


@dataclass
class Wiki:
    pages: dict = field(default_factory=lambda: {"FrontPage": "= Wiki =", "Alpha": "Hello"})
    attachments: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    reject_token: bool = False
    fail_write: bool = False
    anonymous: bool = False
    url: str = ""

    @property
    def settings(self):
        return Settings(self.url, "wiki-user", "wiki-password", "test-access-token")

    def _dispatch(self, method, params):
        if method == "system.multicall":
            results = []
            authenticated = False
            for call in params[0]:
                name, args = call["methodName"], call["params"]
                if name == "applyAuthToken":
                    self.calls.append((name, args))
                    authenticated = args == ["wiki-token"] and not self.reject_token
                    result = "SUCCESS" if authenticated else xc.Fault(403, "Invalid token")
                else:
                    result = self.execute(name, args, authenticated)
                if isinstance(result, xc.Fault):
                    results.append(
                        {"faultCode": result.faultCode, "faultString": result.faultString}
                    )
                else:
                    results.append([result])
            return results
        return self.execute(method, params, False)

    def execute(self, method, params, authenticated):
        self.calls.append((method, params))
        if method == "getAuthToken":
            return "wiki-token" if tuple(params) == ("wiki-user", "wiki-password") else ""
        if not authenticated and not self.anonymous:
            return xc.Fault(403, "Authentication required")
        if method == "getRPCVersionSupported":
            return 2
        if method == "interwikiName":
            return ["TestWiki", "wiki-id"]
        if method in ("getPage", "getPageVersion"):
            if params[0] == "FaultDict":
                return {"faultCode": 404, "faultString": "Missing page"}
            return self.pages.get(params[0], xc.Fault(404, "Missing page"))
        if method in ("getPageHTML", "getPageHTMLVersion"):
            return "<h1>Wiki</h1>"
        if method in ("getPageInfo", "getPageInfoVersion"):
            return {
                "name": params[0],
                "version": params[1] if len(params) > 1 else 2,
                "author": "WikiUser",
                "lastModified": xc.DateTime("20261114T12:00:00"),
            }
        if method == "getAllPagesEx":
            return [name for name in self.pages if name.startswith(params[0].get("prefix", ""))]
        if method == "searchPagesEx":
            return [
                [name, text, f"https://wiki.example/{name}"] for name, text in self.pages.items()
            ]
        if method == "getRecentChanges":
            assert isinstance(params[0], xc.DateTime)
            return [{"name": "FrontPage", "lastModified": xc.DateTime("20261114T12:00:00")}]
        if method == "listLinks":
            return [{"name": "Alpha", "type": 0}]
        if method == "getDiff":
            return {
                "current": 2,
                "conflict": False,
                "diffversion": 1,
                "diff": xc.Binary(b"\x00diff"),
            }
        if method == "listAttachments":
            return [name for page, name in self.attachments if page == params[0]]
        if method == "getAttachment":
            return xc.Binary(self.attachments[(params[0], params[1])])
        if self.fail_write:
            return False
        if method == "putPage":
            self.pages[params[0]] = params[1]
        elif method == "renamePage":
            self.pages[params[1]] = self.pages.pop(params[0])
        elif method == "revertPage":
            assert isinstance(params[1], str)  # MoinMoin's _instr expects a string.
            self.pages[params[0]] = "reverted"
        elif method == "putAttachment":
            self.attachments[(params[0], params[1])] = params[2].data
        elif method == "deleteAttachment":
            del self.attachments[(params[0], params[1])]
        else:
            return xc.Fault(400, "Unknown method")
        return True


@pytest.fixture
def wiki():
    class Handler(SimpleXMLRPCRequestHandler):
        rpc_paths = ("/", "/wiki", "/wiki/")

        def is_rpc_path_valid(self):
            return self.path.split("?", 1)[0] in self.rpc_paths

    instance = Wiki()
    with SimpleXMLRPCServer(
        ("127.0.0.1", 0), requestHandler=Handler, allow_none=True, logRequests=False
    ) as server:
        instance.url = f"http://127.0.0.1:{server.server_address[1]}/wiki"
        server.register_instance(instance)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance
        finally:
            server.shutdown()
            thread.join(timeout=5)
