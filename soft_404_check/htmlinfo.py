"""Small HTML facts needed to judge a 404 page: links, forms, title, h1, layout."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

REGIONS = ("header", "nav", "footer")
SEARCH_NAMES = {"q", "s", "search", "query", "keyword", "keywords", "k", "term"}
NOT_FOUND_RE = re.compile(
    r"\b404\b|not\s+found|does(?:n'?t| not)\s+exist|can(?:'?t|not)\s+be\s+found|"
    r"couldn'?t\s+(?:find|be\s+found)|can'?t\s+find|no\s+longer\s+(?:exists|available)|"
    r"introuvable|n'?existe\s+pas|non\s+trouv|no\s+encontrad|no\s+existe|p[a\u00e1]gina\s+no",
    re.I)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.links: list[tuple[str, bool]] = []  # (href, inside header/nav/footer)
        self.stylesheets: list[str] = []
        self.meta_robots = ""
        self.has_search = False
        self.forms = 0
        self.canonical = ""
        self._in_title = False
        self._in_h1 = False
        self._h1_done = False
        self._region = 0
        self._form_stack: list[dict] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in REGIONS:
            self._region += 1
        if a.get("role", "").lower() == "search":
            self.has_search = True
        if tag == "title" and not self.title:
            self._in_title = True
        elif tag == "h1" and not self._h1_done:
            self._in_h1 = True
        elif tag == "a" and a.get("href"):
            self.links.append((a["href"], self._region > 0))
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "stylesheet" in rel and a.get("href"):
                self.stylesheets.append(a["href"])
            elif "canonical" in rel:
                self.canonical = a.get("href", "")
        elif tag == "meta" and a.get("name", "").lower() == "robots":
            self.meta_robots += " " + a.get("content", "")
        elif tag == "form":
            self.forms += 1
            hint = " ".join((a.get("id", ""), a.get("class", ""), a.get("action", ""))).lower()
            if "search" in hint:
                self.has_search = True
        elif tag == "input":
            kind = a.get("type", "text").lower()
            name = a.get("name", "").lower()
            if kind == "search" or (self.forms and name in SEARCH_NAMES and kind in ("text", "")):
                self.has_search = True

    def handle_endtag(self, tag: str) -> None:
        if tag in REGIONS and self._region > 0:
            self._region -= 1
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            self._h1_done = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._in_h1:
            self.h1 += data


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _charset(content_type: str, head: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.I) or re.search(rb"charset=[\"']?([\w-]+)", head[:2048], re.I)
    if not m:
        return "utf-8"
    val = m.group(1)
    return val.decode("ascii", "ignore") if isinstance(val, bytes) else val


def strip_url(url: str) -> str:
    """URL without fragment and query, for comparisons."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path or "/", "", ""))


class Page:
    """Parsed HTML page. `base` is the URL it was fetched from."""

    def __init__(self, body: bytes, base: str, content_type: str = "text/html") -> None:
        try:
            text = body.decode(_charset(content_type, body), "replace")
        except LookupError:
            text = body.decode("utf-8", "replace")
        p = _Parser()
        try:
            p.feed(text)
            p.close()
        except Exception:  # html.parser is lenient; never let odd markup abort a run
            pass
        self.base = base
        self.title = _clean(p.title)
        self.h1 = _clean(p.h1)
        self.meta_robots = _clean(p.meta_robots).lower()
        self.has_search = p.has_search
        self.canonical = p.canonical
        self.links: list[str] = []
        self.region_links: set[str] = set()
        for href, in_region in p.links:
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(base, href)
            if urlsplit(absolute).scheme not in ("http", "https"):
                continue
            self.links.append(absolute)
            if in_region:
                self.region_links.add(strip_url(absolute))
        self.stylesheets = {strip_url(urljoin(base, s)) for s in p.stylesheets}

    @property
    def noindex(self) -> bool:
        return "noindex" in self.meta_robots or "none" in self.meta_robots.split()

    @property
    def says_not_found(self) -> bool:
        return bool(NOT_FOUND_RE.search(self.title) or NOT_FOUND_RE.search(self.h1))


def is_html(content_type: str, body: bytes) -> bool:
    if content_type in ("text/html", "application/xhtml+xml"):
        return True
    return not content_type and body.lstrip()[:1] == b"<"


def same_layout(page: Page, home: Page) -> bool:
    """Shared stylesheet, or mostly the same links in header/nav/footer."""
    if page.stylesheets & home.stylesheets:
        return True
    common = page.region_links & home.region_links
    smaller = min(len(page.region_links), len(home.region_links))
    return len(common) >= 2 and len(common) >= 0.5 * smaller
