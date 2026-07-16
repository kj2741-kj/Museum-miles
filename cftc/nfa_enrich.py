"""
P2: email discovery for NFA CPO/CTA firms + their principals.

Unlike the SEC ADV track, NFA's own data has no website/email field
anywhere -- confirmed live 2026-07-15 by inspecting the full raw JSON of
getProfileBootstrap (vitals/pools/exemptions/regCounts -- nothing) and
getPrincipals. There is also no NFA equivalent of SEC Form ADV Part 2A/2B
("Firm Brochure" / "Brochure Supplement") to mine text from: CFTC Rule 4.21
(CPOs) / 4.31 (CTAs) require a Disclosure Document to be delivered directly
to prospective pool participants, but it is not filed publicly with NFA the
way ADV brochures are filed with SEC/IAPD -- there is no bulk or per-firm
public source for it. So there's no brochure-style document-mining lever
available on this side at all.

What NFA's data DOES give us, for free, that SEC's doesn't: getPrincipals
returns real, structured, NFA-verified names+titles directly (8,137 named
principals across 2,002/2,006 firms, 99.8% coverage) -- no PDF regex
extraction, so none of the SEC-side name-extraction bugs (TOC lines,
nav-menu text, PDF corruption, etc.) can happen here. The only gap is
website/email, closed two ways, in priority order:

1. Cross-reference against prospects.db (SEC ADV track) by canonicalized
   firm name (dedup.canonicalize -- strips punctuation + legal-entity
   suffixes). A meaningful share of NFA CPO/CTA registrants are ALSO
   SEC-registered RIAs, already enriched with a real website by enrich.py.
   Free, no network call, and higher-precision than guessing.
2. Domain guessing + verification, only when no SEC cross-ref exists: build
   a few candidate domains from the cleaned firm name, fetch each, and only
   trust one whose page text plausibly contains the firm's own name --
   otherwise leave the website blank rather than commit to a wrong guess.
   Inherently less reliable than SEC's approach (which starts from a
   filer-disclosed website); this is the honest ceiling here.

Once a website is established (either way), reuses enrich.py's existing,
already-tested machinery unchanged: discover_emails_from_website() (mailto/
text scrape), guess_personal_emails() (against the REAL principal name),
verify_email() (same SKIP_SMTP_VERIFY-gated Phase-1-discovery-only policy
as the SEC track, per the 2026-07-15 08:44 ET decision).
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from sec import dedup
from sec import enrich
from sec import iapd
from core import linkedin_url
from cftc import nfa_db

_PAGE_TIMEOUT = 5

# NFA's getPrincipals lists every 10%+ owner, not just human officers --
# real example found live 2026-07-15: "GALLAGHER BENEFIT SERVICES INC" and
# "CHARLES ERIC PETERS REVOCABLE TRUST AGREEMENT THE" both came back as
# "principals" of real firms, alongside genuine individuals. iapd.py's own
# _NON_NAME_WORDS blocklist (built for SEC brochure-prose extraction) misses
# these -- "inc"/"trust"/"holdings"/"agreement" were never jargon an SEC
# brochure would produce, so they were never added there. Needs its own
# entity-marker check before treating a "principal" as an emailable person.
_ENTITY_MARKERS = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "corp", "corporation",
    "co", "company", "companies", "trust", "trustee", "trustees", "holdings",
    "partnership", "fund", "funds", "bank", "na", "association", "foundation",
    "pension", "plan", "agreement", "revocable", "irrevocable", "plc", "gp",
}


def _looks_like_person(name: str) -> bool:
    words = re.sub(r"[.,]", "", name).lower().split()
    if not words or any(w in _ENTITY_MARKERS for w in words):
        return False
    return iapd.looks_like_real_name(name)


# Broader than linkedin_url's _LEGAL_SUFFIX_RE on purpose: NFA's roster
# includes many foreign entities (SA, NV, AG, GmbH, Pty Ltd...) that never
# show up in the SEC-only dataset that regex was built against.
_DOMAIN_SUFFIX_RE = re.compile(
    r",?\s*\b(L\.?L\.?C|L\.?L\.?P|L\.?P|LLLP|PLLC|GP|Inc(?:orporated)?|"
    r"Corp(?:oration)?|Ltd|S\.?A\.?|N\.?V\.?|AG|GmbH|PLC|Pty\.?\s*Ltd\.?|"
    r"Co(?:mpany)?)\.?\s*$",
    re.IGNORECASE,
)


def _clean_for_domain(firm_name: str) -> str:
    name = firm_name.strip()
    while True:
        stripped = _DOMAIN_SUFFIX_RE.sub("", name).strip()
        if stripped == name:
            return name or firm_name
        name = stripped


_EXTRA_TLDS = ("net", "org", "co")  # tried after .com fails -- see run_nfa_domain_retry.py


def candidate_domains(firm_name: str, extra_tlds: bool = False) -> list[tuple[str, bool]]:
    """A handful of plausible candidates from the cleaned firm name --
    joined ("xcapital.com"), hyphenated ("x-capital.com"), and first-word-
    only ("x.com"). Boutique CPO/CTA firms are frequently named "X Capital"
    or "X Management" and register the short form of their own name.
    Returns (domain, is_single_word) -- the first-word-only candidate is
    flagged since it needs a stricter content check (see _looks_like_firm_page).

    extra_tlds=True (2026-07-16 overnight retry pass, run_nfa_domain_retry.py):
    also tries .net/.org/.co on the same bases, for firms where every .com
    guess already came back empty -- many boutique CTAs/CPOs use a non-.com
    TLD. Off by default (keeps the original P2 pass's behavior/cost
    unchanged) since it multiplies request count ~4x."""
    clean = _clean_for_domain(firm_name)
    words = re.findall(r"[A-Za-z0-9]+", clean.lower())
    if not words:
        return []
    candidates = [("".join(words), False), ("-".join(words), False), (words[0], True)]
    tlds = ("com",) + _EXTRA_TLDS if extra_tlds else ("com",)
    seen: set[str] = set()
    out = []
    for base, is_single in candidates:
        if base in seen or len(base) < 3:
            continue
        seen.add(base)
        for tld in tlds:
            out.append((f"{base}.{tld}", is_single))
    return out


# Real false positive found live 2026-07-15: "whorton.com" (guessed for
# "WHORTON, JODY CLIFFORD JR") returned a real 200 whose page said "Find
# your @whorton.com email address ... Grab yours now" -- a Hover Realnames
# domain-parking/for-sale template, not the firm's actual site. These pages
# trivially "pass" a firm-name-token check because they dynamically echo
# back whatever domain was requested -- the check was circular for exactly
# the single-surname-domain case it's most needed for. Reject on sight.
_PARKING_PAGE_MARKERS = (
    "domain is for sale", "buy this domain", "this domain may be for sale",
    "realnames", "domain parking", "godaddy", "namecheap", "sedo.com",
    "find your @", "as your email", "checkout the domain",
)


def _page_title(html_text: str) -> str:
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        return (soup.title.string or "").lower() if soup.title and soup.title.string else ""
    except Exception:
        return ""


def _looks_like_firm_page(html_text: str, firm_name: str, require_secondary: bool = False) -> bool:
    lowered = html_text.lower()
    if any(marker in lowered for marker in _PARKING_PAGE_MARKERS):
        return False
    tokens = [
        t for t in re.findall(r"[a-z]{3,}", firm_name.lower())
        if t not in dedup._SUFFIXES
    ]
    if not tokens:
        return False
    if require_secondary:
        # A first-word-only guess like "nrg.com" for "NRG Trading Advisors"
        # is circular against its own token -- ANY page living at nrg.com
        # will mention "nrg" regardless of whether it's really this firm or
        # unrelated NRG Energy. A same-industry unrelated site can ALSO
        # coincidentally share generic secondary vocabulary in 20KB of body
        # text -- real case found live 2026-07-15: "derivative.com" (guessed
        # for "Derivative Path Hedging Solutions Inc") is actually Vontobel
        # Markets, an unrelated Swiss bank's derivatives-trading platform,
        # whose body text plausibly brushes against "path"/"hedging"/
        # "solutions" simply because it's in the same industry -- but its
        # <title> ("Strukturierte Produkte | Vontobel Markets") does not.
        # The <title> tag is a much smaller, higher-precision surface: a
        # real company's own homepage almost always titles itself with its
        # own name, and an unrelated site coincidentally titling itself
        # with the exact right secondary word is far less likely than it
        # merely appearing somewhere in a whole page of body text.
        secondary = tokens[1:3]
        if not secondary:
            return False
        title = _page_title(html_text)
        return bool(title) and any(t in title for t in secondary)
    return any(t in lowered for t in tokens[:3])


def guess_website(firm_name: str, extra_tlds: bool = False) -> str | None:
    """Fetch each candidate domain and only trust one whose page content
    plausibly mentions the firm's own name -- never commit to an unverified
    guess (a wrong domain would poison every downstream email guess)."""
    for domain, is_single in candidate_domains(firm_name, extra_tlds=extra_tlds):
        for scheme in ("https://", "http://"):
            try:
                resp = requests.get(f"{scheme}{domain}", headers=enrich.HEADERS, timeout=_PAGE_TIMEOUT)
            except Exception:
                continue
            if resp.status_code == 200 and _looks_like_firm_page(resp.text[:20000], firm_name, require_secondary=is_single):
                return domain
    return None


def build_sec_crossref_map() -> dict[str, str]:
    """canonical firm name -> website, for every SEC prospect that has one.
    Built once per batch run (not per-firm) -- cheap, in-memory, no repeated
    DB round-trips."""
    from sec import db as sec_db

    out: dict[str, str] = {}
    for row in sec_db.get_all_prospects():
        website = row["website"]
        if not website:
            continue
        key = dedup.canonicalize(row["firm_name"])
        if key and key not in out:
            out[key] = website
    return out


def resolve_website(firm_name: str, sec_crossref: dict[str, str], extra_tlds: bool = False) -> tuple[str | None, str | None]:
    key = dedup.canonicalize(firm_name)
    xref_website = sec_crossref.get(key)
    if xref_website:
        return xref_website, "SEC_ADV_crossref"
    guessed = guess_website(firm_name, extra_tlds=extra_tlds)
    if guessed:
        return guessed, "domain_guess_verified_extra_tld" if extra_tlds else "domain_guess_verified"
    return None, "none_found"


def _match_scraped_to_principal(scraped_emails: list[str], principal_name: str, firm_name: str) -> str | None:
    """If a scraped mailto/text email's local-part already encodes this
    specific principal's name, prefer it over any guess -- it's a real
    address found on the firm's own site, not inferred."""
    for email in scraped_emails:
        inferred = enrich.infer_name_from_email(email, firm_name)
        if inferred and iapd.looks_like_real_name(inferred):
            inferred_parts = set(inferred.lower().split())
            principal_parts = set(principal_name.lower().split())
            if inferred_parts & principal_parts:
                return email
    return None


