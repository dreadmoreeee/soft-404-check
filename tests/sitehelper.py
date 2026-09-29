"""Local HTTP servers for the tests: random free port, one thread, request log."""
from __future__ import annotations

import gzip
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME_HTML = """<!doctype html><html><head><title>Example Studio</title>
<link rel="stylesheet" href="/style.css"></head><body>
<header><a href="/">Example Studio</a></header>
<nav><a href="/blog/">Blog</a> <a href="/docs/">Docs</a> <a href="/about">About</a> <a href="/contact">Contact</a></nav>
<h1>Welcome</h1><p><a href="/blog/post-1">First post</a></p>
<footer><a href="/privacy">Privacy</a> <a href="/about">About</a></footer></body></html>"""

HELPFUL_404 = """<!doctype html><html><head><title>Page not found - Example Studio</title>
<link rel="stylesheet" href="/style.css"></head><body>
<header><a href="/">Example Studio</a></header>
<nav><a href="/blog/">Blog</a> <a href="/docs/">Docs</a> <a href="/about">About</a> <a href="/contact">Contact</a></nav>
<h1>404: page not found</h1>
<form action="/search" role="search"><input type="search" name="q"></form>
<p><a href="/">Back to the homepage</a></p>
<footer><a href="/privacy">Privacy</a> <a href="/about">About</a></footer></body></html>"""

BARE_404 = "<html><head><title>Error</title></head><body><p>Nothing here.</p></body></html>"


def page(status=200, body="", headers=None, ctype="text/html; charset=utf-8"):
    return status, dict(headers or {}, **{"Content-Type": ctype}), body


def redirect(location, status=301):
    return status, {"Location": location, "Content-Type": "text/plain"}, ""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        srv = self.server
        srv.log.append({"path": self.path, "ua": self.headers.get("User-Agent"), "t": time.monotonic(),
                        "method": "GET"})
        result = srv.app(self.path)
        status, headers, body = result
        data = body.encode("utf-8") if isinstance(body, str) else body
        if headers.pop("_gzip", None):
            data = gzip.compress(data)
            headers["Content-Encoding"] = "gzip"
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@contextmanager
def serve(app):
    """`app(path) -> (status, headers, body)`. Yields (base_url, request_log)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.app = app
    srv.log = []
    thread = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", srv.log
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def base_routes(path):
    """The pages every test site shares."""
    path = path.split("?", 1)[0]
    if path == "/":
        return page(200, HOME_HTML)
    if path == "/style.css":
        return page(200, "body{}", ctype="text/css")
    if path in ("/blog/", "/docs/"):
        return page(200, "<html><title>Section</title><body>ok</body></html>")
    if path in ("/blog/post-1", "/about", "/contact", "/privacy", "/new-page", "/target", "/search"):
        return page(200, f"<html><title>{path}</title><body><h1>{path}</h1></body></html>")
    if path == "/robots.txt":
        return page(200, "User-agent: *\nDisallow: /private/\n", ctype="text/plain")
    return None


def make_app(missing, extra=None):
    """Site with the standard pages; `missing(path)` answers everything else."""
    def app(path):
        if extra:
            r = extra(path)
            if r is not None:
                return r
        r = base_routes(path)
        return r if r is not None else missing(path)
    return app


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
