# soft-404-check

Does your site really say "404" when a page does not exist? One command requests a few made-up URLs and reports **soft 404s** (a 200 for a missing page), missing pages that **redirect to the homepage**, whether the 404 page actually helps a visitor, and how the 404 is cached. A second mode checks a list of URLs you removed on purpose. Markdown or JSON, exit codes for CI. Standard library only, Python 3.10+.

```
soft-404-check example.com
soft-404-check example.com --section /blog/ --ext html,php,pdf
soft-404-check example.com --urls removed-urls.txt --format json -o report.json
```

- **Redirects are recorded, not hidden.** Every hop is requested by hand, so the report shows `307 > 404` or `301 > 200`, and a redirect that lands on the homepage (or a bare language root such as `/en/`) is called what it is.
- **Four kinds of made-up URL** per site: a random top-level path, a random path under a real section, one with a file extension, and a trailing-slash variant. The names are random (`s404-` plus 12 letters and digits), so no cache can hold an answer for them.
- **Judges the 404 page too**: does it say "not found" in the title or h1, does it link somewhere, have a search form, link back home, and share the homepage layout?
- **Polite by design**: `GET` only, at least 1 second between requests (not enforced for `localhost`), about 6 to 10 requests per site, a User-Agent that says who is asking, `/cdn-cgi/` links ignored, robots.txt read the RFC 9309 way when a section is discovered.

## Install

```
pip install .                 # from a clone; gives the soft-404-check command
python -m soft_404_check      # or run it straight from the folder, no install
```

## Usage

```
soft-404-check SITE [SITE ...] [options]
soft-404-check [SITE] --urls FILE [options]
```

`SITE` can be `example.com` or `https://example.com`; only the origin is used.

| Option | Meaning |
|---|---|
| `--section PATH` | Section to probe, e.g. `/blog/` (repeatable). Default: one section discovered from the homepage links. |
| `--ext LIST` | Extensions for the file probe, comma separated (default `html`; try `html,php,pdf`). |
| `--urls FILE` | Check known-removed URLs instead of probing (`-` reads standard input). |
| `--format markdown\|json` | Report format (default `markdown`). |
| `--output FILE`, `-o FILE` | Write the report to a file. |
| `--fail-on fail\|warn\|never` | Exit 1 at this level or worse (default `fail`). |
| `--delay SECONDS` | Gap between requests. Values below 1 are raised to 1 (not for `localhost`). |
| `--timeout SECONDS` | Per-request timeout (default 10). |
| `--max-redirects N` | Redirects followed by hand (default 5). |
| `--seed N` | Seed for the random paths, for repeatable runs. |
| `--no-robots` | Do not read robots.txt when discovering a section. |
| `--insecure` | Do not verify TLS certificates. |

`--section` applies to every site on the command line, so give it together with one site when the section differs.

### `--urls FILE`

One URL per line, `#` starts a comment. Relative paths are resolved against the one `SITE` you give; absolute URLs need none. An optional second column is the expected target (`->` or `=>` between them is ignored). See [examples/removed-urls.txt](examples/removed-urls.txt).

```
/old-page
/services/web-design /en/services/web-design
https://example.com/promo-2020
```

Each URL must return **404 or 410**, or **redirect to a sensible replacement**: same site, not the homepage, final status 200. With an expected target, the redirect must land exactly there (path compared, trailing slash ignored).

## What it checks

| Situation | Result |
|---|---|
| 404 or 410 | pass |
| 200 (or any 2xx) for a missing URL | **fail** (soft 404) |
| Redirect to the homepage | **fail** |
| Redirect to a 200 page that says "not found" | **fail** (soft 404) |
| Redirect loop, too many redirects, 5xx | **fail** |
| Redirect to another page (200), another host, or to a 404 | warn |
| Only a trailing slash, case or `www` differs, then 404 | pass, with an info note |
| 401, 403, 429, other 4xx | warn (the server refused, so its 404 behaviour is unknown) |

Also reported for every probe: the redirect chain, response size, time (all hops together) and `noindex` (`X-Robots-Tag` or `<meta name="robots">`, informational only). For the 404 responses: `Cache-Control`, `Expires`, `Age` and the CDN status (`cf-cache-status`, `cdn-cache-status`, `x-cache`, `x-vercel-cache`).

