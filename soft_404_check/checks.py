"""The checks: probe non-existent URLs on a site, or verify a list of removed URLs."""
from __future__ import annotations

import random
import re
from collections import Counter
from urllib.parse import urljoin, urlsplit

from .fetch import Chain, Client, FetchError, Response
from .htmlinfo import Page, is_html, same_layout, strip_url
from .rfc9309 import RobotsTxt

PASS, WARN, FAIL = "pass", "warn", "fail"
NOT_FOUND = (404, 410)
REDIRECTS = (301, 302, 303, 307, 308)
LANG_RE = re.compile(r"^/[a-z]{2}(?:-[a-z]{2})?$", re.I)
CACHE_HEADERS = ("cache-control", "expires", "age", "cf-cache-status", "x-cache",
                 "cdn-cache-status", "x-vercel-cache")
LONG_CACHE_SECONDS = 86400
ASSET_SEGMENTS = {"static", "assets", "asset", "css", "js", "img", "images", "fonts", "media", "public",
                  "dist", "build", "wp-content", "wp-includes", "_next", "uploads", "files", "cdn-cgi"}
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def host_key(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def norm_path(path: str) -> str:
    return (path or "/").rstrip("/") or "/"


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


class Home:
    """Recognises the homepage: '/', where '/' finally lands (say '/en/'), or a bare language root."""

    def __init__(self, url: str, landed: str | None = None) -> None:
        self.host = host_key(urlsplit(url).hostname or "")
        self.paths = {"/"}
        if landed and host_key(urlsplit(landed).hostname or "") == self.host:
            self.paths.add(norm_path(urlsplit(landed).path))

    def is_home(self, url: str) -> bool:
        p = urlsplit(url)
        if host_key(p.hostname or "") != self.host:
            return False
        path = norm_path(p.path)
        return path in self.paths or bool(LANG_RE.match(path))


def random_name(rng: random.Random, prefix: str = "s404-") -> str:
    return prefix + "".join(rng.choice(ALPHABET) for _ in range(12))


def max_age(cache_control: str) -> int | None:
    """Longest s-maxage / max-age in a Cache-Control value, in seconds."""
    vals = [int(m) for m in re.findall(r"(?:s-maxage|max-age)\s*=\s*\"?(\d+)", cache_control, re.I)]
    return max(vals) if vals else None


def cache_info(resp: Response) -> dict[str, str]:
    return {h: resp.headers[h] for h in CACHE_HEADERS if h in resp.headers}


def robots_header_noindex(resp: Response) -> bool:
    return "noindex" in resp.headers.get("x-robots-tag", "").lower()


def chain_view(chain: Chain) -> list[dict]:
    return [{"url": r.url, "status": r.status, "location": r.location} for r in chain.responses]


# ---------------------------------------------------------------- 404 page quality

def analyse_page(resp: Response, home_page: Page | None, home: Home) -> dict | None:
    """Is the error page useful? None when the body is not HTML."""
    if not is_html(resp.content_type, resp.body):
        return None
    page = Page(resp.body, resp.url, resp.content_type)
    n_links = len({strip_url(u) for u in page.links})
    info = {
        "title": page.title,
        "h1": page.h1,
        "says_not_found": page.says_not_found,
        "links": n_links,
        "has_links": n_links >= 3,
        "search_form": page.has_search,
        "home_link": any(home.is_home(u) for u in page.links),
        "same_layout": bool(home_page) and same_layout(page, home_page),
        "noindex": page.noindex or robots_header_noindex(resp),
    }
    score = sum(bool(info[k]) for k in ("says_not_found", "has_links", "search_form", "home_link", "same_layout"))
    info["score"] = score
    info["verdict"] = "helpful" if score >= 4 else ("basic" if score >= 2 else "bare")
    return info


# ---------------------------------------------------------------- judging chains

def _normalising(requested: str, landed: str) -> bool:
    """Only a trailing slash, letter case, www or the scheme differ."""
    a, b = urlsplit(requested), urlsplit(landed)
    return host_key(a.hostname or "") == host_key(b.hostname or "") and \
        norm_path(a.path).lower() == norm_path(b.path).lower()


def judge_probe(chain: Chain, home: Home, final_page: dict | None = None) -> tuple[str, str, str]:
    """(verdict, code, message) for a request to a URL that does not exist."""
    final = chain.final
    if chain.error in ("loop", "too-many"):
        return FAIL, "redirect-" + chain.error, "redirect loop" if chain.error == "loop" else "too many redirects"
    if chain.error:
        return WARN, "redirect-" + chain.error, "unusable redirect (" + chain.error + ")"
    if not chain.redirected:
        s = final.status
        if s in NOT_FOUND:
            return PASS, "not-found", f"{s} {final.reason}".strip()
        if 200 <= s < 300:
            return FAIL, "soft-404", f"{s} for a URL that does not exist (soft 404)"
        if s in (401, 403, 429):
            return WARN, "blocked", f"{s}: the server refused, so its 404 behaviour is unknown"
        if 400 <= s < 500:
            return WARN, "other-4xx", f"{s} instead of 404"
        if s >= 500:
            return FAIL, "server-error", f"{s} on a URL that does not exist"
        return WARN, "other-status", f"unexpected status {s}"
    first = chain.responses[0]
    landed = final.url
    if host_key(urlsplit(landed).hostname or "") != home.host:
        return WARN, "redirect-offsite", f"{first.status} to another host ({urlsplit(landed).hostname})"
    if home.is_home(landed):
        return FAIL, "redirect-home", f"{first.status} redirect to the homepage ({landed})"
    if final.status in NOT_FOUND:
        if _normalising(first.url, landed):
            return PASS, "normalising-redirect", f"{first.status} to {landed}, then {final.status}"
        return WARN, "redirect-to-404", f"{first.status} to {landed}, which answers {final.status}"
    if 200 <= final.status < 300:
        if final_page and final_page["says_not_found"]:
            return FAIL, "soft-404", f"{first.status} to {landed}, a 200 page that says not found (soft 404)"
        return WARN, "redirect-to-page", f"{first.status} to {landed} (200); a missing URL should not redirect"
    if final.status >= 500:
        return FAIL, "server-error", f"redirect to {landed}, which answers {final.status}"
    return WARN, "redirect-other", f"redirect to {landed}, which answers {final.status}"


# ---------------------------------------------------------------- probe mode

def discover_section(home_page: Page | None, home_url: str, home: Home, robots: RobotsTxt | None,
                     user_agent: str) -> str | None:
    """A real section: a first path segment that has pages below it, not a file, not blocked."""
    if home_page is None:
        return None
    host = host_key(urlsplit(home_url).hostname or "")
    deep: Counter[str] = Counter()
    for link in home_page.links:
        p = urlsplit(link)
        if host_key(p.hostname or "") != host or p.path.startswith("/cdn-cgi/"):
            continue
        segs = [s for s in p.path.split("/") if s]
        if not segs or "." in segs[0] or segs[0].lower() in ASSET_SEGMENTS:
            continue
        if len(segs) >= 2 or p.path.endswith("/"):
            deep[segs[0]] += 1
    landed = [s for s in urlsplit(home_url).path.split("/") if s]
    candidates = [s for s, _ in sorted(deep.items(), key=lambda kv: (-kv[1], kv[0]))]
    if landed and landed[0] not in candidates and "." not in landed[0] and landed[0].lower() not in ASSET_SEGMENTS:
        candidates.append(landed[0])
    # a real section beats a language root such as /en/
    candidates.sort(key=lambda s: bool(LANG_RE.match("/" + s)))
    for seg in candidates:
        path = f"/{seg}/"
        if robots is None or robots.can_fetch(user_agent, path):
            return path
    return None


def probe_urls(origin: str, sections: list[str], exts: list[str], rng: random.Random) -> list[tuple[str, str]]:
    probes = [("top-level", f"{origin}/{random_name(rng)}")]
    for sec in sections:
        sec = "/" + sec.strip("/") + "/" if sec.strip("/") else "/"
        probes.append(("section", f"{origin}{sec}{random_name(rng)}"))
    for ext in exts:
        probes.append((f"extension .{ext}", f"{origin}/{random_name(rng)}.{ext}"))
    probes.append(("trailing slash", f"{origin}/{random_name(rng)}/"))
    return probes


def check_site(client: Client, site: str, sections: list[str] | None = None, exts: list[str] | None = None,
               rng: random.Random | None = None, max_redirects: int = 5, use_robots: bool = True) -> dict:
    """Probe one site. Raises FetchError when the homepage cannot be reached."""
    rng = rng or random.Random()
    origin = origin_of(site)
    result: dict = {"site": origin, "error": None}
    requests_before = client.requests
    hchain = client.chain(origin + "/", max_redirects)
    hfinal = hchain.final
    home = Home(origin, hfinal.url)
    home_page = Page(hfinal.body, hfinal.url, hfinal.content_type) if is_html(hfinal.content_type, hfinal.body) else None
    result["homepage"] = {
        "url": hfinal.url, "status": hfinal.status, "title": home_page.title if home_page else "",
        "chain": chain_view(hchain),
    }
    section_note = None
    if sections:
        chosen = list(sections)
    else:
        robots = None
        if use_robots and home_page is not None:
            rchain = client.chain(origin + "/robots.txt", max_redirects)
            rf = rchain.final
            if 200 <= rf.status < 300:
                robots = RobotsTxt(rf.body.decode("utf-8", "replace"))
            elif rf.status >= 500:
                robots = RobotsTxt("User-agent: *\nDisallow: /\n")
        found = discover_section(home_page, hfinal.url, home, robots, client.user_agent)
        chosen = [found] if found else []
        if not found:
            section_note = "no section found on the homepage (or robots.txt blocks them); section probe skipped"
    result["section_note"] = section_note

    probes = []
    for kind, url in probe_urls(origin, chosen, exts or ["html"], rng):
        chain = client.chain(url, max_redirects)
        final = chain.final
        page = analyse_page(final, home_page, home) if final.status in NOT_FOUND or 200 <= final.status < 300 else None
        verdict, code, message = judge_probe(chain, home, page if final.status not in NOT_FOUND else None)
        probes.append({
            "kind": kind, "url": url, "verdict": verdict, "code": code, "message": message,
            "chain": chain_view(chain), "final_url": final.url, "final_status": final.status,
            "size": len(final.body), "time_ms": round(chain.elapsed * 1000),
            "noindex": bool(page and page["noindex"]) or robots_header_noindex(final),
            "cache": cache_info(final) if final.status in NOT_FOUND else {},
            "page": page if final.status in NOT_FOUND else None,
        })
    result["probes"] = probes
    result["findings"] = site_findings(probes)
    result["result"] = overall(result["findings"])
    result["requests"] = client.requests - requests_before
    return result


def site_findings(probes: list[dict]) -> list[dict]:
    out = []
    for p in probes:
        if p["verdict"] != PASS:
            out.append({"level": p["verdict"], "code": p["code"], "probe": p["kind"], "message": f"{p['kind']}: {p['message']}"})
    not_found = [p for p in probes if p["final_status"] in NOT_FOUND]
    hopped = [p for p in probes if p["code"] == "normalising-redirect"]
    if hopped:
        out.append({"level": "info", "code": "404-after-redirect", "probe": hopped[0]["kind"],
                    "message": "a missing URL is first redirected (" + str(hopped[0]["chain"][0]["status"])
                    + ") to its trailing-slash or case variant and only then answers 404: "
                    + ", ".join(p["kind"] for p in hopped)})
    bare = [p for p in not_found if p["page"] and p["page"]["verdict"] == "bare"]
    if bare:
        out.append({"level": WARN, "code": "bare-404-page", "probe": bare[0]["kind"],
                    "message": "bare 404 page (" + ", ".join(p["kind"] for p in bare) + "): no links, search or navigation to help a visitor"})
    if not_found and all(p["page"] is None for p in not_found):
        out.append({"level": "info", "code": "404-not-html", "probe": not_found[0]["kind"],
                    "message": "the 404 responses are not HTML pages, so their helpfulness was not judged"})
    long_cached = []
    for p in not_found:
        age = max_age(p["cache"].get("cache-control", ""))
        if age is not None and age > LONG_CACHE_SECONDS:
            long_cached.append(f"{p['kind']} ({age} s)")
    if long_cached:
        out.append({"level": WARN, "code": "404-long-cache", "probe": "",
                    "message": "404 cacheable for more than a day: " + ", ".join(long_cached)})
    return out


def overall(findings: list[dict]) -> str:
    levels = {f["level"] for f in findings}
    if FAIL in levels:
        return FAIL
    return WARN if WARN in levels else PASS


# ---------------------------------------------------------------- --urls mode

def parse_url_list(text: str) -> list[tuple[str, str | None]]:
    """Lines of `URL [expected target]`; `#` comments, blank lines, `->` / `=>` separators ignored."""
    out = []
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*", "", raw).strip()
        tokens = [t for t in line.split() if t not in ("->", "=>")]
        if not tokens:
            continue
        out.append((tokens[0], tokens[1] if len(tokens) > 1 else None))
    return out


def _same_target(landed: str, expected: str) -> bool:
    a, b = urlsplit(landed), urlsplit(expected)
    if b.netloc and host_key(a.hostname or "") != host_key(b.hostname or ""):
        return False
    if norm_path(a.path) != norm_path(b.path):
        return False
    return not b.query or a.query == b.query


def judge_removed(chain: Chain, home: Home, expected: str | None, page: dict | None = None) -> tuple[str, str, str]:
    """Verdict for a URL that is known to be removed."""
    final = chain.final
    if chain.error in ("loop", "too-many"):
        return FAIL, "redirect-" + chain.error, "redirect loop" if chain.error == "loop" else "too many redirects"
    if chain.error:
        return FAIL, "redirect-" + chain.error, "unusable redirect (" + chain.error + ")"
    if not chain.redirected:
        s = final.status
        if s in NOT_FOUND:
            if expected:
                return WARN, "gone-not-redirected", f"{s}, but a redirect to {expected} was expected"
            return PASS, "gone", f"{s} {final.reason}".strip()
        if 200 <= s < 300:
            return FAIL, "still-served", f"{s}: the URL still answers (removed page, or soft 404)"
        if s in (401, 403, 429):
            return WARN, "blocked", f"{s}: the server refused, so the check could not tell"
        if s >= 500:
            return FAIL, "server-error", f"{s}"
        return WARN, "other-4xx", f"{s} instead of 404 or 410"
    landed = final.url
    same_site = host_key(urlsplit(landed).hostname or "") == home.host
    if expected:
        matches = _same_target(landed, urljoin(chain.responses[0].url, expected))
        if not matches:
            return FAIL, "wrong-target", f"lands on {landed}, expected {expected}"
    else:
        if not same_site:
            return FAIL, "redirect-offsite", f"lands on another site ({landed})"
        if home.is_home(landed):
            return FAIL, "redirect-home", f"redirects to the homepage ({landed})"
    if final.status in NOT_FOUND or final.status >= 400:
        return FAIL, "redirect-to-error", f"redirects to {landed}, which answers {final.status}"
    if not 200 <= final.status < 300:
        return FAIL, "redirect-other", f"redirects to {landed}, which answers {final.status}"
    if page and page["says_not_found"] and not expected:
        return FAIL, "soft-404", f"redirects to {landed}, a 200 page that says not found"
    temporary = [r.status for r in chain.responses[:-1] if r.status in (302, 303, 307)]
    if temporary:
        return WARN, "temporary-redirect", f"{temporary[0]} to {landed}: use 301 or 308 for removed pages"
    if len(chain.responses) > 3:
        return WARN, "long-chain", f"{len(chain.responses) - 1} redirects before {landed}"
    return PASS, "redirect-ok", f"{chain.responses[0].status} to {landed} (200)"


def resolve_entry(url: str, sites: list[str]) -> str:
    if "://" in url:
        return url
    if len(sites) != 1:
        raise ValueError(f"{url!r} is not absolute: give exactly one site to resolve it against")
    return urljoin(origin_of(sites[0]) + "/", url)


def check_urls(client: Client, entries: list[tuple[str, str | None]], sites: list[str],
               max_redirects: int = 5) -> dict:
    """Check known-removed URLs. Network errors are recorded per URL."""
    items = []
    for raw, expected in entries:
        url = resolve_entry(raw, sites)
        item: dict = {"url": url, "expected": expected, "error": None}
        try:
            chain = client.chain(url, max_redirects)
        except FetchError as e:
            item.update(verdict=FAIL, code="network-error", message=str(e), error=str(e), chain=[])
            items.append(item)
            continue
        final = chain.final
        page = None
        if chain.redirected and 200 <= final.status < 300:
            page = analyse_page(final, None, Home(url))
        verdict, code, message = judge_removed(chain, Home(url), expected, page)
        item.update(verdict=verdict, code=code, message=message, chain=chain_view(chain),
                    final_url=final.url, final_status=final.status,
                    size=len(final.body), time_ms=round(chain.elapsed * 1000))
        items.append(item)
    counts = Counter(i["verdict"] for i in items)
    return {"items": items, "counts": {k: counts.get(k, 0) for k in (PASS, WARN, FAIL)},
            "result": FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS),
            "requests": client.requests}
