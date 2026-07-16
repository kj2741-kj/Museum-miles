"""
Identify a named senior person at a firm via SEC's IAPD system (public, no
scraping in the adversarial sense — just downloading SEC's own PDF filings).

Two things were tried and ruled out first, by actually testing them:
- Form ADV Part 1A (the bulk/compilation report) has explicit "Chief Compliance
  Officer: Name / Email" fields, but SEC redacts the values on the public
  report — confirmed blank across multiple real firms.
- No Schedule A (owners/executive officers) table appears in that report either.

What DOES work: Form ADV Part 2 ("Firm Brochure") is a narrative document, and
firms are required to disclose things like "key person" risk (for funds) or
list management persons who are also registered reps of an affiliated
broker-dealer — both name real senior individuals in prose. Not every firm's
brochure yields a name; this is a best-effort heuristic, not a guarantee.
"""
from __future__ import annotations
import io
import re

import pdfplumber
import requests

from core import net_status

API_HEADERS = {"User-Agent": "Museum Mile Funds research kj2741@nyu.edu"}
# The legacy files.adviserinfo.sec.gov domain 404s on non-browser User-Agents.
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# _NAME must stay case-SENSITIVE — it's how we tell a real proper noun apart
# from ordinary text. A prior version compiled the whole pattern with re.I,
# which made [A-Z] also match lowercase letters, silently defeating this
# check entirely: real bug found live — "Item 6: Portfolio Manager Selection
# and Evaluation" (a table-of-contents line) matched "Portfolio Manager" as
# a _TITLE and "Selection and" as a "name". Fixed by scoping case-
# insensitivity with inline (?i:...) to only the title/connector-word
# portions, and by using named groups instead of guessing after the fact
# which captured group is the name vs. the title — that guessing broke
# whenever a title (e.g. "Portfolio Manager", "Founding Partner") happened
# to also be shaped like two capitalized words, indistinguishable from a
# real name by shape alone.
# _NAME_WORD allows an optional Mc/Mac/O' prefix before the required
# capital+lowercase run — plain [A-Z][a-z]+ alone stops at the second
# internal capital, silently truncating names like "McDonald" -> "Mc" or
# "MacArthur" -> "Mac" (found live: "Bruce Mc", "Holly Mac" in the db).
_NAME_WORD = r"(?:Mc|Mac|O')?[A-Z][a-z]+"
_NAME = rf"{_NAME_WORD}(?:\s[A-Z]\.)?\s{_NAME_WORD}"
_TITLE = r"(?i:Chief \w+ Officer|Managing (?:Member|Partner|Director)|Founder|President|Founding Partner|Portfolio Manager)"

_NAME_PATTERNS = [
    re.compile(
        rf"(?i:founding partner),?\s*(?P<name>{_NAME}),?\s*(?i:who is also (?:our|the firm's))\s*(?P<title>{_TITLE})"
    ),
    re.compile(
        rf"(?P<name>{_NAME}),?\s*(?i:is|serves as)\s*(?i:our|the firm's)\s*(?P<title>{_TITLE})"
    ),
    # Requires "our"/"the firm's" immediately before the title — without it,
    # this matched real but unrelated public figures mentioned in risk
    # disclosures, e.g. "...Russian Federation President Vladimir Putin..."
    # in a sanctions-risk section, nothing to do with the firm's own people.
    re.compile(rf"(?i:our|the firm's)\s+(?P<title>{_TITLE}),?\s*(?P<name>{_NAME})"),
    re.compile(
        rf"(?i:management persons at our firm are registered as registered representatives)[^:]*:\s*(?P<name>{_NAME})"
    ),
]

