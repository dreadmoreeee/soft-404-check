"""Minimal HTTP client: one GET per hop, redirects followed by hand, polite delay."""
from __future__ import annotations

import http.client
import ssl
import time
import zlib
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from . import __version__

USER_AGENT = f"soft-404-check/{__version__} (+https://github.com/dreadmoreeee/soft-404-check)"
MAX_BODY = 512 * 1024
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class FetchError(Exception):
    """Network-level failure: DNS, connection, TLS, timeout."""


@dataclass
class Response:
    url: str
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes
    elapsed: float  # seconds until the body was read
    truncated: bool = False

    @property
    def location(self) -> str | None:
        return self.headers.get("location")

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()


@dataclass
class Chain:
    """Every request made while following redirects by hand; the last one is `final`."""
    responses: list[Response] = field(default_factory=list)
    error: str | None = None  # "loop", "too-many", "no-location", "bad-scheme"

    @property
    def final(self) -> Response:
        return self.responses[-1]

    @property
    def redirected(self) -> bool:
        return len(self.responses) > 1

    @property
    def elapsed(self) -> float:
        return sum(r.elapsed for r in self.responses)


def _target(parts) -> str:
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%:@!$&'()*+,;=-._~?")
    return path


def _decode(data: bytes, encoding: str) -> bytes:
    encoding = encoding.lower().strip()
    try:
        if encoding == "gzip":
            return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data)
        if encoding == "deflate":
            return zlib.decompressobj().decompress(data)
    except zlib.error:
        pass
    return data


class Client:
    def __init__(self, user_agent: str = USER_AGENT, delay: float = 1.0, timeout: float = 10.0,
                 verify: bool = True, loopback_exempt: bool = True) -> None:
        self.user_agent = user_agent
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self.loopback_exempt = loopback_exempt
        self.requests = 0
        self._last_end: float | None = None
        self._ctx = ssl.create_default_context()
        if not verify:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _wait(self, host: str) -> None:
        if self._last_end is None:
            return
        if self.loopback_exempt and host in LOOPBACK:
            return
        gap = self.delay - (time.monotonic() - self._last_end)
        if gap > 0:
            time.sleep(gap)

    def get(self, url: str) -> Response:
        """One GET, no redirect following. Raises FetchError on network problems."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError(f"unsupported URL: {url}")
        host = parts.hostname
        self._wait(host)
        self.requests += 1
        port = parts.port
        started = time.monotonic()
        try:
            if parts.scheme == "https":
                conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=self._ctx)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
            try:
                conn.request("GET", _target(parts), headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                    "Accept-Encoding": "gzip",
                    "Connection": "close",
                })
                resp = conn.getresponse()
                raw = resp.read(MAX_BODY + 1)
                headers: dict[str, str] = {}
                for k, v in resp.getheaders():
                    k = k.lower()
                    headers[k] = f"{headers[k]}, {v}" if k in headers else v
                status, reason = resp.status, resp.reason
            finally:
                conn.close()
        except (OSError, http.client.HTTPException, ssl.SSLError, UnicodeError) as e:
            raise FetchError(f"{url}: {e.__class__.__name__}: {e}") from e
        finally:
            self._last_end = time.monotonic()
        truncated = len(raw) > MAX_BODY
        body = _decode(raw[:MAX_BODY], headers.get("content-encoding", ""))
        return Response(url=url, status=status, reason=reason, headers=headers, body=body,
                        elapsed=time.monotonic() - started, truncated=truncated)

    def chain(self, url: str, max_redirects: int = 5) -> Chain:
        """GET `url` and follow redirects manually, recording every hop."""
        chain = Chain()
        seen = {url}
        current = url
        while True:
            resp = self.get(current)
            chain.responses.append(resp)
            if resp.status not in (301, 302, 303, 307, 308):
                return chain
            loc = resp.location
            if not loc:
                chain.error = "no-location"
                return chain
            nxt = urljoin(current, loc.strip())
            nxt = urlunsplit(urlsplit(nxt)._replace(fragment=""))
            if urlsplit(nxt).scheme not in ("http", "https"):
                chain.error = "bad-scheme"
                return chain
            if nxt in seen:
                chain.error = "loop"
                return chain
            if len(chain.responses) > max_redirects:
                chain.error = "too-many"
                return chain
            seen.add(nxt)
            current = nxt
