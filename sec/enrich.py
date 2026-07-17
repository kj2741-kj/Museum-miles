"""
Email discovery + free verification, scoped to whatever the dashboard currently
has filtered — not the whole prospects.db. Per prospect:

1. Named senior person + firm contact recovery: if the firm has a CRD, fetch
   its SEC Form ADV Part 2 ("Firm Brochure") via iapd.py once and pull out (a)
   a named senior person from its prose (key-person risk disclosure,
   management persons who are also broker-dealer reps, etc — best-effort, not
   every brochure names anyone), and (b) the firm's real website/email from
   the cover page — used when the ADV bulk file listed a social-media URL
   instead of the firm's actual site.
2. Discovery: if a person was found, guess personalized emails (first.last@,
   flast@, ...) against the (possibly recovered) domain. Otherwise scrape the
   firm's own website for mailto: links / email addresses, falling back to
   generic mailboxes (info@, contact@, ir@). If a scraped/guessed email's
   local-part looks like it encodes a person's name (either "first.last@" or
   ending in the firm's own likely-founder surname, e.g. "eeagan@" at "Eagan
   Capital"), that's surfaced as a best-guess contact name — not asserted as
   fact, just a label. (Automated web search for named executives was tried —
   tested against DuckDuckGo, Bing, and Google, via both plain HTTP and full
   Playwright browser rendering — and all three block/CAPTCHA automated
   requests. Not viable without a paid search API or CAPTCHA-solving service,
   so this was dropped in favor of the two reliable levers below.)
3. Verification: syntax check -> MX record lookup -> SMTP RCPT TO handshake
   (no message is ever sent). No paid services (Hunter.io/Apollo) involved.
4. Every prospect also gets a LinkedIn people-search URL (firm name +
   location, where registered) — not scraped, just a link for Mayank to
   manually confirm a contact himself when automated discovery can't name
   anyone.
"""
from __future__ import annotations
import os
import re
import smtplib
import socket
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import dns.resolver
import requests
from bs4 import BeautifulSoup

from sec import db
from sec import dedup
from sec import iapd
from core import net_status
from sec import team_page
from core import linkedin_url

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MuseumMileResearch/1.0; kj2741@nyu.edu)"}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_GENERIC_LOCALPARTS = {"info", "contact", "hello", "ir", "support", "admin", "office"}
_SUBPATHS = ["", "contact", "about"]
_PAGE_TIMEOUT = 5
_SMTP_TIMEOUT = 5
_MAX_SMTP_ATTEMPTS = 2  # cap worst-case SMTP time per prospect

# 2026-07-13: outbound port 25 confirmed blocked on this network (both IPv4
# and IPv6 connect attempts to a real MX time out after the full 5s each,
# every single time) -- every RCPT-TO handshake is now guaranteed to fail
# while still paying its full timeout cost. Set SKIP_SMTP_VERIFY=1 in the
# environment to skip the doomed handshake entirely (still does syntax + MX
# lookup, just never dials port 25) until port 25 access is confirmed
# restored on this network.
_SKIP_SMTP_VERIFY = os.environ.get("SKIP_SMTP_VERIFY") == "1"

# Not a firm's own domain — never scrape or pattern-guess against these.
_NON_FIRM_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "wikipedia.org", "bloomberg.com", "crunchbase.com",
}

# Regulatory-site domains SEC brochures reference in their own required
# disclosure boilerplate (e.g. "see www.adviserinfo.sec.gov") -- PDF text
# extraction doesn't always render this identically across documents (font/
# kerning quirks), so real live variants found include "adviserinfo.sec.gov",
# "advisorinfo.sec.gov" (typo'd extraction), "adviser.sec.gov" (missing
# "info"), and "adviserinfo.sec.gov.The" (no space before the next sentence,
# so the domain-shaped regex glued the next word on). A suffix check catches
# the first three but not the fourth (doesn't END in "sec.gov"); substring
# containment catches all of them, since no legitimate firm domain would
# ever contain "sec.gov" for any other reason. Found live 2026-07-16 via a
# direct-reproduction test on stuck records (e.g. "info@advisorinfo.sec.gov"
# guessed for Jacobs & Company LLC).
_NON_FIRM_DOMAIN_MARKERS = ("sec.gov",)