# Corporate/title/department jargon that can never be part of a real person's
# name — catches a real bug found live: "...reviewed by the Managing
# Director, Chief Compliance Officer, Portfolio Manager or..." is a LIST of
# role titles, not a named person, but pattern 3 above matched "Managing
# Director" as the title and "Chief Compliance" (first two words of the
# SECOND title) as if it were the name. This blocklist rejects any candidate
# name containing one of these words, whether from that failure mode, from a
# company name (e.g. "Deutsche Bank"), or a department ("Investor Relations").
_NON_NAME_WORDS = {
    "chief", "compliance", "executive", "investment", "financial", "operating",
    "officer", "president", "vice", "chairwoman", "chairman", "managing",
    "director", "member", "partner", "partners", "portfolio", "manager",
    "founder", "counsel", "general", "legal", "corporate", "client", "clients",
    "assets", "services", "relations", "investor", "solutions", "business",
    "team", "info", "information", "website", "contact", "telephone", "number",
    "company", "companies", "capital", "advisors", "advisor", "management",
    "securities", "bank", "group", "corp", "fund", "funds", "the", "program",
    "risk", "when", "selection", "evaluation", "us", "www", "consultant",
    "research", "institutional", "street", "external",
    # Found live 2026-07-13 while investigating a reported bad-contact case:
    # "Senior Wealth" (Bahl & Gaynor), "Cincinnati Office" (Wealthquest),
    # "Senior Account" (Treetop Wealth Mgmt) all slipped through because
    # none of these four words were on the list yet.
    "senior", "wealth", "office", "account",
    # Found live 2026-07-13 via team_page.py: "View Bio" (TMB Capital
    # Partners' team page has a "View Bio" button sitting directly between
    # each real name and their title — structurally two capitalized words,
    # so it matched the name pattern and captured ahead of the real name).
    "view", "bio", "read", "learn",
    # Found live 2026-07-13 (also TMB Capital Partners, secondary-contacts
    # test): the same page has a nav-menu list of real names with no title
    # text next to them, immediately followed by an unrelated menu heading
    # "What Makes Us Different / Our Partners" — "Different Our" matched
    # the name shape and "Partners" supplied a plausible-looking title,
    # even though neither is a real person.
    "our", "different", "what", "makes",
    # Found live 2026-07-13 (TMB Capital Partners' Part 2B, all 4 people):
    # brochure layout is "Form ADV Part 2B - Brochure Supplement\nfor\n
    # [Name]" — "Brochure Supplement for Scott Tabor" wrapped so "for"
    # landed alone on its own line, a real English word with real letters
    # so it passed every prior check. Common short prepositions/
    # conjunctions unlikely to ever be part of a real name.
    "for", "and", "with", "by", "to", "at", "in", "on",
    # Found live 2026-07-15 (Mulholland Wealth Advisors' Part 2B, via the
    # process-pool throughput run): a stray cross-reference line like
    # "(See Individual's Disclosure Brochure)" sat right where a name was
    # expected — "Disclosure Brochure)" passed every prior check (two
    # capitalized words, no blocklisted word, every "word" contains a
    # letter even with the trailing paren attached).
    "disclosure", "brochure",
    # Found live 2026-07-16 (Axonic Capital's team page, via the overnight
    # SMTP-review audit): "Human Resources" sat near a "COO" title on the
    # page (same nav-menu/department-label adjacency shape as the earlier
    # TMB Capital "Different Our"/"What Makes" bug) and got stored as if it
    # were a person's name, with "human.resources@..." as the generated
    # email — a real generic department label, not a person, structurally
    # indistinguishable from a name (two capitalized words, no letters
    # missing) until this word pair is on the list. Added the other common
    # corporate-department nav-menu labels in the same family preemptively,
    # same reasoning as the senior/wealth/office/account batch above.
    "human", "resources", "administration", "operations", "marketing",
    "recruiting", "talent", "careers",
}


def looks_like_real_name(name: str) -> bool:
    words = re.sub(r"[.,]", "", name).lower().split()
    if not words or any(w in _NON_NAME_WORDS for w in words):
        return False
    # Found live 2026-07-13 (Wisconsin Wealth Advisors' Part 2B): a PDF
    # encoding artifact from an accented character ("Renée") split a lone
    # replacement-character glyph onto its own line just before the real
    # name — the blocklist alone doesn't catch pure garbage since a
    # symbol-only "word" was never on the jargon list. Every word must
    # contain at least one plain Latin letter.
    return all(re.search(r"[a-zA-Z]", w) for w in words)