def _best_verified_candidate(candidates: list[str]) -> tuple[str | None, bool, str]:
    """Try each candidate in order, return the first SMTP-verified one; if
    none verify (including when SMTP checks are disabled), fall back to the
    first candidate unverified -- a named person's best-guess email is still
    worth more for outreach than nothing, same rule as the SEC track."""
    for email in candidates:
        verified, reason = enrich.verify_email(email)
        if verified:
            return email, True, reason
    if candidates:
        return candidates[0], False, "not SMTP-verified"
    return None, False, "no domain to guess against"


def enrich_firm(firm: dict, principals: list[dict], sec_crossref: dict[str, str], extra_tlds: bool = False) -> dict:
    """Returns {'website': ..., 'website_source': ..., 'principals': [{'id', 'email', 'email_verified', 'email_source', 'linkedin_profile_url'}, ...]}."""
    website, website_source = resolve_website(firm["firm_name"], sec_crossref, extra_tlds=extra_tlds)
    domain = enrich._domain_of(website) if website else None
    scraped = enrich.discover_emails_from_website(website) if website else []

    principal_updates = []
    for p in principals:
        if not _looks_like_person(p["name"]):
            # A corporate/trust owner disclosed as a 10%+ "principal" --
            # nothing to email-guess or LinkedIn-search for. Leave as-is
            # (name/title already stored by P1); just mark it as attempted
            # so a rerun doesn't keep retrying it.
            principal_updates.append({
                "id": p["id"], "email": None, "email_verified": 0,
                "email_source": "not_individual", "linkedin_profile_url": None, "notes": None,
            })
            continue

        linkedin_profile_url = linkedin_url.build_person_url(p["name"], firm["firm_name"], firm.get("state"))

        scraped_match = _match_scraped_to_principal(scraped, p["name"], firm["firm_name"]) if scraped else None
        if scraped_match:
            email = scraped_match
            verified, reason = enrich.verify_email(scraped_match)
            source = "website_scrape_named"
        else:
            candidates = enrich.guess_personal_emails(p["name"], domain) if domain else []
            email, verified, reason = _best_verified_candidate(candidates)
            source = "personal_guess" if email else None

        principal_updates.append({
            "id": p["id"],
            "email": email,
            "email_verified": int(verified),
            "email_source": source,
            "linkedin_profile_url": linkedin_profile_url,
            "notes": reason if email and not verified else None,
        })

    return {"website": website, "website_source": website_source, "principals": principal_updates}