def _is_non_firm_domain(domain: str) -> bool:
    return domain in _NON_FIRM_DOMAINS or any(marker in domain for marker in _NON_FIRM_DOMAIN_MARKERS)


# Some ADV filers put descriptive text in the "website" field instead of a
# real URL (e.g. "www.guidestone.org  (organization web site that contains
# links to all guidestone affiliates' websites)"). Treating that as a
# hostname and handing it to requests crashes deep inside urllib3's URL/IDNA
# parsing with a LocationParseError that isn't a requests.RequestException
# subclass — it isn't caught by the try/except in _find_emails_on_page and
# took down an entire state's enrichment batch. Validate the shape of a
# domain before ever using it as one.
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def _domain_of(website: str | None) -> str | None:
    if not website:
        return None
    website = website.strip()
    base = website if website.lower().startswith("http") else f"http://{website}"
    parsed = urlparse(base)
    domain = (parsed.netloc or parsed.path.split("/")[0]).lower().replace("www.", "")
    if not domain or _is_non_firm_domain(domain):
        return None
    if not _DOMAIN_RE.match(domain) or any(len(label) > 63 for label in domain.split(".")):
        return None
    return domain


def _find_emails_on_page(url: str) -> set[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=_PAGE_TIMEOUT)
        if resp.status_code != 200:
            return set()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        # Best-effort scrape of an arbitrary external URL — a malformed
        # host, TLS failure, or any other one-off error here must never
        # take down the whole enrichment batch (requests.RequestException
        # alone doesn't cover every failure mode, e.g. LocationParseError).
        net_status.mark_if_network_error(e)
        return set()

    found = set()
    for a in soup.select('a[href^="mailto:"]'):
        addr = a["href"].split("mailto:")[-1].split("?")[0].strip()
        if addr:
            found.add(addr.lower())
    # get_text() with no separator concatenates adjacent text nodes with
    # nothing between them — found live: a "Contact" label sitting right
    # next to "ted.potter@iqcio.com" in the DOM (no whitespace, different
    # elements) rendered as "Contactted.potter@iqcio.com", which the regex
    # then swallowed whole as if "Contact" were part of the address.
    found.update(m.lower() for m in _EMAIL_RE.findall(soup.get_text(" ", strip=True)))
    return found


def discover_emails_from_website(website: str) -> list[str]:
    """Crawl a firm's homepage + a couple of common subpages for email addresses,
    preferring addresses that live on the firm's own domain."""
    domain = _domain_of(website)
    if not domain:
        return []
    base = website if website.lower().startswith("http") else f"http://{website}"

    found: set[str] = set()
    for sub in _SUBPATHS:
        found |= _find_emails_on_page(urljoin(base.rstrip("/") + "/", sub))
        if found:
            break  # stop at the first page that yields anything

    on_domain = [e for e in found if domain in e.split("@")[-1]]
    return sorted(on_domain or found)


def _rank_email(email: str) -> int:
    """Lower ranks first: named-person addresses over generic mailboxes."""
    return 1 if email.split("@")[0].lower() in _GENERIC_LOCALPARTS else 0


def guess_generic_emails(domain: str) -> list[str]:
    return [f"{lp}@{domain}" for lp in ("info", "contact", "ir")]


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def guess_personal_emails(name: str, domain: str | None) -> list[str]:
    if not domain:
        return []
    # Found live 2026-07-13 (individual-search API result "Hugo R Sanchez
    # II"): taking the literal last word as the surname produced
    # "hugo.ii@..." — the suffix "II", not his actual surname "Sanchez".
    parts = [p.strip(".,") for p in name.lower().split()]
    parts = [p for p in parts if p not in _NAME_SUFFIXES]
    if len(parts) < 2:
        return []
    first, last = parts[0], parts[-1]
    return [
        f"{first}.{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first[0]}.{last}@{domain}",
    ]