_COVER_PAGE_CHARS = 1500  # website/email disclosures live on the cover page
_WEBSITE_RE = re.compile(r"(?:https?://|www\.)[a-zA-Z0-9][a-zA-Z0-9\-.]*\.[a-zA-Z]{2,}", re.I)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Boilerplate domains that show up in the SEC-required disclaimer text, not the firm's own site.
_NON_FIRM_DOMAINS = {"sec.gov", "adviserinfo.sec.gov", "iard.com", "finra.org"}


# Real bug found live (Psi Capital Management, 2026-07-13): blindly taking
# the first brochuredetails entry picked a Wrap Fee Program Appendix instead
# of the actual Part 2A/2B disclosure brochure — a short fee-schedule
# document that would essentially never name a person. A firm can have
# several brochure entries (wrap fee appendices, exhibits, the real
# disclosure brochure); score each by name and prefer the real one.
_BAD_BROCHURE_WORDS = ("wrap", "appendix", "exhibit")


def _brochure_score(name: str) -> int:
    name = (name or "").lower()
    if any(w in name for w in _BAD_BROCHURE_WORDS):
        return 0
    if "2a/2b" in name or "2a / 2b" in name:
        return 3  # combined document — richest source, has both parts
    if "2a" in name or "adv" in name:
        return 2
    return 1  # unrecognized naming, still worth trying over nothing


def get_brochure_version_id(crd: str) -> int | None:
    """Look up the best Part 2 brochure filing id for a firm, if any — the
    real disclosure brochure (preferring a combined 2A/2B document), not a
    wrap-fee appendix or exhibit that happens to be listed first."""
    try:
        resp = requests.get(
            f"https://api.adviserinfo.sec.gov/search/firm/{crd}", headers=API_HEADERS, timeout=10
        )
        resp.raise_for_status()
        import json
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return None
        content = json.loads(hits[0]["_source"]["iacontent"])
        details = content.get("brochures", {}).get("brochuredetails") or []
        if not details:
            return None
        best = max(details, key=lambda d: _brochure_score(d.get("brochureName", "")))
        return best["brochureVersionID"]
    except (requests.RequestException, KeyError, ValueError) as e:
        net_status.mark_if_network_error(e)
        return None


# Removed the earlier 15-page cap entirely (2026-07-13): Part 2B ("Brochure
# Supplement" — the section that actually names individual advisory
# personnel) is appended after Part 2A in a combined document and was
# getting silently truncated — found live at page 19 of 20 (Hoffman, Alan
# N. Investment Management) and pages 26/30/32 of 36 (Vigil Wealth
# Management, 3 people). Most real brochures are well under 100 pages; this
# ceiling exists only to guard against a genuinely pathological/corrupted
# PDF, not to limit real content.
_MAX_BROCHURE_PAGES = 200


def fetch_brochure_text(brochure_version_id: int) -> str | None:
    """Download a Part 2 brochure PDF and extract its text."""
    url = (
        "https://files.adviserinfo.sec.gov/IAPD/Content/Common/crd_iapd_Brochure.aspx"
        f"?BRCHR_VRSN_ID={brochure_version_id}"
    )
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
        if resp.status_code != 200 or not resp.content:
            return None
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages[:_MAX_BROCHURE_PAGES])
    except Exception as e:
        net_status.mark_if_network_error(e)
        return None


def extract_senior_person(text: str) -> tuple[str, str] | None:
    """Best-effort extraction of a named senior person + title from brochure prose.
    Returns (name, title) or None — not every brochure names anyone this way."""
    for pattern in _NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            gd = m.groupdict()
            name = gd.get("name")
            if name:
                # _NAME's \s (needed to span a PDF line-wrap mid-name) can
                # capture a literal newline — collapse to a single space so
                # "Anne\nCampbell" reads as "Anne Campbell", not broken text.
                name = re.sub(r"\s+", " ", name).strip()
                title = re.sub(r"\s+", " ", gd.get("title") or "").strip()
                if looks_like_real_name(name):
                    return name, title
                # Jargon/title-list false positive (e.g. "Chief Compliance"
                # from a list of role titles) — try the next pattern instead
                # of trusting this match.
                continue
    return None


