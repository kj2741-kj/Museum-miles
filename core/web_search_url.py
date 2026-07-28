"""
Plain Google-search URL builder — the honest ceiling for NFA website
discovery, same "one-click manual assist" pattern as core/linkedin_url.py.

Why this exists (2026-07-27): NFA's own data has no website field (see
cftc/nfa_enrich.py's module docstring), so cftc/nfa_enrich.py has to guess a
domain from the firm name and verify the page content actually matches --
that only resolves ~35% of NFA firms (702/2,007, confirmed live). Automated
search-engine scraping was already tested and ruled out on the SEC side
(Session 9, sec/): DuckDuckGo/Bing/Google all block automated requests, even
through a full Playwright-rendered browser (a literal CAPTCHA wall from
Bing). Not re-tested here since the blocker is the search engines
themselves, not anything specific to NFA firm names.

So, same as LinkedIn person-search: never scrape results, just build a
normal Google search URL for Mayank to click and view himself. Not fetched
or parsed by this code at all.
"""
from __future__ import annotations
import urllib.parse

from core.linkedin_url import _clean_firm_name


def build_firm_website_search_url(firm_name: str, hq_state: str | None = None) -> str:
    """Search query: cleaned (legal-suffix-stripped) firm name + "official
    website", optionally narrowed by state -- same suffix-stripping already
    proven correct for LinkedIn search (reused directly rather than
    duplicating the regex), since a legal suffix like "LLC"/"Inc." rarely
    appears on a firm's own homepage copy either."""
    clean_name = _clean_firm_name(firm_name)
    query = f'"{clean_name}" official website'
    if hq_state:
        query += f" {hq_state}"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
