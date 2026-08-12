"""Stdlib HTTP server for the Operator Console (no third-party web dependency).

Routes each path to a screen via `render(conn, path)`; an unknown path is a 404. A fresh read per
request — projections are live. Reads only.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kawa.console.render import render
from kawa.storage.db import connect


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs
        path, _, qs = self.path.partition("?")
        if path == "/index.html":
            path = "/"
        try:
            with connect() as conn:            # a fresh read per request; projections are live
                page = render(conn, path, parse_qs(qs) if qs else None)
        except Exception as exc:  # pragma: no cover - surfaced to the operator, never a blank page
            # ASCII reason phrase (HTTP status line is latin-1); detail goes in the utf-8 body.
            self.send_error(500, "projection read failed", str(exc))
            return
        if page is None:
            self.send_error(404, "no such screen")
            return
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:  # quiet
        pass


def serve(host: str = "127.0.0.1", port: int = 8099) -> None:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"Kawa Console on http://{host}:{port}  (reads live projections; Ctrl-C to stop)")
    httpd.serve_forever()