def enrich_firms(
    firm_ids: list[int], progress_callback=None, max_workers: int = 8, retry_none_found: bool = False,
) -> dict:
    """Batch driver, mirrors enrich.enrich_prospects()'s shape: I/O-bound
    (website fetch + SMTP), so ThreadPoolExecutor -- no PDF parsing here,
    so no need for enrich.py's ProcessPoolExecutor CPU workaround. Skips
    firms already attempted (website_source IS NOT NULL is the "touched"
    marker, since P1 already set status='Enriched' on every firm -- status
    can't double as the P2 progress marker the way it does on the SEC side).
    A crashed worker's exception is caught per-firm and leaves
    website_source NULL, so a future rerun retries it -- same discipline as
    every other batch script in this project.

    retry_none_found=True (2026-07-16 overnight retry, run_nfa_domain_retry.py):
    inverts the selection to target ONLY firms already marked 'none_found'
    (the original P2 pass's .com-only guess found nothing), and re-attempts
    with extra_tlds=True (.net/.org/.co). A separate mode, not the default,
    since it's a meaningfully more expensive re-guess (~4x requests) meant
    for a dedicated overnight pass, not every routine P2 run."""
    results = {"enriched_website": 0, "no_website": 0, "principals_emailed": 0, "errors": 0, "skipped": 0}
    sec_crossref = build_sec_crossref_map()

    to_process = []
    all_firms = {f["id"]: dict(f) for f in nfa_db.get_firms()}
    for fid in firm_ids:
        firm = all_firms.get(fid)
        if firm is None:
            continue
        wants_this_firm = (
            firm.get("website_source") == "none_found" if retry_none_found
            else not firm.get("website_source")
        )
        if wants_this_firm:
            to_process.append(firm)
        else:
            results["skipped"] += 1

    def _work(firm: dict):
        principals = [dict(p) for p in nfa_db.get_principals_for_firm(firm["id"])]
        return firm, principals, enrich_firm(firm, principals, sec_crossref, extra_tlds=retry_none_found)

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, firm): firm["id"] for firm in to_process}
        for future in as_completed(futures):
            fid = futures[future]
            try:
                firm, principals, outcome = future.result()
            except Exception:
                results["errors"] += 1
                done += 1
                if progress_callback:
                    progress_callback(done, len(to_process))
                continue

            nfa_db.update_firm(fid, website=outcome["website"], website_source=outcome["website_source"])
            if outcome["website"]:
                results["enriched_website"] += 1
            else:
                results["no_website"] += 1
            for pu in outcome["principals"]:
                pid = pu.pop("id")
                nfa_db.update_principal(pid, **pu)
                if pu["email"]:
                    results["principals_emailed"] += 1

            done += 1
            if progress_callback:
                progress_callback(done, len(to_process))
    return results
