"""Read-only localhost HTTP server: /v/<path> shows a cited source, /raw/<path> its file."""

from __future__ import annotations

import html
import json
import mimetypes
import sqlite3
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from genea.index.db import connect_readonly
from genea.kb import KnowledgeBase
from genea.viewer.policy import is_excluded

NO_INDEX = ("The index is missing or is being built. Run <code>genea index</code> "
            "and reload this page.")


@dataclass(frozen=True)
class FileRow:
    id: int
    path: str
    kind: str
    status: str


@dataclass
class ViewerHandle:
    url: str
    server: ThreadingHTTPServer

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def notice_page(title: str, message_html: str) -> bytes:
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<h1>{html.escape(title)}</h1><p>{message_html}</p>").encode("utf-8")


def lookup(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str) -> FileRow | None:
    """The indexed, readable, non-excluded file at this KB-relative path, else None."""
    try:
        path = kb.resolve_inside(rel)
    except (ValueError, OSError):
        return None
    if not path.is_file():
        return None
    row = conn.execute("SELECT id, path, kind, status FROM files WHERE path = ?",
                       (kb.rel(path),)).fetchone()
    if row is None or row["status"] != "ok":
        return None
    file = FileRow(row["id"], row["path"], row["kind"], row["status"])
    if file.kind == "image":
        for (keywords,) in conn.execute("SELECT keywords FROM images WHERE file_id = ?", (file.id,)):
            try:
                tags = json.loads(keywords)
            except ValueError:
                tags = [keywords]
            if is_excluded(tags):
                return None
    return file


def _render_page(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str,
                 params: dict[str, list[str]]) -> bytes:
    from genea.viewer import pages
    first = {key: values[0] for key, values in params.items() if values}
    return pages.render(kb, conn, rel, first).encode("utf-8")


def make_handler(kb: KnowledgeBase, hosts_of) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "genea-viewer"

        def log_message(self, format, *args):  # noqa: A002 - silent on purpose
            pass

        def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8",
                  inline: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if inline:
                self.send_header("Content-Disposition", "inline")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _not_found(self) -> None:
            self._send(404, notice_page("Not found", "This file is not available in the viewer."))

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._get()
            except Exception as exc:  # keep serving whatever happens
                try:
                    self._send(500, f"Viewer error: {type(exc).__name__}".encode(),
                               "text/plain; charset=utf-8")
                except OSError:
                    pass

        def _method_not_allowed(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _method_not_allowed  # noqa: N815

        def _get(self) -> None:
            if self.headers.get("Host", "") not in hosts_of():
                self._send(403, notice_page("Forbidden", "Unexpected Host header."))
                return
            parts = urlsplit(self.path)
            route, _, encoded = parts.path.lstrip("/").partition("/")
            if route not in ("v", "raw") or not encoded:
                self._not_found()
                return
            rel = unquote(encoded)
            if not kb.index_path.exists():
                self._send(503, notice_page("Index not ready", NO_INDEX))
                return
            try:
                conn = connect_readonly(kb.index_path)
            except sqlite3.Error:
                self._send(503, notice_page("Index not ready", NO_INDEX))
                return
            try:
                try:
                    file = lookup(kb, conn, rel)
                except sqlite3.Error:
                    self._send(503, notice_page("Index not ready", NO_INDEX))
                    return
                if file is None:
                    self._not_found()
                elif route == "raw":
                    ctype = mimetypes.guess_type(file.path)[0] or "application/octet-stream"
                    self._send(200, kb.resolve_inside(file.path).read_bytes(), ctype, inline=True)
                else:
                    self._send(200, _render_page(kb, conn, file.path, parse_qs(parts.query)))
            finally:
                conn.close()

    return Handler


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows reuse would let two viewers share one port
    daemon_threads = True


def start_viewer(kb: KnowledgeBase, port: int | None = None) -> ViewerHandle:
    """Serve on 127.0.0.1 in a daemon thread; falls back to a free port if the wanted one is taken."""
    wanted = kb.settings.viewer_port if port is None else port
    state: dict[str, int] = {"port": 0}
    handler = make_handler(kb, lambda: {f"127.0.0.1:{state['port']}", f"localhost:{state['port']}"})
    try:
        server = _Server(("127.0.0.1", wanted), handler)
    except OSError:
        server = _Server(("127.0.0.1", 0), handler)
    state["port"] = server.server_address[1]
    threading.Thread(target=server.serve_forever, name="genea-viewer", daemon=True).start()
    return ViewerHandle(f"http://127.0.0.1:{state['port']}", server)
