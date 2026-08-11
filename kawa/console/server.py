"""Stdlib HTTP server for the Operator Console (no third-party web dependency)."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kawa.console.render import render_page
from kawa.storage.db import connect


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in ("/", "/index.html"):
            self.send_error(404); return
        try:
            with connect() as conn:          # a fresh read per request; projections are live
                page = render_page(conn).encode("utf-8")
        except Exception as exc:  # pragma: no cover - surfaced to the operator, never a blank page
            self.send_error(500, "projection read failed", str(exc)); return  # ASCII reason; utf-8 detail in body (ja_JP locale)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *a: object) -> None:  # quiet
        pass


def serve(host: str = "127.0.0.1", port: int = 8099) -> None:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"Kawa Console on http://{host}:{port}  (reads live projections; Ctrl-C to stop)")
    httpd.serve_forever()