def guess_email_with_known_pattern(name: str, domain: str, known_name: str, known_email: str) -> str | None:
    """Once we have one confirmed real email at a firm (SMTP-verified, or
    directly disclosed by the firm itself), figure out which of the 4
    guess patterns it actually matches (first.last@ / flast@ / firstlast@
    / f.last@), then apply that SAME pattern to a different person at the
    same firm. A company's email convention is almost always shared
    firm-wide, so this is meaningfully more confident than guessing each
    person's pattern independently from scratch — the whole idea behind
    trying it is that a confirmed real example beats a blind default.
    Returns None if the known email doesn't match any of the 4 known
    patterns (e.g. a nickname or an unrelated format), or if `name` is a
    single word (no pattern is guessable at all)."""
    known_candidates = guess_personal_emails(known_name, domain)
    known_local = known_email.split("@")[0].lower()
    pattern_index = next(
        (i for i, c in enumerate(known_candidates) if c.split("@")[0].lower() == known_local), None
    )
    if pattern_index is None:
        return None
    new_candidates = guess_personal_emails(name, domain)
    return new_candidates[pattern_index] if pattern_index < len(new_candidates) else None


def infer_name_from_email(email: str, firm_name: str) -> str | None:
    """Best-effort: a scraped/guessed email's local-part sometimes already
    encodes a person's name. Two patterns: (a) "firstname.lastname@" is
    self-evident; (b) local-part ending in the firm's own likely-founder
    surname (the first significant word of the firm name), e.g. "eeagan@"
    at "Eagan Capital Management". Never asserted as fact — just a labeled
    best guess for the dashboard to show, so returns a plain name string."""
    local = email.split("@")[0].lower()
    if local in _GENERIC_LOCALPARTS:
        return None

    m = re.fullmatch(r"([a-z]+)\.([a-z]+)", local)
    if m and len(m.group(1)) > 1 and len(m.group(2)) > 1:
        # "word.word@" isn't always a person — e.g. wealth.compliance@ is a
        # department mailbox. Reuse iapd.py's jargon blocklist so this
        # heuristic doesn't fabricate a "name" out of two role/dept words.
        candidate = f"{m.group(1).capitalize()} {m.group(2).capitalize()}"
        if iapd.looks_like_real_name(candidate):
            return candidate
        return None

    firm_tokens = dedup.canonicalize(firm_name).split()
    if firm_tokens:
        surname = firm_tokens[0]
        if len(surname) > 2 and local != surname and local.endswith(surname):
            prefix = local[: -len(surname)]
            if prefix.isalpha():
                candidate = (
                    f"{prefix.capitalize()} {surname.capitalize()}" if len(prefix) >= 3
                    else f"{prefix.upper()}. {surname.capitalize()}"
                )
                if iapd.looks_like_real_name(candidate):
                    return candidate
    return None


def _smtp_rcpt_check(mx_host: str, email: str) -> tuple[bool, str]:
    try:
        with smtplib.SMTP(mx_host, 25, timeout=_SMTP_TIMEOUT) as smtp:
            smtp.helo("museummilefunds.local")
            smtp.mail("verify@museummilefunds.local")
            code, _ = smtp.rcpt(email)
            if code == 250:
                return True, "smtp accepted"
            if code in (550, 551, 553):
                return False, "smtp rejected"
            return False, f"smtp ambiguous ({code})"
    except (smtplib.SMTPException, socket.error, OSError) as e:
        net_status.mark_if_network_error(e)
        return False, "smtp unreachable"