# Part 2B ("Brochure Supplement") is a separate, SEC-mandated document per
# advisory person, usually appended after Part 2A in the same combined PDF.
# Far more reliable than the narrative Part 2A patterns above: one document
# per person, name directly under a predictable heading, often with their
# own title right after. A firm can have several (Vigil Wealth Management
# has 3 real people this way) — this is the main source that makes multiple
# real contacts per firm possible, not just a single best guess.
_PART2B_HEADING_RE = re.compile(r"form\s+adv\s+part\s+2b", re.I)
_PART2B_TITLE_KEYWORDS = (
    "officer", "director", "president", "partner", "advisor", "adviser",
    "manager", "counsel", "analyst", "associate", "principal", "founder",
    "vice president", "relations", "representative", "owner", "member",
)


def _looks_like_title_line(line: str) -> bool:
    low = line.lower()
    return any(kw in low for kw in _PART2B_TITLE_KEYWORDS)


def extract_part2b_people(text: str) -> list[tuple[str, str, str]]:
    """Extract every named individual from Part 2B sections of a brochure.
    Returns (name, title, email) triples in document order, title/email as
    "" when not confidently found. Title extraction is deliberately
    conservative (safer to under-extract than mistake the firm's own
    name — which sits right where a title would in some brochure layouts,
    e.g. Hoffman's — for one). Email is genuinely rare here: SEC brochures
    almost never disclose an individual's personal email — confirmed
    directly (2026-07-13) against real multi-person firms (TMB Capital
    Partners, Vigil Wealth Management) where every Part 2B section repeats
    only the FIRM's shared phone/website, never a personal one. The one
    real exception found is sole-proprietor firms, where the individual
    effectively IS the firm and their Part 2B cover page repeats the same
    firm-level contact block that includes an email (e.g. Hoffman, Alan N.
    Investment Management) — this is captured when present, not assumed."""
    lines = [ln.strip() for ln in text.split("\n")]
    people: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for i, line in enumerate(lines):
        if not _PART2B_HEADING_RE.search(line):
            continue
        # Table-of-contents entries reference "Form ADV Part 2B" too, e.g.
        # "Form ADV Part 2B - Brochure Supplement: Upadhyaya ....... 30" —
        # found live (Vigil Wealth Management) matching the heading pattern
        # and then grabbing whatever unrelated text followed as a fake
        # "name". TOC lines have a dot-leader + trailing page number; a
        # real heading never does.
        if re.search(r"\.{3,}\s*\d+\s*$", line):
            continue
        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        if j >= len(lines):
            continue

        # The name is usually the very next non-blank line, but a PDF
        # encoding glitch can push a garbage line in front of it first
        # (found live: an accented "é" in "Renée" split a lone
        # replacement-character glyph onto its own line) — try a small
        # window instead of giving up on the first miss.
        name_only = None
        for j2 in range(j, min(j + 3, len(lines))):
            if not lines[j2]:
                continue
            candidate = re.sub(r"\s+", " ", re.split(r",", lines[j2], maxsplit=1)[0]).strip()
            if looks_like_real_name(candidate):
                name_only = candidate
                j = j2
                break
        if not name_only or name_only.lower() in seen:
            continue

        title = ""
        for k in range(j + 1, min(j + 3, len(lines))):
            if lines[k] and _looks_like_title_line(lines[k]):
                title = re.sub(r"\s+", " ", lines[k]).strip()
                # A title can wrap onto the next line (e.g. "Managing
                # Director, Private Wealth Advisor, and" / "Chief
                # Compliance Officer") — found live (Vigil Wealth). Append
                # the continuation rather than leave a dangling "and"/"&".
                if title.endswith(("and", "&")) and k + 1 < len(lines) and lines[k + 1]:
                    title = re.sub(r"\s+", " ", f"{title} {lines[k + 1]}").strip()
                break

        # Rare, but real (sole-proprietor firms) — scan this person's own
        # supplement window for an email, stopping at "Item 2" (where the
        # career-history narrative begins) so this never reaches into
        # unrelated later content.
        email = ""
        window_end = j + 1
        while window_end < len(lines) and window_end < j + 25 and not lines[window_end].lower().startswith("item 2"):
            window_end += 1
        window_text = " ".join(lines[j + 1:window_end])
        for m in _EMAIL_RE.finditer(window_text):
            candidate = m.group(0).lower()
            if ".." in candidate or candidate.split("@")[-1] in _NON_FIRM_DOMAINS:
                continue
            email = candidate
            break

        seen.add(name_only.lower())
        people.append((name_only, title, email))

    return people


