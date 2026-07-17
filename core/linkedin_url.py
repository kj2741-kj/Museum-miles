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

# Same suffix set iapd.py's _person_dedup_key already uses.
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Common formal-first-name -> nickname(s) people actually use on their own
# LinkedIn profile instead of the SEC-filed formal name (e.g. "Bradley Benz"
# filed with the SEC, "Brad Benz" on LinkedIn — real case reported
# 2026-07-17). Bounded, verified-by-inspection list, same philosophy as
# _GEO_URNS: only include pairs we're confident are genuinely common,
# rather than an LLM/algorithmic guess that could invent a wrong nickname
# nobody uses. Used to OR-expand the query, never to replace the formal
# name outright, so a wrong/missing mapping only costs recall, never
# breaks a search that would otherwise have worked.
_NICKNAMES = {
    "bradley": ["brad"], "robert": ["rob", "bob"], "william": ["will", "bill"],
    "richard": ["rick", "rich"], "michael": ["mike"], "christopher": ["chris"],
    "matthew": ["matt"], "daniel": ["dan"], "david": ["dave"],
    "james": ["jim"], "thomas": ["tom"], "charles": ["chuck", "charlie"],
    "joseph": ["joe"], "edward": ["ed"], "jonathan": ["jon"],
    "nicholas": ["nick"], "alexander": ["alex"], "benjamin": ["ben"],
    "andrew": ["andy", "drew"], "anthony": ["tony"], "kenneth": ["ken"],
    "steven": ["steve"], "stephen": ["steve"], "timothy": ["tim"],
    "patrick": ["pat"], "jeffrey": ["jeff"], "gregory": ["greg"],
    "douglas": ["doug"], "samuel": ["sam"], "raymond": ["ray"],
    "lawrence": ["larry"], "frederick": ["fred"], "theodore": ["ted"],
    "vincent": ["vince"], "nathaniel": ["nathan", "nate"],
    "jennifer": ["jen"], "katherine": ["kate", "katie"], "catherine": ["kate", "katie"],
    "margaret": ["meg", "maggie", "peggy"], "deborah": ["deb"],
    "cynthia": ["cindy"], "rebecca": ["becky"], "susan": ["sue"],
    "patricia": ["pat", "patty"], "barbara": ["barb"], "victoria": ["vicki"],
    "stephanie": ["steph"], "jacqueline": ["jackie"], "gerald": ["gerry", "jerry"],
    "harold": ["harry"], "donald": ["don"], "ronald": ["ron"],
    "russell": ["russ"], "walter": ["walt"], "albert": ["al"],
    "arthur": ["art"], "eugene": ["gene"], "francis": ["frank"],
    "leonard": ["leo", "len"], "philip": ["phil"],
}

# Stopwords that make a useless anchor keyword when they land as the "first
# significant word" of a firm name (e.g. "The Suby Group" -> "The" instead
# of "Suby" — real case reported 2026-07-17).
_STOPWORDS = {"the", "a", "an"}


def _firm_token(firm_name: str) -> str:
    words = [w for w in _clean_firm_name(firm_name).split() if w.lower() not in _STOPWORDS]
    return words[0] if words else ""


def _first_last_name(name: str) -> str:
    """First + last word only. LinkedIn's exact-phrase match on the full
    stored name fails whenever it includes a middle name/initial (e.g.
    "Alan N. Hoffman", "Hugo R Sanchez II") but the person's real profile
    just says "Alan Hoffman" / "Hugo Sanchez" — same reduction iapd.py's
    _person_dedup_key already applies for name matching, just not
    previously applied here for the search query itself."""
    words = [w.strip(".,") for w in name.split()]
    words = [w for w in words if w.lower() not in _NAME_SUFFIXES]
    if len(words) < 2:
        return name.strip()
    return f"{words[0]} {words[-1]}"


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
    that says "Greenlight Capital".

    Nickname handling (real case 2026-07-17): "Bradley Benz" filed with the
    SEC, "Brad Benz" on his actual LinkedIn profile. LinkedIn ANDs
    space-separated keywords (same lesson as the old title-keyword bug), so
    an explicit OR-group covering both forms is needed, not just the formal
    name — see _NICKNAMES."""
    first_last = _first_last_name(person_name)
    name_parts = first_last.split()
    if len(name_parts) >= 2:
        first, last = name_parts[0], name_parts[-1]
        nicknames = _NICKNAMES.get(first.lower())
        if nicknames:
            variants = " OR ".join(f'"{n.capitalize()} {last}"' for n in [first] + nicknames)
            keywords = f"({variants})"
        else:
            keywords = f'"{first_last}"'
    else:
        keywords = f'"{first_last}"'
    firm_token = _firm_token(firm_name)
    if firm_token:
        keywords += f" {firm_token}"
    params = {"keywords": keywords, "origin": "GLOBAL_SEARCH_HEADER"}
    geo = _GEO_URNS.get(hq_state) if hq_state else None
    if geo:
        params["geoUrn"] = f'["{geo}"]'
    return "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode(params)
