"""Command line: soft-404-check SITE [SITE ...] [--urls FILE] [options]."""
from __future__ import annotations

import argparse
import random
import sys
from urllib.parse import urlsplit

from . import __version__
from .checks import FAIL, WARN, check_site, check_urls, origin_of, parse_url_list
from .fetch import USER_AGENT, Client, FetchError
from .report import to_json, to_markdown


def _site_url(arg: str) -> str:
    url = arg if "://" in arg else "https://" + arg
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise argparse.ArgumentTypeError(f"not a site: {arg!r}")
    return origin_of(url)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="soft-404-check",
        description="Check that a site answers 404 or 410 for pages that do not exist (no soft 404s).")
    p.add_argument("sites", nargs="*", type=_site_url, metavar="SITE",
                   help="site to probe, e.g. example.com or https://example.com")
    p.add_argument("--urls", metavar="FILE",
                   help="check known-removed URLs instead of probing ('-' reads standard input); "
                        "each line: URL [expected target]")
    p.add_argument("--section", action="append", default=[], metavar="PATH",
                   help="section to probe, e.g. /blog/ (repeatable; default: discovered from the homepage)")
    p.add_argument("--ext", default="html", metavar="LIST",
                   help="comma-separated extensions for the file probe (default html; e.g. html,php,pdf)")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown", help="report format")
    p.add_argument("-o", "--output", metavar="FILE", help="write the report to a file")
    p.add_argument("--fail-on", choices=("fail", "warn", "never"), default="fail",
                   help="exit 1 at this level or worse (default fail)")
    p.add_argument("--delay", type=float, default=1.0, metavar="SECONDS",
                   help="gap between requests; values below 1 are raised to 1 (not for localhost)")
    p.add_argument("--timeout", type=float, default=10.0, metavar="SECONDS", help="per-request timeout")
    p.add_argument("--max-redirects", type=int, default=5, metavar="N", help="redirects followed by hand (default 5)")
    p.add_argument("--seed", type=int, metavar="N", help="seed for the random paths (default: random)")
    p.add_argument("--no-robots", action="store_true", help="do not read robots.txt when discovering a section")
    p.add_argument("--insecure", action="store_true", help="do not verify TLS certificates")
    p.add_argument("--version", action="version", version=f"soft-404-check {__version__}")
    return p


def _emit(text: str, output: str | None) -> None:
    if output:
        with open(output, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    else:
        sys.stdout.buffer.write(text.encode("utf-8", "replace"))
        sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.sites and not args.urls:
        parser.error("give at least one SITE, or --urls FILE")
    exts = [e.strip().lstrip(".") for e in args.ext.split(",") if e.strip()]
    if not exts or any(not e.isalnum() for e in exts):
        parser.error("--ext takes letters and digits only, e.g. html,php,pdf")
    if args.max_redirects < 0 or args.timeout <= 0:
        parser.error("--max-redirects must be >= 0 and --timeout > 0")

    client = Client(user_agent=USER_AGENT, delay=max(1.0, args.delay), timeout=args.timeout,
                    verify=not args.insecure)
    rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()

    mode = "urls" if args.urls else "probe"
    results: list[dict] = []
    network_error = False
    if args.urls:
        try:
            if args.urls == "-":
                text = sys.stdin.read()
            else:
                with open(args.urls, encoding="utf-8") as f:
                    text = f.read()
        except (OSError, UnicodeError) as e:
            print(f"soft-404-check: cannot read {args.urls}: {e}", file=sys.stderr)
            return 2
        entries = parse_url_list(text)
        if not entries:
            print("soft-404-check: no URLs in the list", file=sys.stderr)
            return 2
        try:
            res = check_urls(client, entries, args.sites, args.max_redirects)
        except ValueError as e:
            print(f"soft-404-check: {e}", file=sys.stderr)
            return 2
        network_error = any(i.get("error") for i in res["items"])
        results.append(res)
    else:
        for site in args.sites:
            before = client.requests
            try:
                res = check_site(client, site, sections=args.section, exts=exts, rng=rng,
                                 max_redirects=args.max_redirects, use_robots=not args.no_robots)
            except FetchError as e:
                network_error = True
                res = {"site": site, "error": str(e), "result": FAIL, "requests": client.requests - before}
            results.append(res)

    text = to_json(mode, results) if args.format == "json" else to_markdown(mode, results)
    _emit(text, args.output)

    if network_error:
        return 2
    levels = {r["result"] for r in results}
    if args.fail_on == "fail" and FAIL in levels:
        return 1
    if args.fail_on == "warn" and levels & {FAIL, WARN}:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