def verify_email(email: str) -> tuple[bool, str]:
    """Free syntax + MX + SMTP-handshake check, with a catch-all-domain
    guard. Returns (verified, reason). A 250 on a real-looking guess
    doesn't actually confirm the mailbox exists if the mail server accepts
    mail to ANY address at that domain ("catch-all") — right after a real
    250, probes a deliberately-nonexistent address at the same domain
    (same resolved mail server, no extra DNS lookup) and flags the result
    in `reason` rather than silently trusting a false positive. Still
    returns verified=True either way — a catch-all 250 is less certain,
    not proven wrong, so the label stays "verified" but callers can check
    `reason` to show the caveat."""
    if not _EMAIL_RE.fullmatch(email):
        return False, "bad syntax"

    domain = email.split("@")[-1]
    try:
        mx_records = sorted(dns.resolver.resolve(domain, "MX"), key=lambda r: r.preference)
        mx_host = str(mx_records[0].exchange).rstrip(".")
    except Exception as e:
        net_status.mark_if_network_error(e)
        return False, "no MX record"

    if _SKIP_SMTP_VERIFY:
        return False, "smtp verification skipped (port 25 blocked on this network)"

    verified, reason = _smtp_rcpt_check(mx_host, email)
    if verified:
        fake = f"doesnotexist{uuid.uuid4().hex[:10]}@{domain}"
        fake_verified, _ = _smtp_rcpt_check(mx_host, fake)
        if fake_verified:
            reason = "smtp accepted (catch-all domain — less certain)"
    return verified, reason


def _catch_all_note(reason: str) -> str | None:
    if "catch-all" in reason:
        return "Verified, but this domain accepts mail to any address (catch-all) — deliverability is less certain than a normal verified result."
    return None


def _build_secondary_contacts(
    people: list[tuple[str, str]], domain: str, firm_name: str, hq_state: str | None,
    anchor_name: str | None, anchor_email: str | None, source_label: str,
    existing_overrides: dict[str, str] | None = None,
) -> list[dict]:
    """Build secondary-contact records for everyone after the primary
    (up to 9 more per firm, per the 10-contact cap). When a confirmed real
    email is available for the primary (anchor_name/anchor_email — SMTP-
    verified or genuinely personal, never a generic mailbox), infer that
    firm's actual email pattern from it and apply the SAME pattern to each
    secondary contact — a company's convention (first.last@ vs flast@ vs
    firstlast@) is almost always shared firm-wide, so this beats guessing
    each person's pattern independently from scratch. Every candidate
    still gets its own SMTP verification regardless of the pattern match —
    a shared format tells us the likely FORM, not that this specific
    person's mailbox exists.

    existing_overrides (2026-07-17): {person_dedup_key: linkedin_person_override}
    from db.get_contact_overrides() for this same prospect — a user-confirmed
    LinkedIn correction on a secondary contact, carried forward and applied to
    the freshly-built URL rather than being silently lost when this prospect's
    whole secondary-contact list gets rebuilt from scratch."""
    existing_overrides = existing_overrides or {}
    out = []
    for extra_name, extra_title in people:
        candidate = None
        if anchor_email and anchor_name:
            candidate = guess_email_with_known_pattern(extra_name, domain, anchor_name, anchor_email)
        if not candidate:
            guesses = guess_personal_emails(extra_name, domain)
            candidate = guesses[0] if guesses else None

        verified = 0
        notes = None
        if candidate:
            is_ok, reason = verify_email(candidate)
            verified = int(is_ok)
            if is_ok:
                notes = _catch_all_note(reason)

        override = existing_overrides.get(iapd.person_dedup_key(extra_name))
        contact = {
            "contact_name": extra_name, "contact_title": extra_title,
            "email": candidate, "email_verified": verified,
            "email_source": f"{source_label}_pattern_guess" if candidate else None,
            "linkedin_profile_url": linkedin_url.build_person_url(override or extra_name, firm_name, hq_state),
        }
        if override:
            contact["linkedin_person_override"] = override
        if notes:
            contact["notes"] = notes
        out.append(contact)
    return out


