import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sitehelper import BARE_404, HELPFUL_404, make_app, page, redirect, serve, free_port
from soft_404_check import __version__
from soft_404_check.cli import main
from soft_404_check.fetch import USER_AGENT, Client

ROOT = Path(__file__).resolve().parent.parent


def good(path):
    return page(404, HELPFUL_404)


def test_ok_exit_0_and_markdown(capsys):
    with serve(make_app(good)) as (base, _):
        code = main([base, "--seed", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("# Soft 404 check")
    assert "Result: **PASS**" in out and "| top-level |" in out and "| trailing slash |" in out
    assert "404 page quality" in out and "Cache headers on the 404 responses" in out
    assert "Findings: none" in out


def test_soft_404_exit_1(capsys):
    with serve(make_app(lambda p: page(200, HELPFUL_404))) as (base, _):
        code = main([base])
    assert code == 1
    assert "FAIL: 200 for a URL that does not exist (soft 404)" in capsys.readouterr().out


def test_fail_on_warn(capsys):
    with serve(make_app(lambda p: page(404, BARE_404))) as (base, _):
        assert main([base]) == 0
        assert main([base, "--fail-on", "warn"]) == 1
        assert main([base, "--fail-on", "never"]) == 0
    capsys.readouterr()


def test_json_output_to_file(tmp_path, capsys):
    out = tmp_path / "r.json"
    with serve(make_app(lambda p: redirect("/"))) as (base, _):
        code = main([base, "--format", "json", "-o", str(out), "--section", "/docs/"])
    assert code == 1
    assert capsys.readouterr().out == ""
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["tool"] == "soft-404-check" and doc["version"] == __version__ and doc["mode"] == "probe"
    site = doc["sites"][0]
    assert site["result"] == "fail"
    assert {p["code"] for p in site["probes"]} == {"redirect-home"}
    assert site["probes"][0]["chain"][0]["status"] == 301
    assert out.read_bytes().isascii()


def test_urls_mode_markdown_and_exit(tmp_path, capsys):
    lst = tmp_path / "removed.txt"
    lst.write_text("# gone\n/old-a\n/old-b /new-page\n", encoding="utf-8")

    def extra(path):
        return {"/old-a": page(410, "gone"), "/old-b": redirect("/new-page")}.get(path)
    with serve(make_app(good, extra=extra)) as (base, _):
        code = main([base, "--urls", str(lst)])
        out = capsys.readouterr().out
        assert code == 0
        assert "## Removed URLs (2)" in out and "pass 2, warn 0, fail 0" in out
        lst.write_text("/old-a\n/about\n", encoding="utf-8")
        assert main([base, "--urls", str(lst), "--format", "json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["mode"] == "urls" and doc["sites"][0]["counts"]["fail"] == 1


def test_urls_mode_absolute_urls_need_no_site(tmp_path, capsys):
    with serve(make_app(good)) as (base, _):
        lst = tmp_path / "u.txt"
        lst.write_text(f"{base}/gone-page\n", encoding="utf-8")
        assert main(["--urls", str(lst)]) == 0
    capsys.readouterr()


def test_urls_mode_relative_without_site_is_usage_error(tmp_path, capsys):
    lst = tmp_path / "u.txt"
    lst.write_text("/x\n", encoding="utf-8")
    assert main(["--urls", str(lst)]) == 2
    assert "not absolute" in capsys.readouterr().err


def test_unreachable_site_exit_2(capsys):
    code = main([f"http://127.0.0.1:{free_port()}", "--timeout", "2"])
    out = capsys.readouterr().out
    assert code == 2 and "Could not be checked" in out


def test_urls_network_error_exit_2(tmp_path, capsys):
    lst = tmp_path / "u.txt"
    lst.write_text(f"http://127.0.0.1:{free_port()}/x\n", encoding="utf-8")
    assert main(["--urls", str(lst), "--timeout", "2"]) == 2
    capsys.readouterr()


@pytest.mark.parametrize("argv", [[], ["--ext", "ht ml", "example.com"], ["--timeout", "0", "example.com"]])
def test_usage_errors_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2
    capsys.readouterr()


def test_missing_urls_file_exit_2(capsys):
    assert main(["--urls", "definitely-not-here.txt"]) == 2
    capsys.readouterr()


def test_bare_host_is_accepted_with_https_default():
    from soft_404_check.cli import build_parser
    assert build_parser().parse_args(["example.com/some/path"]).sites == ["https://example.com"]


def test_module_entry_point():
    with serve(make_app(good)) as (base, _):
        r = subprocess.run([sys.executable, "-m", "soft_404_check", base, "--format", "json"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["sites"][0]["result"] == "pass"


def test_user_agent_identifies_the_tool():
    assert USER_AGENT == f"soft-404-check/{__version__} (+https://github.com/dreadmoreeee/soft-404-check)"


def test_politeness_delay_is_enforced_off_localhost():
    with serve(make_app(good)) as (base, log):
        client = Client(delay=0.3, loopback_exempt=False)
        for path in ("/", "/about", "/contact"):
            client.get(base + path)
    times = [e["t"] for e in log]
    assert all(b - a >= 0.28 for a, b in zip(times, times[1:]))


def test_localhost_is_not_delayed():
    with serve(make_app(good)) as (base, log):
        client = Client(delay=1.0)
        started = time.monotonic()
        for path in ("/", "/about", "/contact"):
            client.get(base + path)
    assert time.monotonic() - started < 1.0


def test_cli_raises_delay_to_one_second_minimum():
    # --delay 0.1 is raised to 1.0; only checked through the parsed value used to build the client
    from soft_404_check import cli
    seen = {}
    real = cli.Client

    class Spy(real):
        def __init__(self, **kw):
            seen.update(kw)
            super().__init__(**kw)
    cli.Client = Spy
    try:
        with serve(make_app(good)) as (base, _):
            cli.main([base, "--delay", "0.1"])
    finally:
        cli.Client = real
    assert seen["delay"] == 1.0


def test_sources_are_ascii():
    for path in list((ROOT / "soft_404_check").glob("*.py")) + list((ROOT / "tests").glob("*.py")):
        path.read_bytes().decode("ascii")
