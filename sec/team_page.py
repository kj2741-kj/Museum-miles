"""
Extends contact discovery beyond the existing homepage/contact/about email
scrape: checks a firm's "our team"/"leadership"/"people" page specifically,
and looks for plain-text "Name Title" pairs, not just email addresses.

Team pages are structured, high-signal listings (not narrative prose like an
SEC brochure), so a much simpler adjacency pattern works here: Name directly
followed by Title, no connector words ("our"/"serves as") needed the way
iapd.py's brochure patterns require them to avoid false positives in
unrelated prose. Real example that motivated this (2026-07-13): Cresta Fund
Management's /our-team/ page has clean text like "Chris D. Rozzell Managing
Partner", "Julie Westbrook General Counsel & Chief Compliance Officer" that
the email-only scraper never looked at, since it only ever tried "",
"contact", "about" and only ever ran an email regex, never a name pattern.
"""
from __future__ import annotations
import re

import requests
from bs4 import BeautifulSoup

from core import net_status
from sec import iapd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MuseumMileResearch/1.0; kj2741@nyu.edu)"}
_TIMEOUT = 8
_TEAM_SUBPATHS = ["team", "our-team", "people", "leadership", "about-us", "our-people"]

_TITLE = (
    r"(?i:Founder|Co-?Founder|Chief Executive Officer|CEO|"
    r"Chief Investment Officer|CIO|Chief Compliance Officer|CCO|"
    r"Chief Operating Officer|COO|Chief Financial Officer|CFO|"
    r"Managing Partner|Managing Director|Managing Member|"
    r"General Counsel(?:\s*(?:&|and)\s*Chief Compliance Officer)?|"
    r"President|Senior Vice President|Vice President|Portfolio Manager|"
    r"Partner|Director|Head|Principal|Analyst|Associate)"
    # Titles like "Director of Research" / "Head of Trading" would
    # otherwise get truncated to just "Director"/"Head" — found live
    # (Sagefield: "Kyle Gavin Director of Research" -> was captured as
    # just "Director"). Optional trailing "of <Word(s)>" clause.
    r"(?: of [A-Z][a-z]+(?:\s[A-Z][a-z]+)*)?"
)
# Name directly adjacent to a title (optional comma/dash between) — team
# pages are structured listings, so the adjacency itself is the signal,
# unlike iapd.py's brochure prose which needs a connector word to avoid
# matching an unrelated name/title pair mentioned in running text.
#
# Real bug found live (TMB Capital Partners, 2026-07-13): a "View Bio"
# button sits directly between each name and title in the rendered text
# ("Scott Tabor View Bio Co-Founder..."), which is itself shaped like a
# name, so it matched the title immediately and the regex skipped right
# past the real name. This optional noise clause absorbs known CTA/button
# text between name and title so the real name is still found — the
# iapd.looks_like_real_name() blocklist (now including "view"/"bio"/"read"/
# "learn") is the actual safety net if a new, unlisted CTA phrase shows up
# elsewhere and slips through unrecognized.
_CTA_NOISE = r"(?:\s*(?:View Bio|Read (?:More|Bio)|Learn More|Full Bio))?"
_PATTERN = re.compile(rf"(?P<name>{iapd._NAME}),?{_CTA_NOISE}\s*[-–]?\s*(?P<title>{_TITLE})")

_MAX_CONTACTS = 10  # aligned with the per-firm contact cap agreed 2026-07-13

# Team pages often list many people; ranked so the most senior ends up
# first (used as the firm's single "primary" contact) with the rest kept
# as secondary contacts (prospect_contacts table). Lower number = more senior.
_SENIORITY = {
    "founder": 0, "co-founder": 0, "cofounder": 0,
    "chief executive officer": 1, "ceo": 1, "president": 1,
    "managing partner": 2, "managing member": 2,
    "chief investment officer": 3, "cio": 3,
    "chief operating officer": 3, "coo": 3,
    "chief financial officer": 3, "cfo": 3,
    "chief compliance officer": 3, "cco": 3,
    "managing director": 4, "partner": 4, "principal": 4,
    "general counsel": 5, "general counsel & chief compliance officer": 5,
    "director": 6, "vice president": 6, "senior vice president": 6,
    "portfolio manager": 6,
    "analyst": 8, "associate": 8,
}


def _seniority(title: str) -> int:
    return _SENIORITY.get(title.lower().strip(), 7)


def find_people_on_website(website: str) -> list[tuple[str, str]]:
    """Best-effort: check a firm's team/leadership/people page for named
    people + titles. Returns up to _MAX_CONTACTS (name, title) pairs, most
    senior first, from the first page that yields anything — empty list if
    none found. Same caveat as every other extraction heuristic here: not
    every site has one of these pages, and this is never guaranteed to find
    everyone (or the right people), just plausible ones."""
    base = website if website.lower().startswith("http") else f"http://{website}"
    base = base.rstrip("/")

    for sub in _TEAM_SUBPATHS:
        url = f"{base}/{sub}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(" ", strip=True)
        except Exception as e:
            net_status.mark_if_network_error(e)
            continue

        seen = set()
        candidates = []
        for m in _PATTERN.finditer(text):
            name = re.sub(r"\s+", " ", m.group("name")).strip()
            title = re.sub(r"\s+", " ", m.group("title")).strip()
            if name.lower() in seen:
                continue
            if iapd.looks_like_real_name(name):
                seen.add(name.lower())
                candidates.append((name, title))

        if candidates:
            candidates.sort(key=lambda c: _seniority(c[1]))
            return candidates[:_MAX_CONTACTS]

    return []


def find_person_on_website(website: str) -> tuple[str, str] | None:
    """Backward-compatible single-result wrapper — the most senior match
    found, if any."""
    people = find_people_on_website(website)
    return people[0] if people else None
