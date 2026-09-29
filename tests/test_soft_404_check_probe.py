import random

import pytest

from sitehelper import BARE_404, HELPFUL_404, HOME_HTML, make_app, page, redirect, serve
from soft_404_check.checks import check_site, discover_section
from soft_404_check.fetch import USER_AGENT, Client
from soft_404_check.htmlinfo import Page


def run(app, **kw):
    with serve(app) as (base, log):
        client = Client(delay=0)
        result = check_site(client, base, rng=random.Random(1), **kw)
    return result, log


def by_kind(result):
    return {p["kind"]: p for p in result["probes"]}


def helpful_404(path):
    return page(404, HELPFUL_404, {"Cache-Control": "no-store"})


def test_proper_404_passes_everywhere():
    result, log = run(make_app(helpful_404))
    assert result["result"] == "pass"
    kinds = by_kind(result)
    assert list(kinds) == ["top-level", "section", "extension .html", "trailing slash"]
    assert all(p["verdict"] == "pass" and p["final_status"] == 404 for p in kinds.values())
    assert kinds["section"]["url"].split("/")[3] == "blog"
    assert kinds["extension .html"]["url"].endswith(".html")
    assert kinds["trailing slash"]["url"].endswith("/")
    assert result["findings"] == []
    # home, robots.txt, 4 probes
    assert result["requests"] == 6
    assert all(e["ua"] == USER_AGENT for e in log)


def test_helpful_page_is_recognised():
    result, _ = run(make_app(helpful_404))
    g = by_kind(result)["top-level"]["page"]
    assert g["says_not_found"] and g["has_links"] and g["search_form"] and g["home_link"] and g["same_layout"]
    assert g["verdict"] == "helpful" and g["score"] == 5
    assert "no-store" in by_kind(result)["top-level"]["cache"]["cache-control"]


def test_soft_404_200_fails():
    result, _ = run(make_app(lambda p: page(200, HELPFUL_404)))
    assert result["result"] == "fail"
    assert all(p["code"] == "soft-404" for p in result["probes"])
    assert all(p["page"] is None for p in result["probes"])


def test_redirect_to_home_fails():
    result, _ = run(make_app(lambda p: redirect("/", 302)))
    assert result["result"] == "fail"
    p = by_kind(result)["top-level"]
    assert p["code"] == "redirect-home"
    assert [h["status"] for h in p["chain"]] == [302, 200]


def test_redirect_to_language_home_fails():
    result, _ = run(make_app(lambda p: redirect("/en/"), extra=lambda p: page(200, HOME_HTML) if p == "/en/" else None))
    assert by_kind(result)["top-level"]["code"] == "redirect-home"


def test_410_passes():
    result, _ = run(make_app(lambda p: page(410, HELPFUL_404)))
    assert result["result"] == "pass"
    assert all(p["final_status"] == 410 for p in result["probes"])


def test_bare_404_warns():
    result, _ = run(make_app(lambda p: page(404, BARE_404)))
    assert result["result"] == "warn"
    assert [f["code"] for f in result["findings"]] == ["bare-404-page"]
    assert by_kind(result)["top-level"]["page"]["verdict"] == "bare"


def test_non_html_404_is_only_informational():
    result, _ = run(make_app(lambda p: page(404, "not found", ctype="text/plain")))
    assert result["result"] == "pass"
    assert [f["code"] for f in result["findings"]] == ["404-not-html"]


def test_long_cached_404_warns_and_headers_are_recorded():
    headers = {"Cache-Control": "public, max-age=604800", "Expires": "Wed, 01 Jan 2031 00:00:00 GMT",
               "Age": "42", "cf-cache-status": "HIT"}
    result, _ = run(make_app(lambda p: page(404, HELPFUL_404, headers)))
    assert result["result"] == "warn"
    assert "404-long-cache" in [f["code"] for f in result["findings"]]
    c = by_kind(result)["top-level"]["cache"]
    assert c["age"] == "42" and c["cf-cache-status"] == "HIT" and c["expires"].startswith("Wed")


def test_short_cache_is_fine():
    result, _ = run(make_app(lambda p: page(404, HELPFUL_404, {"Cache-Control": "public, max-age=300"})))
    assert result["findings"] == []


def test_other_redirect_warns():
    result, _ = run(make_app(lambda p: redirect("/new-page")))
    assert result["result"] == "warn"
    assert by_kind(result)["top-level"]["code"] == "redirect-to-page"


def test_redirect_to_200_page_that_says_not_found_is_soft_404():
    app = make_app(lambda p: redirect("/oops"),
                   extra=lambda p: page(200, "<html><title>Page not found</title></html>") if p == "/oops" else None)
    result, _ = run(app)
    assert result["result"] == "fail"
    assert by_kind(result)["top-level"]["code"] == "soft-404"


def test_redirect_to_a_404_page_warns():
    app = make_app(lambda p: redirect("/oops"),
                   extra=lambda p: page(404, HELPFUL_404) if p == "/oops" else None)
    result, _ = run(app)
    assert by_kind(result)["top-level"]["code"] == "redirect-to-404"
    assert by_kind(result)["top-level"]["verdict"] == "warn"


def test_trailing_slash_normalisation_then_404_passes():
    def missing(p):
        if p.endswith("/") and p != "/":
            return redirect(p.rstrip("/"))
        return page(404, HELPFUL_404)
    result, _ = run(make_app(missing))
    p = by_kind(result)["trailing slash"]
    assert p["code"] == "normalising-redirect" and p["verdict"] == "pass"
    assert [h["status"] for h in p["chain"]] == [301, 404]