def lookup_individuals_by_firm(crd: str, limit: int = 10) -> list[tuple[str, str]]:
    """Every SEC-registered individual currently employed at this firm, via
    IAPD's own structured individual-search API — no PDF parsing, so no
    extraction risk, but no title/role field either (just registration
    tenure as a weak seniority proxy, earliest first). Real examples
    (2026-07-13): Elemental Capital Partners (no Part 2B available at all)
    -> exactly 1 individual, "Brian Wu" — essentially unambiguous. Psi
    Capital Management -> 11 individuals ranging from a 2001 registrant to
    a 2024 one, with no way to tell founder from newest hire without this
    tenure ordering — the reason this is used as a lower-confidence source
    than Part 2B/2A, not the primary one."""
    try:
        resp = requests.get(
            "https://api.adviserinfo.sec.gov/search/individual",
            params={"query": "", "firm": crd},
            headers=BROWSER_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
    except (requests.RequestException, ValueError, KeyError) as e:
        net_status.mark_if_network_error(e)
        return []

    ranked = []
    for hit in hits:
        s = hit.get("_source", {})
        first = (s.get("ind_firstname") or "").strip()
        last = (s.get("ind_lastname") or "").strip()
        if not first or not last:
            continue
        middle = (s.get("ind_middlename") or "").strip()
        raw_suffix = (s.get("ind_namesuffix") or "").strip().lower().rstrip(".")
        name = " ".join(p for p in (first, middle, last) if p).title()
        # Found live 2026-07-13 (Schroder Wealth Management): SEC's
        # namesuffix field holds honorifics for some (often foreign)
        # individuals, not just genealogical suffixes — "Janette Clare
        # Saxer" + suffix "MRS" naively appended reads as "...Saxer Mrs",
        # backwards and odd. Only append genealogical ones, and display
        # them correctly ("II" not "Ii" — Python's .title() mangles Roman
        # numerals; found live as "Hugo R Sanchez Ii").
        if raw_suffix in {"ii", "iii", "iv", "v"}:
            name = f"{name} {raw_suffix.upper()}"
        elif raw_suffix in {"jr", "sr"}:
            name = f"{name} {raw_suffix.capitalize()}."
        if not looks_like_real_name(name):
            continue
        tenure = s.get("ind_industry_cal_date_iapd") or "9999-99-99"
        ranked.append((tenure, name))

    ranked.sort(key=lambda t: t[0])
    return [(name, "", "") for _, name in ranked[:limit]]


def extract_firm_contact(text: str) -> dict:
    """Best-effort extraction of the firm's real website/email from the brochure
    cover page — useful when the SEC ADV bulk file listed a social-media URL
    instead of the firm's actual site. Returns {} if nothing usable is found."""
    cover = text[:_COVER_PAGE_CHARS]
    out = {}

    for m in _WEBSITE_RE.finditer(cover):
        url = m.group(0)
        # Real corruption found live (Hoffman, Alan N. Investment
        # Management, 2026-07-13): the source PDF had two overlapping text
        # objects ("Website:" label + "alan") that pdfplumber extracted as
        # character-interleaved garbage — "Websiatlea:n
        # whwofwfm.aannh@inicvloeusdt..ccoomm". This is a PDF rendering
        # defect, not something re-extraction can ever fix (the same
        # broken PDF produces the same garbage every time), so any
        # website/email containing consecutive dots (never valid in a real
        # domain) is rejected outright rather than trusted.
        if ".." in url:
            continue
        # Real corruption found live (Hill Investment Group, 2026-07-13):
        # two mentions of the same URL glued together with no separator
        # (a header + body-text repeat) matched as one continuous string —
        # "www.hillinvestmentgroup.com.www.hillinvestmentgroup.com". A
        # real domain never has a second TLD-shaped ".com"/".net"/etc
        # partway through; reject any match where one shows up more than
        # once.
        if len(re.findall(r"\.(?:com|net|org|io|co|biz|us)\b", url, re.I)) > 1:
            continue
        domain = url.lower().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
        if domain not in _NON_FIRM_DOMAINS:
            out["website"] = url if url.lower().startswith("http") else f"http://{url}"
            break

    for m in _EMAIL_RE.finditer(cover):
        email = m.group(0).lower()
        if ".." in email:
            continue
        if email.split("@")[-1] not in _NON_FIRM_DOMAINS:
            out["email"] = email
            break

    return out


# Agreed cap (2026-07-13): keep up to 10 real contacts per firm rather than
# guessing a single "leader" — large firms can have dozens of registered
# individuals with no title field to rank them by, so storing several
# (primary shown in the dashboard, the rest as secondary contacts) beats
# risking a wrong single guess while also beating discarding real people.
_MAX_PEOPLE_PER_FIRM = 10
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _person_dedup_key(name: str) -> str:
    """First + last word only (suffixes like Jr./III stripped, middle
    names/initials ignored) — Part 2B and the individual-search API often
    format the same person's name differently (e.g. "Alan N. Hoffman" vs.
    "Alan Nathan Hoffman", "David Vigil" vs. "David James Vigil"); found
    live across 2 of the first 4 real firms tested, common enough that
    exact-string dedup isn't good enough."""
    words = [w.strip(".,").lower() for w in name.split()]
    words = [w for w in words if w not in _NAME_SUFFIXES]
    if len(words) < 2:
        return words[0] if words else name.lower()
    return f"{words[0]} {words[-1]}"


def lookup_brochure(crd: str | None) -> dict:
    """End-to-end: CRD -> brochure + individual-registry lookup -> up to
    _MAX_PEOPLE_PER_FIRM named people (deduplicated, most-confident-source
    first) + recovered firm website/email from the brochure cover page.
    Any/all may be missing — every step is best-effort. Returns a dict with
    keys 'people' (list of (name, title) tuples, title '' when unknown),
    'website' (str|None), 'email' (str|None).

    Source priority, most to least confident: Part 2B ("Brochure
    Supplement" — one document per person, name+title predictable and
    explicit, occasionally even a personal email for sole-proprietor
    firms) -> Part 2A narrative (single best-effort regex match on prose)
    -> IAPD's individual-search-by-firm API (every SEC-registered
    individual at the firm, no title/email field, tenure-ordered only —
    used to fill out the roster, not to guess who's senior)."""
    people: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def _add(candidates: list[tuple[str, str, str]]) -> None:
        for name, title, email in candidates:
            key = _person_dedup_key(name)
            if key not in seen and len(people) < _MAX_PEOPLE_PER_FIRM:
                seen.add(key)
                people.append((name, title, email))

    # part2b_status: None = never checked (no CRD, or brochure fetch itself
    # failed — a different failure mode than "checked and found nothing").
    # Tracked separately from whether a contact was ultimately found by any
    # method, for reconciliation.
    result = {"people": [], "website": None, "email": None, "part2b_status": None}
    if not crd:
        return result

    brochure_id = get_brochure_version_id(crd)
    text = fetch_brochure_text(brochure_id) if brochure_id else None

    if text:
        part2b_people = extract_part2b_people(text)
        result["part2b_status"] = "found" if part2b_people else "not_found"
        _add(part2b_people)
        senior = extract_senior_person(text)
        if senior:
            _add([(senior[0], senior[1], "")])
        contact = extract_firm_contact(text)
        result["website"] = contact.get("website")
        result["email"] = contact.get("email")

    if len(people) < _MAX_PEOPLE_PER_FIRM:
        _add(lookup_individuals_by_firm(crd, limit=_MAX_PEOPLE_PER_FIRM))

    result["people"] = people
    return result
