import pytest

from sitehelper import HOME_HTML, HELPFUL_404, make_app, page, redirect, serve
from soft_404_check.checks import check_urls, parse_url_list
from soft_404_check.fetch import Client


def removed(path):
    table = {
        "/old-404": page(404, HELPFUL_404),
        "/old-410": page(410, "gone"),
        "/old-moved": redirect("/new-page"),
        "/old-home": redirect("/"),
        "/old-lang-home": redirect("/en/"),
        "/old-expected": redirect("/target"),
        "/old-wrong": redirect("/new-page"),
        "/old-temp": redirect("/new-page", 302),
        "/old-still": page(200, "<html><title>Old page</title></html>"),
        "/old-to-404": redirect("/nowhere"),
        "/old-offsite": redirect("http://127.0.0.2:1/x"),
        "/old-soft": redirect("/gone-page"),
        "/gone-page": page(200, "<html><title>Page not found</title></html>"),
        "/old-chain": redirect("/hop-1"),
        "/hop-1": redirect("/hop-2"),
        "/hop-2": redirect("/hop-3"),
        "/hop-3": redirect("/new-page"),
        "/en/": page(200, HOME_HTML),
    }
    return table.get(path.split("?")[0])


def run(lines, sites=(), **kw):
    app = make_app(lambda p: page(404, HELPFUL_404), extra=removed)
    with serve(app) as (base, log):
        entries = parse_url_list("\n".join(l.replace("BASE", base) for l in lines))
        res = check_urls(Client(delay=0), entries, list(sites) or [base], **kw)
    return {i["url"].replace(base, ""): i for i in res["items"]}, res


def test_404_and_410_pass():
    items, res = run(["/old-404", "/old-410"])
    assert items["/old-404"]["code"] == "gone" and items["/old-410"]["code"] == "gone"
    assert res["result"] == "pass" and res["counts"] == {"pass": 2, "warn": 0, "fail": 0}


def test_sensible_redirect_passes():
    items, _ = run(["/old-moved"])
    it = items["/old-moved"]
    assert it["verdict"] == "pass" and it["code"] == "redirect-ok"
    assert it["final_url"].endswith("/new-page") and it["final_status"] == 200


def test_redirect_to_home_fails():
    items, res = run(["/old-home", "/old-lang-home"])
    assert items["/old-home"]["code"] == "redirect-home"
    assert items["/old-lang-home"]["code"] == "redirect-home"
    assert res["result"] == "fail"


def test_expected_target_matches_by_path_and_absolute():
    items, _ = run(["/old-expected /target", "BASE/old-expected BASE/target", "/old-expected -> /target"])
    assert all(i["verdict"] == "pass" for i in items.values()), items
    assert len(items) == 1  # same URL three times collapses in the dict; each one passed above


def test_expected_target_mismatch_fails():
    items, _ = run(["/old-wrong /target"])
    assert items["/old-wrong"]["code"] == "wrong-target"


def test_expected_home_is_allowed_when_asked_for():
    items, _ = run(["/old-home /"])
    assert items["/old-home"]["verdict"] == "pass"


def test_temporary_redirect_warns():
    items, res = run(["/old-temp"])
    assert items["/old-temp"]["code"] == "temporary-redirect"
    assert res["result"] == "warn"


def test_still_served_fails():
    items, _ = run(["/old-still"])
    assert items["/old-still"]["code"] == "still-served"


def test_redirect_to_missing_page_fails():
    items, _ = run(["/old-to-404"])
    assert items["/old-to-404"]["code"] == "redirect-to-error"


def test_redirect_to_page_saying_not_found_fails():
    items, _ = run(["/old-soft"])
    assert items["/old-soft"]["code"] == "soft-404"


def test_long_chain_warns_and_limit_fails():
    items, _ = run(["/old-chain"])
    assert items["/old-chain"]["code"] == "long-chain"
    items, _ = run(["/old-chain"], max_redirects=1)
    assert items["/old-chain"]["code"] == "redirect-too-many"


def test_gone_but_redirect_expected_warns():
    items, _ = run(["/old-404 /new-page"])
    assert items["/old-404"]["code"] == "gone-not-redirected"


def test_network_error_is_recorded_per_url():
    from sitehelper import free_port
    port = free_port()
    res = check_urls(Client(delay=0, timeout=2), [(f"http://127.0.0.1:{port}/x", None)], [])
    assert res["items"][0]["code"] == "network-error" and res["items"][0]["error"]


def test_relative_url_needs_exactly_one_site():
    with pytest.raises(ValueError):
        check_urls(Client(delay=0), [("/x", None)], [])


def test_parse_url_list():
    text = "# removed in 2026\n\n/a\n/b  /c  # note\nhttps://x.test/d => https://x.test/e\n   \n/frag#anchor\n"
    assert parse_url_list(text) == [("/a", None), ("/b", "/c"), ("https://x.test/d", "https://x.test/e"),
                                    ("/frag#anchor", None)]