def test_redirect_loop_fails():
    result, _ = run(make_app(lambda p: redirect(p + "x") if len(p) < 3 else redirect("/loop-a"),
                             extra=lambda p: redirect("/loop-b") if p == "/loop-a" else (redirect("/loop-a") if p == "/loop-b" else None)))
    assert result["result"] == "fail"
    assert by_kind(result)["top-level"]["code"] == "redirect-loop"


def test_server_error_fails():
    result, _ = run(make_app(lambda p: page(500, "boom")))
    assert by_kind(result)["top-level"]["code"] == "server-error"
    assert result["result"] == "fail"


def test_forbidden_is_a_warning():
    result, _ = run(make_app(lambda p: page(403, "no")))
    assert by_kind(result)["top-level"]["code"] == "blocked"
    assert result["result"] == "warn"


def test_noindex_is_reported_from_header_and_meta():
    result, _ = run(make_app(lambda p: page(404, HELPFUL_404, {"X-Robots-Tag": "noindex, nofollow"})))
    assert all(p["noindex"] for p in result["probes"])
    meta = HELPFUL_404.replace("<head>", '<head><meta name="robots" content="noindex">')
    result, _ = run(make_app(lambda p: page(404, meta)))
    assert all(p["noindex"] for p in result["probes"])
    result, _ = run(make_app(helpful_404))
    assert not any(p["noindex"] for p in result["probes"])


def test_explicit_section_skips_robots_and_discovery():
    result, log = run(make_app(helpful_404), sections=["/docs"])
    section = by_kind(result)["section"]
    assert section["url"].split("/")[3] == "docs"
    assert "/robots.txt" not in [e["path"] for e in log]


def test_several_sections_and_extensions():
    result, _ = run(make_app(helpful_404), sections=["/blog/", "/docs/"], exts=["html", "php", "pdf"])
    kinds = [p["kind"] for p in result["probes"]]
    assert kinds == ["top-level", "section", "section", "extension .html", "extension .php",
                     "extension .pdf", "trailing slash"]


def test_robots_blocked_section_is_not_used():
    def extra(path):
        if path == "/robots.txt":
            return page(200, "User-agent: *\nDisallow: /blog/\n", ctype="text/plain")
    result, _ = run(make_app(helpful_404, extra=extra))
    assert by_kind(result)["section"]["url"].split("/")[3] == "docs"


def test_no_section_found_is_noted():
    home = "<html><title>Tiny</title><body><a href='/about'>About</a></body></html>"

    def extra(path):
        return page(200, home) if path == "/" else None
    result, _ = run(make_app(helpful_404, extra=extra))
    assert "section" not in by_kind(result)
    assert "section probe skipped" in result["section_note"]


def test_same_seed_same_paths():
    a, _ = run(make_app(helpful_404))
    b, _ = run(make_app(helpful_404))
    assert [p["url"].split("/", 3)[3] for p in a["probes"]] == [p["url"].split("/", 3)[3] for p in b["probes"]]


def test_gzip_pages_are_decoded():
    result, _ = run(make_app(lambda p: page(404, HELPFUL_404, {"_gzip": "1"})))
    assert by_kind(result)["top-level"]["page"]["score"] == 5


def test_discover_prefers_real_section_over_language_root():
    home = ("<html><body><a href='/en/'>EN</a> <a href='/en/a'>a</a> <a href='/en/b'>b</a>"
            "<a href='/work/x'>x</a><a href='/cdn-cgi/l/email'>e</a><a href='/file.pdf'>f</a></body></html>")
    from soft_404_check.checks import Home
    section = discover_section(Page(home.encode(), "https://e.com/", "text/html"), "https://e.com/",
                               Home("https://e.com/"), None, "ua")
    assert section == "/work/"


def test_layout_and_search_detection_on_unrelated_page():
    result, _ = run(make_app(lambda p: page(404, "<html><title>Not Found</title><body><h1>Not Found</h1></body></html>")))
    g = by_kind(result)["top-level"]["page"]
    assert g["says_not_found"] and not g["has_links"] and not g["search_form"]
    assert not g["home_link"] and not g["same_layout"]


@pytest.mark.parametrize("title,expected", [
    ("Page not found", True), ("404", True), ("Page introuvable", True), ("P\u00e1gina no encontrada", True),
    ("Welcome home", False),
])
def test_not_found_wording(title, expected):
    assert Page(f"<title>{title}</title>".encode(), "https://e.com/").says_not_found is expected


def test_asset_folders_are_not_sections():
    home = ("<html><body><a href='/static/app.css'>c</a> <a href='/static/x/'>s</a> <a href='/static/y/'>s</a>"
            "<a href='/guides/one'>g</a></body></html>")
    from soft_404_check.checks import Home
    section = discover_section(Page(home.encode(), "https://e.com/", "text/html"), "https://e.com/",
                               Home("https://e.com/"), None, "ua")
    assert section == "/guides/"


def test_redirect_before_404_is_an_info_note():
    def missing(p):
        if p.endswith("/") and p != "/":
            return page(404, HELPFUL_404)
        return redirect(p + "/", 307)
    result, _ = run(make_app(missing))
    assert result["result"] == "pass"
    note = [f for f in result["findings"] if f["code"] == "404-after-redirect"]
    assert note and note[0]["level"] == "info" and "307" in note[0]["message"]