Site findings: bare 404 page (warn: no links, search or navigation), 404 cacheable for more than a day (warn), non-HTML 404 (info), a missing URL that is redirected before it answers 404 (info).

404 page score, one point each: says not found (title or h1; English, French and Spanish wording), at least 3 links, search form, link to the homepage, same layout as the homepage (shared stylesheet, or mostly the same links in header, nav and footer). 4 or 5 is `helpful`, 2 or 3 `basic`, 0 or 1 `bare`.

In `--urls` mode the same rules apply to known-removed URLs, plus: a 200 with no redirect is `still-served` (fail), a redirect to the wrong target is `wrong-target` (fail), a 302/303/307 is a warning (use 301 or 308 for removed pages), and so is a chain of more than three redirects.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | nothing at or above `--fail-on` |
| `1` | at least one problem found |
| `2` | usage error, unreadable list, or a site or URL could not be reached |

[examples/ci-github-actions.yml](examples/ci-github-actions.yml) is a weekly CI job (not an active workflow) that probes a site and checks a list of removed URLs.

## Measured result

Real runs on 2026-09-29 against my own three sites, GET only, at least 1 second apart, `--fail-on never` so the run always prints everything. The reports are pasted as printed; where one command probed several sites, only the site under discussion is kept. The sites come from different runs because my first attempts had mistakes (a shell rewrote a path, and `--section` applies to every site on the command line).

