"""An in-memory stand-in for the Gramps Web API, served through httpx.MockTransport."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import uuid

import httpx

KINDS = {"people": "Person", "families": "Family", "events": "Event", "notes": "Note",
         "citations": "Citation", "places": "Place", "sources": "Source"}
CLASS_TO_KIND = {v: k for k, v in KINDS.items()}


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def etag_of(obj: dict) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


class FakeGramps:
    def __init__(self):
        self.tree = "tree-a"
        self.omit_tree_claim = False
        self.html_on: set[tuple[str, str]] = set()  # (method, path prefix) -> 200 with an HTML body
        self.empty_post_response = False  # POST answers 201 with []
        self.version = "3.22.0"
        self.permissions = ["ViewPrivate", "AddObject", "EditObject", "DeleteObject"]
        self.objects: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        self.history: list[dict] = []
        self.exports: dict[str, bytes] = {}
        self.fail_on: dict[tuple[str, str], int] = {}  # (method, path prefix) -> status, returned once
        self.requests: list[tuple[str, str]] = []
        self._tokens = 0
        self.transport = httpx.MockTransport(self._handle)

    def add(self, kind: str, obj: dict) -> str:
        handle = obj.get("handle") or uuid.uuid4().hex
        self.objects[kind][handle] = {**obj, "handle": handle}
        return handle

    def _record(self, kind: str, handle: str, old: dict | None, new: dict | None) -> int:
        tid = len(self.history) + 1
        self.history.append({"id": tid, "obj_class": KINDS[kind], "handle": handle, "old": copy.deepcopy(old), "new": copy.deepcopy(new)})
        return tid

    def _token(self) -> str:
        self._tokens += 1
        claims = {"tree": self.tree, "permissions": self.permissions, "n": self._tokens}
        if self.omit_tree_claim:
            del claims["tree"]
        return f"{_b64({'alg': 'none'})}.{_b64(claims)}.sig"

    @staticmethod
    def _json(status: int, body, headers: dict | None = None) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        resp = self._dispatch(request)
        if resp.status_code < 400 and any(request.method == m and request.url.path.startswith(p)
                                          for m, p in self.html_on):  # applied, but the body is not JSON
            return httpx.Response(resp.status_code, text="<html>proxy</html>", headers={"content-type": "text/html"})
        return resp

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.requests.append((method, path))
        for (m, prefix), status in list(self.fail_on.items()):
            if m == method and path.startswith(prefix):
                del self.fail_on[(m, prefix)]
                return self._json(status, {"error": {"message": f"injected {status}"}})
        if path == "/api/token/":
            body = json.loads(request.content)
            if not body.get("username") or body.get("password") != "pw":
                return self._json(403, {"error": {"message": "Invalid credentials"}})
            return self._json(200, {"access_token": self._token(), "refresh_token": "refresh-1"})
        if path == "/api/token/refresh/":
            return self._json(200, {"access_token": self._token()})
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return self._json(401, {"error": {"message": "Unauthorized"}})
        if path == "/api/metadata/":
            return self._json(200, {"gramps_webapi": {"version": self.version}})
        if m := re.fullmatch(r"/api/exporters/(\w+)/file", path):
            data = self.exports.get(m.group(1))
            if data is None:
                return self._json(404, {"error": {"message": "no such exporter"}})
            return httpx.Response(200, content=data)
        if m := re.fullmatch(r"/api/transactions/history/objects/(\w+)/(\w+)", path):
            rows = [h for h in self.history if h["obj_class"] == m.group(1) and h["handle"] == m.group(2)]
            rows.sort(key=lambda h: -h["id"])
            size = int(request.url.params.get("pagesize", len(rows) or 1))
            return self._json(200, [{"id": h["id"]} for h in rows[:size]])
        if m := re.fullmatch(r"/api/transactions/history/(\d+)/undo", path):
            return self._undo(int(m.group(1)))
        if (m := re.fullmatch(r"/api/(\w+)/", path)) and m.group(1) in KINDS:
            return self._collection(request, m.group(1))
        if (m := re.fullmatch(r"/api/(\w+)/(\w+)", path)) and m.group(1) in KINDS:
            return self._item(request, m.group(1), m.group(2))
        return self._json(404, {"error": {"message": "not found"}})

    def _collection(self, request: httpx.Request, kind: str) -> httpx.Response:
        if request.method == "POST":
            obj = json.loads(request.content)
            if not obj.get("gramps_id"):  # Gramps Web assigns the next id, like Gramps itself
                obj["gramps_id"] = f"{KINDS[kind][0]}{len(self.objects[kind]) + 1000:04d}"
            if kind == "places" and "profile" not in obj:  # Gramps Web derives the profile from the name
                obj["profile"] = {"name": (obj.get("name") or {}).get("value") or obj.get("title", "")}
            handle = self.add(kind, obj)
            tid = self._record(kind, handle, None, self.objects[kind][handle])
            if self.empty_post_response:
                return self._json(201, [])
            return self._json(201, [{"type": "add", "handle": handle, "_class": KINDS[kind], "trans_id": tid}])
        params = request.url.params
        items = list(self.objects[kind].values())
        if "gramps_id" in params:
            items = [o for o in items if o.get("gramps_id") == params["gramps_id"]]
        size = int(params.get("pagesize", 0)) or len(items) or 1
        page = int(params.get("page", 1))
        items = items[(page - 1) * size:page * size]
        if "keys" in params:
            keys = params["keys"].split(",")
            items = [{k: o[k] for k in keys if k in o} for o in items]
        return self._json(200, items)

    def _item(self, request: httpx.Request, kind: str, handle: str) -> httpx.Response:
        current = self.objects[kind].get(handle)
        if current is None:
            return self._json(404, {"error": {"message": "Object not found"}})
        if request.method == "GET":
            return self._json(200, current, {"ETag": f'"{etag_of(current)}"'})
        if request.method == "PUT":
            if request.headers.get("if-match", "").strip('"') != etag_of(current):
                return self._json(412, {"error": {"message": "Precondition failed"}})
            new = {**json.loads(request.content), "handle": handle}
            self.objects[kind][handle] = new
            tid = self._record(kind, handle, current, new)
            return self._json(200, [{"type": "update", "handle": handle, "_class": KINDS[kind], "trans_id": tid}])
        if request.method == "DELETE":
            del self.objects[kind][handle]
            tid = self._record(kind, handle, current, None)
            return self._json(200, [{"type": "delete", "handle": handle, "_class": KINDS[kind], "trans_id": tid}])
        return self._json(405, {"error": {"message": "method not allowed"}})

    def _undo(self, tid: int) -> httpx.Response:
        row = next((h for h in self.history if h["id"] == tid), None)
        if row is None:
            return self._json(404, {"error": {"message": "Transaction not found"}})
        kind = CLASS_TO_KIND[row["obj_class"]]
        current = self.objects[kind].get(row["handle"])
        if current != row["new"]:
            return self._json(409, {"error": {"message": "Object changed since the transaction"}})
        if row["old"] is None:
            del self.objects[kind][row["handle"]]
        else:
            self.objects[kind][row["handle"]] = row["old"]
        self._record(kind, row["handle"], current, row["old"])
        return self._json(200, [])
