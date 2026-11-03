"""A real HTTP server on 127.0.0.1 that plays the API server in the pass-through tests.

`/echo...` answers with what it received, as JSON: the method, the raw path with
its query string, every header (as a list, so repeats show) and the body as base64.
The other paths answer in ways a real API server might.
"""
from __future__ import annotations

import base64
import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
GZIPPED_TEXT = "compressed upstream text " * 20


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # keep test output quiet
        pass

    def _reply(self, status, body=b"", headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        if status not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD" and status not in (204, 304):
            self.wfile.write(body)

    def _any(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        route = self.path.split("?")[0]

        if route.startswith("/status/"):
            status = int(route.split("/")[2])
            headers = [("X-Upstream", "yes"), ("Allow", "GET"), ("Vary", "Origin")]
            if status not in (204, 304):
                headers.append(("Content-Type", "text/plain; charset=utf-8"))
            self._reply(status, f"upstream says {status}".encode(), headers)
        elif route == "/png":
            self._reply(200, PNG, [("Content-Type", "image/png")])
        elif route == "/gzip":
            self._reply(200, gzip.compress(GZIPPED_TEXT.encode()), [
                ("Content-Type", "text/plain"), ("Content-Encoding", "gzip"),
            ])
        elif route == "/cookies":
            self._reply(200, b"{}", [
                ("Content-Type", "application/json"),
                ("Set-Cookie", "theme=dark; Path=/"),
                ("Set-Cookie", "session=abc123; Path=/; HttpOnly; SameSite=Lax"),
            ])
        elif route == "/hop":
            self._reply(200, b"{}", [
                ("Content-Type", "application/json"),
                ("Connection", "X-Hop-Secret"), ("X-Hop-Secret", "1"),
                ("Keep-Alive", "timeout=5"), ("X-End-To-End", "kept"),
            ])
        elif route == "/etag":
            if self.headers.get("If-None-Match") == '"v1"':
                self._reply(304, headers=[("ETag", '"v1"')])
            else:
                self._reply(200, b"fresh", [("Content-Type", "text/plain"), ("ETag", '"v1"')])
        elif route == "/redirect":
            self._reply(302, headers=[("Location", "/echo/after-redirect")])
        elif route == "/slow":
            time.sleep(1)
            self._reply(200, b"late", [("Content-Type", "text/plain")])
        else:
            echo = {
                "method": self.command,
                "path": self.path,
                "headers": [[name, value] for name, value in self.headers.items()],
                "body": base64.b64encode(body).decode(),
            }
            self._reply(200, json.dumps(echo).encode(), [("Content-Type", "application/json")])

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _any


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # e.g. the timeout test hangs up on /slow on purpose


class Upstream:
    """Start with `Upstream.start()`; `url` is its address, `stop()` shuts it down."""

    def __init__(self):
        self.server = _QuietServer(("127.0.0.1", 0), _Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @classmethod
    def start(cls):
        upstream = cls()
        upstream.thread.start()
        return upstream

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def echoed(response):
    """Decode an `/echo` reply: method, path, headers (dict of lists) and body bytes."""
    data = json.loads(response.content)
    headers = {}
    for name, value in data["headers"]:
        headers.setdefault(name.lower(), []).append(value)
    return data["method"], data["path"], headers, base64.b64decode(data["body"])