**1. [demarkstudio.ca](https://demarkstudio.ca)** (8 requests, section `/en/`):

```
$ python -m soft_404_check https://demarkstudio.ca --section /en/ --fail-on never
# Soft 404 check

soft-404-check 1.0.0, 2026-09-29 11:40 UTC

## https://demarkstudio.ca

Result: **PASS**. Homepage: 200 at /en/, title "DeMark — Websites, online booking and invoicing for small businesses across Canada". 8 request(s).

| Probe | Requested path | Chain | Result | Size | Time | noindex |
|---|---|---|---|---|---|---|
| top-level | `/s404-7xznh4feao8d` | 307 > 404 (/s404-7xznh4feao8d/) | PASS: 307 to https://demarkstudio.ca/s404-7xznh4feao8d/, then 404 | 7.6 KiB | 203 ms | yes |
| section | `/en/s404-lxwsgh1fu3mz` | 404 | PASS: 404 Not Found | 7.6 KiB | 109 ms | yes |
| extension .html | `/s404-bemktb3czvac.html` | 307 > 404 (/s404-bemktb3czvac.html/) | PASS: 307 to https://demarkstudio.ca/s404-bemktb3czvac.html/, then 404 | 7.6 KiB | 188 ms | yes |
| trailing slash | `/s404-1m8g1b02vi5w/` | 404 | PASS: 404 Not Found | 7.6 KiB | 93 ms | yes |

404 page quality (only for probes that answered 404 or 410):

| Probe | Says not found | Links | Search form | Home link | Same layout | Verdict |
|---|---|---|---|---|---|---|
| top-level | yes | 26 | no | yes | yes | helpful (4/5) |
| section | yes | 26 | no | yes | yes | helpful (4/5) |
| extension .html | yes | 26 | no | yes | yes | helpful (4/5) |
| trailing slash | yes | 26 | no | yes | yes | helpful (4/5) |

Title / h1: "Page not found — DeMark" / "That page doesn't exist."

Cache headers on the 404 responses:

| Probe | Cache-Control | Expires | Age | CDN cache status |
|---|---|---|---|---|
| top-level | - | - | - | cf-cache-status: DYNAMIC |
| section | - | - | - | cf-cache-status: DYNAMIC |
| extension .html | - | - | - | cf-cache-status: DYNAMIC |
| trailing slash | - | - | - | cf-cache-status: DYNAMIC |

Findings:

- **INFO** `404-after-redirect`: a missing URL is first redirected (307) to its trailing-slash or case variant and only then answers 404: top-level, extension .html
```

**2. [demo.demarkstudio.ca](https://demo.demarkstudio.ca)** (10 requests). The demo sends `noindex` on purpose, and `/` sends a visitor to the login page, so that is what "homepage" means here. The same `--section` also went to the other two sites in this run (where it is not a real section); those parts are dropped:

```
$ python -m soft_404_check https://demarkstudio.ca https://demo.demarkstudio.ca https://marvin.demarkstudio.ca --section /riverside-pub/site/ --fail-on never
# Soft 404 check

soft-404-check 1.0.0, 2026-09-29 11:40 UTC

## https://demo.demarkstudio.ca

Result: **PASS**. Homepage: 200 at /app/login, title "Sign in — DeMark". 10 request(s).

| Probe | Requested path | Chain | Result | Size | Time | noindex |
|---|---|---|---|---|---|---|
| top-level | `/s404-qyqcg98w0538` | 307 > 308 > 404 (/s404-qyqcg98w0538/) | PASS: 307 to https://demo.demarkstudio.ca/s404-qyqcg98w0538/, then 404 | 7.3 KiB | 250 ms | yes |
| section | `/riverside-pub/site/s404-wu12wgomowda` | 404 | PASS: 404 Not Found | 7.3 KiB | 93 ms | yes |
| extension .html | `/s404-2mmx6epch1hs.html` | 307 > 308 > 404 (/s404-2mmx6epch1hs.html/) | PASS: 307 to https://demo.demarkstudio.ca/s404-2mmx6epch1hs.html/, then 404 | 7.3 KiB | 250 ms | yes |
| trailing slash | `/s404-iq9mt3hhhtna/` | 404 | PASS: 404 Not Found | 7.3 KiB | 110 ms | yes |

404 page quality (only for probes that answered 404 or 410):

| Probe | Says not found | Links | Search form | Home link | Same layout | Verdict |
|---|---|---|---|---|---|---|
| top-level | yes | 25 | no | yes | no | basic (3/5) |
| section | yes | 25 | no | yes | no | basic (3/5) |
| extension .html | yes | 25 | no | yes | no | basic (3/5) |
| trailing slash | yes | 25 | no | yes | no | basic (3/5) |

Title / h1: "Page not found — DeMark" / "That page doesn't exist."

Cache headers on the 404 responses:

| Probe | Cache-Control | Expires | Age | CDN cache status |
|---|---|---|---|---|
| top-level | - | - | - | cf-cache-status: DYNAMIC |
| section | - | - | - | cf-cache-status: DYNAMIC |
| extension .html | - | - | - | cf-cache-status: DYNAMIC |
| trailing slash | - | - | - | cf-cache-status: DYNAMIC |

Findings:

- **INFO** `404-after-redirect`: a missing URL is first redirected (307) to its trailing-slash or case variant and only then answers 404: top-level, extension .html
```

**3. [marvin.demarkstudio.ca](https://marvin.demarkstudio.ca)** (6 requests, section discovered from the homepage links; in that run `demarkstudio.ca` picked an asset folder, `/static/`, which is why the tool now skips folders such as `static` and `assets`):

```
$ python -m soft_404_check https://demarkstudio.ca https://marvin.demarkstudio.ca --fail-on never
# Soft 404 check

soft-404-check 1.0.0, 2026-09-29 11:38 UTC

## https://marvin.demarkstudio.ca

Result: **PASS**. Homepage: 200 at /, title "Marvin Palencia — Python & Web Developer \| Stripe, FastAPI, Websites". 6 request(s).

| Probe | Requested path | Chain | Result | Size | Time | noindex |
|---|---|---|---|---|---|---|
| top-level | `/s404-5rhsj2125jr4` | 404 | PASS: 404 Not Found | 906 B | 78 ms | yes |
| section | `/work/s404-s1u89nge127j` | 404 | PASS: 404 Not Found | 906 B | 94 ms | yes |
| extension .html | `/s404-8uvv5rmtist8.html` | 404 | PASS: 404 Not Found | 906 B | 78 ms | yes |
| trailing slash | `/s404-prgg25kgg7i8/` | 404 | PASS: 404 Not Found | 906 B | 78 ms | yes |

404 page quality (only for probes that answered 404 or 410):

| Probe | Says not found | Links | Search form | Home link | Same layout | Verdict |
|---|---|---|---|---|---|---|
| top-level | yes | 2 | no | yes | yes | basic (3/5) |
| section | yes | 2 | no | yes | yes | basic (3/5) |
| extension .html | yes | 2 | no | yes | yes | basic (3/5) |
| trailing slash | yes | 2 | no | yes | yes | basic (3/5) |

Title / h1: "Page not found — Marvin Palencia" / "Page not found"

Cache headers on the 404 responses:

| Probe | Cache-Control | Expires | Age | CDN cache status |
|---|---|---|---|---|
| top-level | - | - | - | cf-cache-status: DYNAMIC |
| section | - | - | - | cf-cache-status: DYNAMIC |
| extension .html | - | - | - | cf-cache-status: DYNAMIC |
| trailing slash | - | - | - | cf-cache-status: DYNAMIC |

Findings: none
```

**4. Tests** (no internet; local HTTP servers on random free ports):

```
$ python -m pytest -q -p no:cacheprovider --import-mode=importlib soft-404-check
....................................................................     [100%]
68 passed in 11.34s
```

What it found, without spin:

- **No soft 404 on any of the three sites.** Every made-up URL ended in a real 404, none redirected to a homepage, and none of the 404s is cached (`cf-cache-status: DYNAMIC`, no long `Cache-Control`).
- **Missing top-level and `.html` paths take a redirect first** on `demarkstudio.ca` and the demo: a `307` to the same path with a trailing slash (the demo adds a `308`), and only then the 404. The end result is right, but the temporary redirect is an extra request, and `/name.html/` is an odd place to redirect to. The tool reports this as an info note, not a failure.
- **The 404 page on `demarkstudio.ca` is helpful** (4 of 5: 26 links, home link, same layout, says "not found"; no search form). On the demo it scores 3 of 5 only because its homepage is the login page, whose layout differs, so "same layout" is `no`. That is a limit of comparing with whatever `/` lands on. On `marvin.demarkstudio.ca` the 404 is basic (3 of 5): "Page not found" and a way home, but only 2 links and no search.
- **Every 404 page carries `noindex`.** Informational; it is what you want on an error page.
- **Not exercised on the real sites:** `--urls` mode (covered by the tests only), a 410, and a helpful 404 with a search form.

## Tests

`python -m pytest -q -p no:cacheprovider --import-mode=importlib soft-404-check` (from the parent folder) runs without internet. Local `http.server` sites on random free ports simulate a proper 404, a soft 404, a redirect to the homepage or a language root, 410, helpful and bare 404 pages, non-HTML 404s, long and short cache headers, `noindex` in header and meta, trailing-slash and other redirects, loops, 5xx and 403, robots.txt blocking a section, gzip pages, and `--urls` mode with 404, 410, sensible redirects, redirects to the homepage, expected targets (matching and not), temporary redirects, long chains and network errors. Also the politeness delay, the User-Agent, the JSON and Markdown output and every exit code.

## Limitations

- **A handful of probes proves little.** A CMS may treat `/blog/...` differently from `/shop/...`; give more `--section` values.
- **"Homepage" is what `/` lands on.** A site whose homepage is a login page or a redirect to a landing page makes the layout comparison weak. Any two-letter first-level path (`/en`, `/fr`) counts as a language home, so a real two-letter section would be misjudged.
- **No JavaScript.** A single-page app that shows "not found" client-side after a 200 is reported as a soft 404 (correctly), but its rendered page is not inspected.
- **Helpfulness is a heuristic**: link counts, a search-form guess (`type=search`, common field names, `role=search`, "search" in id, class or action) and English, French and Spanish wording. It does not read the page like a person.
- **robots.txt is read only to pick a section.** The made-up URLs themselves are requested regardless, like a link checker on your own site: run it on sites you are allowed to test.
- **What one client sees.** CDNs and firewalls may answer a script differently from a browser, or block repeated runs (`blocked` warnings).
- Bodies are read up to 512 KiB; `br` and `zstd` are not requested, so pages arrive plain or gzip.
- `--urls` compares an expected target by path (query only if you give one) and does not compare the replacement's content.

## Layout

`soft_404_check/rfc9309.py` is a copy of [robots9309](https://github.com/dreadmoreeee/robots9309) (MIT, same author). The rest: `fetch.py` (client, manual redirect chain, delay), `htmlinfo.py` (links, forms, title, layout), `checks.py` (probes, judging, `--urls`), `report.py` (Markdown and JSON), `cli.py`.

## Author

Marvin Palencia, founder of [DeMark Studio](https://demarkstudio.ca), Miramichi, New Brunswick, Canada. Portfolio: [marvin.demarkstudio.ca](https://marvin.demarkstudio.ca)

MIT License.