def enrich_prospect(prospect: dict) -> dict:
    """Thin wrapper around _enrich_prospect_inner: clears any stale
    thread-local network-error flag before running, then records whether a
    genuine network-level failure (timeout/connection/DNS, as opposed to a
    clean "nothing found") was hit anywhere during this prospect's
    enrichment — so a hotspot/connectivity blip can be told apart from a
    real dead end and re-run later, rather than silently counted as one.
    A thin wrapper (rather than touching every one of the ~10 return points
    inside _enrich_prospect_inner) since the flag needs checking exactly
    once, after everything else has run, regardless of which path returned."""
    net_status.check_and_clear()
    result = _enrich_prospect_inner(prospect)
    result["network_issue"] = int(net_status.check_and_clear())
    return result


def _enrich_prospect_inner(prospect: dict) -> dict:
    """Discover + verify an email for one prospect. Returns fields to update.

    Priority ladder per prospect:
    1. Verified personal-guess email for a named senior person (from IAPD brochure)
    2. Email directly disclosed on the brochure's cover page (verified or not —
       it's self-disclosed by the firm, so trustworthy even unverified)
    3. Unverified personal-guess email for the named senior person
    4. Website scrape (mailto: links / on-page addresses)
    5. Generic pattern-guess (info@/contact@/ir@)
    A named person, even with an unverified guessed email, beats a generic
    mailbox — that's the actual point of the IAPD lookup: a real outreach
    target, not an anonymous inbox.

    If the ADV-listed website is missing or a social-media URL, and the firm
    has a CRD, its brochure's cover page is checked for the firm's real site —
    reused for scrape/generic-guess too when the original website is unusable.
    """
    firm_name = prospect["firm_name"]
    hq_state = prospect.get("hq_state")
    # User-confirmed LinkedIn firm-name correction (see core/linkedin_override.py)
    # takes priority over the filed name so it survives a future re-enrichment
    # pass, not just the one-time fix applied when it was first saved.
    linkedin_firm_name = prospect.get("linkedin_firm_override") or firm_name
    # Fetched once per prospect and threaded into every _build_secondary_contacts()
    # call below so a user-confirmed correction on a secondary contact survives
    # this prospect's whole secondary-contact list being rebuilt from scratch.
    existing_overrides = db.get_contact_overrides(prospect["id"])
    base_fields = {"linkedin_search_url": linkedin_url.build_search_url(linkedin_firm_name, hq_state)}

    original_website = prospect.get("website")
    domain = _domain_of(original_website)
    crd = prospect.get("crd_number")

    brochure = iapd.lookup_brochure(crd) if crd else {}
    # Recorded regardless of which path below ultimately supplies an email
    # — tracks whether Part 2B was actually checked, separate from whether
    # a contact was found by any method (requested for reconciliation).
    base_fields["part2b_status"] = brochure.get("part2b_status")
    effective_website = original_website
    website_fields: dict = {}

    if not domain and brochure.get("website"):
        recovered_domain = _domain_of(brochure["website"])
        if recovered_domain:
            domain = recovered_domain
            effective_website = brochure["website"]
            website_fields = {"website": brochure["website"], "website_source": "ADV_brochure"}

    person_fields: dict = {}
    personal_candidates: list[str] = []
    brochure_people = brochure.get("people") or []
    # Real gap found live (2026-07-13): this used to require `domain` just
    # to enter this block at all, so a real name found via Part 2B or the
    # individual-search API got silently discarded whenever there was no
    # website to guess an email from — Northwestern Mutual Investment
    # Services had 10 real registered individuals, all thrown away for
    # exactly this reason. A name is still useful without an email (the
    # LinkedIn search URL below needs no domain at all), so `person_fields`
    # is now set unconditionally; only the email-guessing steps below stay
    # gated on `domain` actually being available.
    if brochure_people:
        name, title, direct_person_email = brochure_people[0]
        person_fields = {
            "contact_name": name, "contact_title": title,
            "linkedin_profile_url": linkedin_url.build_person_url(prospect.get("linkedin_person_override") or name, linkedin_firm_name, hq_state),
        }
        personal_candidates = guess_personal_emails(name, domain)

        # Rare but real (2026-07-13): Part 2B occasionally discloses the
        # individual's own email directly on their supplement cover page —
        # confirmed live for sole-proprietor firms, where the person IS
        # the firm (e.g. Hoffman, Alan N. Investment Management). Still
        # SMTP-verified like any other candidate: a directly-disclosed
        # email doesn't guarantee the mailbox is still live.
        if direct_person_email:
            verified, reason = verify_email(direct_person_email)
            result = {
                **base_fields, **website_fields, **person_fields,
                "email": direct_person_email, "email_verified": int(verified),
                "email_source": "iapd_brochure_direct", "status": "Enriched",
            }
            if not verified:
                result["notes"] = "Directly disclosed on ADV Part 2B, not SMTP-verified"
            else:
                note = _catch_all_note(reason)
                if note:
                    result["notes"] = note
            secondary = _build_secondary_contacts(
                [(n, t) for n, t, _ in brochure_people[1:]], domain, linkedin_firm_name, hq_state,
                name, direct_person_email, "iapd_brochure", existing_overrides,
            )
            if secondary:
                result["_secondary_contacts"] = secondary
            return result

        for email in personal_candidates[:_MAX_SMTP_ATTEMPTS]:
            verified, reason = verify_email(email)
            if verified:
                result = {
                    **base_fields, **website_fields, **person_fields, "email": email, "email_verified": 1,
                    "email_source": "iapd_brochure_pattern_guess", "status": "Enriched",
                }
                secondary = _build_secondary_contacts(
                    [(n, t) for n, t, _ in brochure_people[1:]], domain, linkedin_firm_name, hq_state, name, email, "iapd_brochure", existing_overrides,
                )
                if secondary:
                    result["_secondary_contacts"] = secondary
                note = _catch_all_note(reason)
                if note:
                    result["notes"] = note
                return result

    # A named person's own guessed email beats a generic firm mailbox — even
    # unverified. Otherwise a specific person's name (e.g. "Vinit Sethi")
    # ends up displayed next to an unrelated info@ address disclosed
    # elsewhere on the same brochure, implying it's his email when it isn't.
    direct_email_is_generic = (
        brochure.get("email")
        and brochure["email"].split("@")[0].lower() in _GENERIC_LOCALPARTS
    )
    if brochure.get("email") and not (person_fields and personal_candidates and direct_email_is_generic):
        direct_email = brochure["email"]
        verified, reason = verify_email(direct_email)
        name_fields = person_fields
        if not name_fields:
            inferred_name = infer_name_from_email(direct_email, firm_name)
            if inferred_name:
                name_fields = {
                    "contact_name": inferred_name,
                    "linkedin_profile_url": linkedin_url.build_person_url(prospect.get("linkedin_person_override") or inferred_name, linkedin_firm_name, hq_state),
                }
        result = {
            **base_fields, **website_fields, **name_fields, "email": direct_email,
            "email_verified": int(verified), "email_source": "iapd_brochure_direct",
            "status": "Enriched",
        }
        # Only usable as a pattern anchor for secondary contacts when it's
        # genuinely a personal email (a real name is attached and it's not
        # a generic firm mailbox like info@) — "info@firm.com" reveals
        # nothing about anyone's actual personal address format.
        anchor_name = name_fields.get("contact_name") if not direct_email_is_generic else None
        secondary = _build_secondary_contacts(
            [(n, t) for n, t, _ in brochure_people[1:]], domain, linkedin_firm_name, hq_state, anchor_name, direct_email, "iapd_brochure", existing_overrides,
        )
        if secondary:
            result["_secondary_contacts"] = secondary
        if not verified:
            result["notes"] = "Directly disclosed on ADV brochure, not SMTP-verified"
        else:
            note = _catch_all_note(reason)
            if note:
                result["notes"] = note
        return result

    if person_fields:
        # Reached with a real name but no email either way — either the
        # guess(es) never verified, or (the gap fixed 2026-07-13) there
        # was no domain at all to guess against. Surface the name
        # regardless: a confirmed real person is still useful for LinkedIn
        # outreach even with zero email information attached.
        result = {
            **base_fields, **website_fields, **person_fields,
            "email": personal_candidates[0] if personal_candidates else None,
            "email_verified": 0,
            "email_source": "iapd_brochure_pattern_guess" if personal_candidates else None,
            "status": "Enriched",
            "notes": (
                "Guessed from named senior person found in ADV brochure, not SMTP-verified"
                if personal_candidates else
                "Named contact found via SEC filing — no usable website to guess an email from"
            ),
        }
        # No confirmed anchor here (the primary's own guess never
        # verified, or there was no domain at all) — secondary contacts
        # still each get their own SMTP attempt when a domain exists.
        secondary = _build_secondary_contacts(
            [(n, t) for n, t, _ in brochure_people[1:]], domain, linkedin_firm_name, hq_state, None, None, "iapd_brochure", existing_overrides,
        )
        if secondary:
            result["_secondary_contacts"] = secondary
        return result

    # No brochure-named person — check the firm's own team/leadership page
    # before falling back to anonymous discovery (website scrape / generic
    # pattern-guess). Real example: Cresta Fund Management's /our-team/ page
    # names "Chris D. Rozzell, Managing Partner" in clean structured text
    # that the email-only scrape below never looks at. A team page often
    # lists several real people — the most senior becomes the firm's
    # primary contact (same as before); anyone else found is kept as a
    # secondary contact instead of being discarded outright.
    if domain and effective_website:
        team_people = team_page.find_people_on_website(effective_website)
        if team_people:
            name, title = team_people[0]
            person_fields = {
                "contact_name": name, "contact_title": title,
                "linkedin_profile_url": linkedin_url.build_person_url(prospect.get("linkedin_person_override") or name, linkedin_firm_name, hq_state),
            }
            personal_candidates = guess_personal_emails(name, domain)

            for email in personal_candidates[:_MAX_SMTP_ATTEMPTS]:
                verified, reason = verify_email(email)
                if verified:
                    result = {
                        **base_fields, **website_fields, **person_fields, "email": email, "email_verified": 1,
                        "email_source": "team_page_pattern_guess", "status": "Enriched",
                    }
                    secondary = _build_secondary_contacts(
                        team_people[1:], domain, linkedin_firm_name, hq_state, name, email, "team_page", existing_overrides,
                    )
                    if secondary:
                        result["_secondary_contacts"] = secondary
                    note = _catch_all_note(reason)
                    if note:
                        result["notes"] = note
                    return result
            if personal_candidates:
                result = {
                    **base_fields, **website_fields, **person_fields, "email": personal_candidates[0],
                    "email_verified": 0, "email_source": "team_page_pattern_guess", "status": "Enriched",
                    "notes": "Guessed from named person found on firm's team/leadership page, not SMTP-verified",
                }
                secondary = _build_secondary_contacts(
                    team_people[1:], domain, linkedin_firm_name, hq_state, None, None, "team_page", existing_overrides,
                )
                if secondary:
                    result["_secondary_contacts"] = secondary
                return result

    candidates = discover_emails_from_website(effective_website) if effective_website else []
    source = "website_scrape"
    if not candidates and domain:
        candidates = guess_generic_emails(domain)
        source = "pattern_guess"

    if not candidates:
        return {**base_fields, **website_fields, **person_fields, "status": "Enriched", "notes": "No email found — no usable website"}

    candidates.sort(key=_rank_email)
    for email in candidates[:_MAX_SMTP_ATTEMPTS]:
        verified, reason = verify_email(email)
        if verified:
            inferred_name = infer_name_from_email(email, firm_name)
            name_fields = (
                {"contact_name": inferred_name, "linkedin_profile_url": linkedin_url.build_person_url(prospect.get("linkedin_person_override") or inferred_name, linkedin_firm_name, hq_state)}
                if inferred_name else {}
            )
            return {
                **base_fields, **website_fields, **person_fields, **name_fields,
                "email": email, "email_verified": 1, "email_source": source, "status": "Enriched",
            }

    best = candidates[0]
    inferred_name = infer_name_from_email(best, firm_name)
    name_fields = (
        {"contact_name": inferred_name, "linkedin_profile_url": linkedin_url.build_person_url(prospect.get("linkedin_person_override") or inferred_name, linkedin_firm_name, hq_state)}
        if inferred_name else {}
    )
    return {
        **base_fields, **website_fields, **person_fields, **name_fields,
        "email": best, "email_verified": 0, "email_source": source,
        "status": "Enriched", "notes": "Email found but not SMTP-verified",
    }


