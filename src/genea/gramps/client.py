"""Gramps Web HTTP client: one user, one tree, ETag-guarded writes. Tokens live in memory only."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from genea.config import Settings
from genea.gramps.credentials import get_password

MIN_API_VERSION = (3, 22, 0)
PAGE_SIZE = 500
TIMEOUT = 30.0


class GrampsError(Exception):
    """A Gramps Web request failed or was refused."""


class Conflict(GrampsError):
    """The object changed on the server (HTTP 412), or an undo no longer applies."""


class TooOld(GrampsError):
    """The server's API is older than MIN_API_VERSION."""


class WrongTree(GrampsError):
    """The login reached a different tree than the one pinned for this user."""


def tree_for(settings: Settings, ged_file: str) -> str:
    """The Gramps Web user mapped to a KB-relative .ged."""
    user = settings.gramps_web_trees.get(ged_file)
    if not user:
        raise GrampsError(
            f"{ged_file} is not mapped to a Gramps Web tree; add it under [gramps_web_trees] "
            f"in the KB config"
        )
    return user


def _claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise GrampsError("Gramps Web returned an access token that cannot be read") from e


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split(".")[:3] if p.isdigit())


class GrampsClient:
    def __init__(self, url: str, user: str, password: str, state_file: Path,
                 transport: httpx.BaseTransport | None = None):
        self.user = user
        self._password = password
        self.state_file = state_file
        self._http = httpx.Client(base_url=url.rstrip("/"), transport=transport, timeout=TIMEOUT)
        self._access = ""
        self._refresh = ""
        self._claims: dict = {}
        self._version = ""

    def __enter__(self) -> GrampsClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _send(self, method: str, path: str, *, auth: bool = True, retry: bool = True, **kw) -> httpx.Response:
        headers = dict(kw.pop("headers", {}))
        if auth and self._access:
            headers["Authorization"] = f"Bearer {self._access}"
        try:
            resp = self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise GrampsError(f"{method} {path}: {type(e).__name__}") from e
        if resp.status_code == 401 and auth and retry and self._refresh:
            self._do_refresh()
            headers.pop("Authorization", None)
            return self._send(method, path, retry=False, headers=headers, **kw)
        return resp

    def _do_refresh(self) -> None:
        path = "/api/token/refresh/"
        resp = self._send("POST", path, auth=False, retry=False,
                          headers={"Authorization": f"Bearer {self._refresh}"})
        self._check(resp, "POST", path)
        self._access = resp.json()["access_token"]

    @staticmethod
    def _message(resp: httpx.Response) -> str:
        try:
            return str(resp.json()["error"]["message"])
        except (ValueError, KeyError, TypeError):
            return resp.reason_phrase or ""

    def _check(self, resp: httpx.Response, method: str, path: str) -> httpx.Response:
        if resp.status_code >= 400:
            raise GrampsError(f"{method} {path}: {resp.status_code} {self._message(resp)}")
        return resp

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        return self._check(self._send(method, path, **kw), method, path)

    def login(self) -> str:
        path = "/api/token/"
        resp = self._send("POST", path, auth=False,
                          json={"username": self.user, "password": self._password})
        body = self._check(resp, "POST", path).json()
        self._access, self._refresh = body["access_token"], body.get("refresh_token", "")
        self._claims = _claims(self._access)
        tree = str(self._claims.get("tree", ""))
        meta = self._call("GET", "/api/metadata/").json()
        self._version = str(meta.get("gramps_webapi", {}).get("version", ""))
        if _version(self._version) < MIN_API_VERSION:
            need = ".".join(map(str, MIN_API_VERSION))
            raise TooOld(f"Gramps Web API {self._version or 'unknown'} is too old: need {need} or newer")
        self._pin(tree)
        return tree

    def _pin(self, tree: str) -> None:
        pinned = {}
        if self.state_file.exists():
            pinned = json.loads(self.state_file.read_text(encoding="utf-8"))
        if self.user in pinned:
            if pinned[self.user] != tree:
                raise WrongTree(
                    f"User '{self.user}' reached tree {tree}, but it is pinned to {pinned[self.user]}"
                )
            return
        pinned[self.user] = tree
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(pinned), encoding="utf-8")

    def info(self) -> dict:
        return {"tree": self._claims.get("tree", ""), "version": self._version,
                "permissions": list(self._claims.get("permissions", []))}

    def get(self, kind: str, handle: str) -> tuple[dict, str]:
        resp = self._call("GET", f"/api/{kind}/{handle}")
        return resp.json(), resp.headers.get("ETag", "").strip('"')

    def find_by_gramps_id(self, kind: str, gramps_id: str) -> dict | None:
        rows = self._call("GET", f"/api/{kind}/", params={"gramps_id": gramps_id}).json()
        return rows[0] if rows else None

    def list_all(self, kind: str, keys: str) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            rows = self._call("GET", f"/api/{kind}/",
                              params={"keys": keys, "page": page, "pagesize": PAGE_SIZE}).json()
            out.extend(rows)
            if len(rows) < PAGE_SIZE:
                return out
            page += 1

    def create(self, kind: str, obj: dict) -> list[dict]:
        return self._call("POST", f"/api/{kind}/", json=obj).json()

    def update(self, kind: str, handle: str, obj: dict, etag: str) -> list[dict]:
        path = f"/api/{kind}/{handle}"
        resp = self._send("PUT", path, json=obj, headers={"If-Match": f'"{etag}"'})
        if resp.status_code == 412:
            raise Conflict(f"PUT {path}: the object changed on the server")
        return self._check(resp, "PUT", path).json()

    def delete(self, kind: str, handle: str) -> None:
        self._call("DELETE", f"/api/{kind}/{handle}")

    def latest_transaction(self, obj_class: str, handle: str) -> int:
        rows = self._call("GET", f"/api/transactions/history/objects/{obj_class}/{handle}",
                          params={"sort": "-id", "pagesize": 1}).json()
        if not rows:
            raise GrampsError(f"No transaction found for {obj_class} {handle}")
        return rows[0]["id"]

    def undo(self, transaction_id: int) -> None:
        path = f"/api/transactions/history/{transaction_id}/undo"
        resp = self._send("POST", path)
        if resp.status_code in (409, 412, 422):
            raise Conflict(f"POST {path}: {resp.status_code} {self._message(resp)}")
        self._check(resp, "POST", path)

    def export(self, extension: str) -> bytes:
        return self._call("GET", f"/api/exporters/{extension}/file").content


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    raise GrampsError(f"gramps_web_url must be an https:// URL (http:// only for localhost), got '{url}'")


def open_client(kb, ged_file: str, transport: httpx.BaseTransport | None = None) -> GrampsClient:
    """Log in with the user mapped to ged_file; the caller closes the client."""
    url = kb.settings.gramps_web_url
    if not url:
        raise GrampsError("gramps_web_url is not set in the KB config")
    _check_url(url)
    user = tree_for(kb.settings, ged_file)
    client = GrampsClient(url, user, get_password(user), kb.meta_dir / "gramps_web.json", transport)
    client.login()
    return client
