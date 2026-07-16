"""
LinkedIn people-search URL builder — constructs a normal LinkedIn search URL
for Mayank to click and view in his own logged-in browser. Not scraping:
LinkedIn's search results are never fetched or parsed by this code, only a
URL is built. Same pattern already proven safe in career-monitor's
networking_utils.py.

This is the honest ceiling for automation: when neither the SEC brochure
lookup nor an inferred email finds a named person, this gives Mayank a
one-click way to confirm one himself.

Went through several rounds of real-world testing, none of them successfully
verified against a live account (no login access here). By the third failed
attempt (title keywords needed explicit "OR", not just quoting) it was clear
the title-matching part of the query was the recurring point of failure — so
per direct instruction, dropped it entirely. Now just the firm name (legal
suffix stripped — profiles/company pages rarely include "LLC"/"Inc." in free
text) plus a location filter. Location uses LinkedIn's structured geoUrn
parameter where we have a verified ID (reused from career-monitor's own
GEO_URNS, proven in that project); for states without a verified ID, falls
back to appending the state as plain keyword text rather than guessing an
unverified geoUrn (guessing was the type of mistake that caused the last
three rounds of silent failures).
"""
from __future__ import annotations
import re
import urllib.parse

_LEGAL_SUFFIX_RE = re.compile(
    # Trailing period handled once, outside the group — an alternative like
    # "L\.P\." would consume its own period and leave no word character for
    # \b to anchor against (periods aren't word characters), so periods
    # inside each alternative are optional instead. "Co"/"Company" excluded:
    # too many real firm names end in "& Co." as part of their actual
    # identity, not a generic suffix (e.g. "Donald Smith & Co.").
    r",?\s*\b(L\.?L\.?C|L\.?L\.?P|L\.?P|PLLC|GP|Inc(?:orporated)?|Corp(?:oration)?|Ltd)\.?\s*$",
    re.IGNORECASE,
)

# Verified LinkedIn geoUrn IDs — reused from career-monitor's networking_utils.py
# (that project's own proven set). Only states we have a confirmed ID for;
# guessing unverified IDs for the rest risks another silent-failure round.
_GEO_URNS = {
    "NY": "105080838",
    "CA": "102095887",
    "CT": "101539313",
    "NJ": "105763467",
    "IL": "101318387",
    "TX": "105088894",
    "MA": "100506914",
}


def _clean_firm_name(firm_name: str) -> str:
    """Strip trailing legal-entity suffixes (repeatedly, for names with more
    than one, e.g. "X Capital Management, LLC") — company LinkedIn pages
    rarely include these in free text, so keeping them causes false zero-hits
    on the old exact-phrase-match approach and is unhelpful even without it."""
    name = firm_name.strip()
    while True:
        stripped = _LEGAL_SUFFIX_RE.sub("", name).strip()
        if stripped == name:
            return name or firm_name
        name = stripped


def build_search_url(firm_name: str, hq_state: str | None = None) -> str:
    """Firm-level search: browse people at this firm, filtered to where it's
    registered. No title keywords — that portion was the repeated point of
    failure across three rounds of testing; dropped per direct instruction."""
    clean_name = _clean_firm_name(firm_name)
    params = {"keywords": f'"{clean_name}"', "origin": "GLOBAL_SEARCH_HEADER"}

    geo = _GEO_URNS.get(hq_state) if hq_state else None
    if geo:
        params["geoUrn"] = f'["{geo}"]'
    elif hq_state:
        params["keywords"] += f" {hq_state}"

    return "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode(params)


def build_person_url(person_name: str, firm_name: str, hq_state: str | None = None) -> str:
    """Person-level search: a specific named contact + their firm.

    Real case found 2026-07-13: "Vinit Sethi" at "Greenlight Masters, LLC"
    (a specific fund vehicle SEC-registered under David Einhorn's Greenlight
    Capital) was unfindable because the query required his exact name AND
    the exact phrase "Greenlight Masters" — but people brand themselves by
    the parent firm on LinkedIn, not the legal name of whichever fund
    vehicle they happen to be a listed principal of. Using only the firm's
    first significant word, unquoted, keeps enough relevance filtering
    without that false-negative: "Greenlight" alone still matches a profile
    that says "Greenlight Capital"."""
    firm_token = _clean_firm_name(firm_name).split()[0] if _clean_firm_name(firm_name) else ""
    keywords = f'"{person_name}"'
    if firm_token:
        keywords += f" {firm_token}"
    params = {"keywords": keywords, "origin": "GLOBAL_SEARCH_HEADER"}
    geo = _GEO_URNS.get(hq_state) if hq_state else None
    if geo:
        params["geoUrn"] = f'["{geo}"]'
    return "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode(params)