def enrich_prospects(
    prospect_ids: list[int], progress_callback=None, max_workers: int = 8, use_processes: bool = False,
) -> dict:
    """Run discovery+verification for the given prospect ids, updating prospects.db.
    Skips prospects that already have an email on file. Network calls (page fetch,
    SMTP handshake) run concurrently across prospects since they're I/O-bound;
    the SQLite writes happen sequentially back on the calling thread.

    use_processes: when the real bottleneck is CPU-bound work (e.g. pdfplumber/
    pdfminer brochure parsing with SMTP off), threads don't parallelize it across
    cores due to the GIL. ProcessPoolExecutor does. enrich_prospect() does no DB
    access itself (returns a plain dict, written back here on the calling thread/
    process), so this is a safe swap -- no shared SQLite connection crosses the
    process boundary. Defaults to False (threads) since most callers are I/O-bound
    (SMTP-heavy) where thread concurrency is already the right tool."""
    results = {"enriched": 0, "verified": 0, "no_email": 0, "skipped": 0}

    to_process = []
    for pid in prospect_ids:
        prospect = dict(db.get_prospect(pid))
        if prospect.get("email"):
            results["skipped"] += 1
        else:
            to_process.append((pid, prospect))

    done = 0
    executor_cls = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
    with executor_cls(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich_prospect, p): pid for pid, p in to_process}
        for future in as_completed(futures):
            pid = futures[future]
            try:
                updates = future.result()
            except Exception:
                # One prospect's unhandled failure must never take down the
                # rest of the batch — this is exactly what cost TX ~886
                # skipped prospects when a single malformed website crashed
                # the whole state's run. Leave it as "New" so a future pass
                # retries it instead of silently losing it.
                results["no_email"] += 1
                done += 1
                if progress_callback:
                    progress_callback(done, len(to_process))
                continue
            secondary_contacts = updates.pop("_secondary_contacts", None)
            db.update_prospect(pid, **updates)
            if secondary_contacts is not None:
                db.replace_contacts(pid, secondary_contacts)
            if updates.get("email"):
                results["enriched"] += 1
                if updates.get("email_verified"):
                    results["verified"] += 1
            else:
                results["no_email"] += 1
            done += 1
            if progress_callback:
                progress_callback(done, len(to_process))
    return results


def reverify_emails(prospect_ids: list[int], progress_callback=None, max_workers: int = 8) -> dict:
    """Re-run ONLY the free MX+SMTP verification check against the email
    already on file — does not search for a different address, does not
    touch discovery/scraping/IAPD at all. Mail servers can be temporarily
    unreachable or rate-limit a first attempt; this is a cheap retry. Skips
    prospects with no email, or one already marked verified."""
    results = {"checked": 0, "now_verified": 0, "still_unverified": 0, "skipped": 0}

    to_process = []
    for pid in prospect_ids:
        prospect = dict(db.get_prospect(pid))
        if not prospect.get("email") or prospect.get("email_verified"):
            results["skipped"] += 1
        else:
            to_process.append((pid, prospect["email"]))

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(verify_email, email): (pid, email) for pid, email in to_process}
        for future in as_completed(futures):
            pid, email = futures[future]
            verified, reason = future.result()
            if verified:
                db.update_prospect(pid, email_verified=1, notes=None)
                results["now_verified"] += 1
            else:
                results["still_unverified"] += 1
            results["checked"] += 1
            done += 1
            if progress_callback:
                progress_callback(done, len(to_process))
    return results
