"""
Automated web search for a firm's named senior person, when the SEC brochure
lookup (iapd.py) didn't name anyone. Uses Startpage (a Google-results proxy)
via plain HTTP requests — tested live and, unlike Google/Bing/DuckDuckGo
(all confirmed to block automated requests, even through full Playwright
browser rendering — see CLAUDE.md Session 9), Startpage returns real,
unblocked organic results.

Query syntax matters: quoted exact-phrase + boolean OR (the format that
kept failing for LinkedIn too) returns ZERO results here as well. Plain,
unquoted, space-separated keywords work — tested against multiple real
firms before committing to this format.

Best-effort, same caveat as iapd.py's extraction: search results can surface
an executive who has since LEFT the firm, or an unrelated mention. Requires
the firm's own canonical name token to appear in the same result snippet as
the extracted name, as a (partial, not perfect) relevance guard.
"""
from __future__ import annotations
import re

import requests
from bs4 import BeautifulSoup

import dedup
import iapd

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 10

_NAME = r"[A-Z][a-z]+(?:\s[A-Z]\.)?\s[A-Z][a-z]+"
# Case-insensitive scoped to _TITLE only, keeping _NAME case-sensitive — same
# fix as iapd.py's Session 12 bug (blanket re.I let [A-Z] match lowercase,
# defeating the whole point of the capitalization check).
_TITLE = r"(?i:President|Founder|Chief Executive Officer|CEO|Chief Investment Officer|CIO|Managing Partner|Managing Director)"

# Named groups, not group-position guessing — "Managing Partner"/"Managing
# Director" are themselves two capitalized words, structurally identical to
# _NAME, so guessing which captured group is the name (as opposed to
# explicitly tagging them) breaks exactly like it did in iapd.py.
_NAME_PATTERNS = [
    re.compile(rf"(?P<name>{_NAME})(?:,|\s+is|\s+was|\s*[-–])\s*(?:the\s+)?(?P<title>{_TITLE})"),
    re.compile(rf"(?P<title>{_TITLE})\s*(?:of|at)?\s*:?\s*(?P<name>{_NAME})"),
]


def search_web(query: str, max_results: int = 10) -> list[tuple[str, str]]:
    """Returns list of (title, description) snippet pairs from Startpage."""
    try:
        resp = requests.get(
            "https://www.startpage.com/sp/search", params={"query": query},
            headers=HEADERS, timeout=_TIMEOUT,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        out = []
        for res in soup.select(".result")[:max_results]:
            title_el = res.select_one(".result-title")
            desc_el = res.select_one(".description")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            desc = desc_el.get_text(" ", strip=True) if desc_el else ""
            if title or desc:
                out.append((title, desc))
        return out
    except requests.RequestException:
        return []


def find_person_via_search(firm_name: str) -> tuple[str, str] | None:
    """Search for a named senior person at a firm. Requires the firm's own
    (canonicalized) first token to appear in the same result blob as the
    extracted name. Returns (name, title) or None."""
    tokens = dedup.canonicalize(firm_name).split()
    if not tokens:
        return None
    firm_token = tokens[0]

    query = f"{firm_name} president founder CEO chief investment officer managing partner"
    for title, desc in search_web(query):
        text = f"{title} {desc}"
        if firm_token not in text.lower():
            continue
        for pattern in _NAME_PATTERNS:
            m = pattern.search(text)
            if m:
                gd = m.groupdict()
                name = gd.get("name")
                if name and iapd.looks_like_real_name(name):
                    return name.strip(), (gd.get("title") or "").strip()
    return None
