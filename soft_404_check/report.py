"""Markdown and JSON output."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import __version__

LABEL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "info": "INFO"}


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KiB"


def _yn(v) -> str:
    return "yes" if v else "no"


def _path(url: str) -> str:
    from urllib.parse import urlsplit
    p = urlsplit(url)
    return (p.path or "/") + (("?" + p.query) if p.query else "")


def chain_text(chain: list[dict]) -> str:
    if not chain:
        return "-"
    parts = [str(chain[0]["status"])]
    for hop in chain[1:]:
        parts.append(f"{hop['status']}")
    if len(chain) == 1:
        return parts[0]
    return " > ".join(parts) + " (" + _path(chain[-1]["url"]) + ")"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def to_json(mode: str, sites: list[dict]) -> str:
    doc = {"tool": "soft-404-check", "version": __version__, "mode": mode,
           "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "sites": sites}
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


def _findings(findings: list[dict]) -> list[str]:
    if not findings:
        return ["Findings: none", ""]
    out = ["Findings:", ""]
    out += [f"- **{LABEL[f['level']]}** `{f['code']}`: {f['message']}" for f in findings]
    return out + [""]


def site_markdown(s: dict) -> list[str]:
    out = [f"## {s['site']}", ""]
    if s.get("error"):
        return out + [f"Could not be checked: {s['error']}", ""]
    home = s["homepage"]
    out.append(f"Result: **{LABEL[s['result']]}**. Homepage: {home['status']} at {_path(home['url'])}"
               + (f", title \"{_cell(home['title'])}\"" if home["title"] else "")
               + f". {s['requests']} request(s).")
    out.append("")
    if s.get("section_note"):
        out += [f"Note: {s['section_note']}", ""]
    out += ["| Probe | Requested path | Chain | Result | Size | Time | noindex |", "|---|---|---|---|---|---|---|"]
    for p in s["probes"]:
        out.append(f"| {p['kind']} | `{_cell(_path(p['url']))}` | {chain_text(p['chain'])} | "
                   f"{LABEL[p['verdict']]}: {_cell(p['message'])} | {_size(p['size'])} | {p['time_ms']} ms | "
                   f"{_yn(p['noindex'])} |")
    out.append("")
    pages = [p for p in s["probes"] if p["page"]]
    if pages:
        out += ["404 page quality (only for probes that answered 404 or 410):", "",
                "| Probe | Says not found | Links | Search form | Home link | Same layout | Verdict |",
                "|---|---|---|---|---|---|---|"]
        for p in pages:
            g = p["page"]
            out.append(f"| {p['kind']} | {_yn(g['says_not_found'])} | {g['links']} | {_yn(g['search_form'])} | "
                       f"{_yn(g['home_link'])} | {_yn(g['same_layout'])} | {g['verdict']} ({g['score']}/5) |")
        titles = sorted({(p["page"]["title"], p["page"]["h1"]) for p in pages})
        out += ["", "Title / h1: " + "; ".join(f"\"{_cell(t)}\" / \"{_cell(h)}\"" for t, h in titles), ""]
    cached = [p for p in s["probes"] if p["cache"] or p["final_status"] in (404, 410)]
    if cached:
        out += ["Cache headers on the 404 responses:", "",
                "| Probe | Cache-Control | Expires | Age | CDN cache status |", "|---|---|---|---|---|"]
        for p in cached:
            c = p["cache"]
            cdn = ", ".join(f"{k}: {c[k]}" for k in ("cf-cache-status", "cdn-cache-status", "x-cache", "x-vercel-cache") if k in c)
            out.append(f"| {p['kind']} | {_cell(c.get('cache-control', '-'))} | {_cell(c.get('expires', '-'))} | "
                       f"{_cell(c.get('age', '-'))} | {_cell(cdn or '-')} |")
        out.append("")
    return out + _findings(s["findings"])


def urls_markdown(s: dict) -> list[str]:
    out = [f"## Removed URLs ({len(s['items'])})", "",
           f"Result: **{LABEL[s['result']]}**. pass {s['counts']['pass']}, warn {s['counts']['warn']}, "
           f"fail {s['counts']['fail']}. {s['requests']} request(s).", "",
           "| URL | Chain | Result |", "|---|---|---|"]
    for i in s["items"]:
        out.append(f"| `{_cell(i['url'])}` | {chain_text(i['chain'])} | {LABEL[i['verdict']]}: {_cell(i['message'])} |")
    return out + [""]


def to_markdown(mode: str, sites: list[dict]) -> str:
    out = ["# Soft 404 check", "", f"soft-404-check {__version__}, {now_utc()}", ""]
    for s in sites:
        out += urls_markdown(s) if mode == "urls" else site_markdown(s)
    return "\n".join(out).rstrip() + "\n"
