"""
LinkedIn Matched Audiences COMPANY list CSV export (2026-07-29) -- the
company-level counterpart to sec/linkedin_export.py's contact-list export.

Unlike that file, this format could NOT be verified against LinkedIn's own
template the same rigorous way: the contact-match template is a public
static file (content.linkedin.com/.../LinkedIn_Ads_Contact_Match_Template.csv),
but no equivalent public URL exists for the company-list template -- tried
the obvious filename guess (404) and web-searching for a public copy (none
found). LinkedIn's own help docs (linkedin.com/help/lms/answer/a423102)
state the template is only available by downloading it from inside a
logged-in Campaign Manager session.

Column choice is a best-effort format based on LinkedIn's own DOCUMENTED
REQUIREMENTS (prose, not a verified template): "company name, company
website domain, LinkedIn Page URL, or company email domain... including
multiple data points improves match rates." Uses company name + website
domain (the two fields this project's data actually has), lowercase
no-space headers matching the style LinkedIn's verified contact template
uses. NOT independently confirmed -- if LinkedIn's upload rejects the
header row, download LinkedIn's actual template from Campaign Manager once
and compare; this is a starting point, not a guarantee.
"""
from __future__ import annotations
import csv
from datetime import date

import pandas as pd

from core import config

EXPORTS_DIR = config.BASE_DIR / "exports" / "linkedin"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

_COLUMNS = ["companyname", "domain"]


def build_filename(prefix: str, scope_label: str) -> str:
    from sec.excel_export import _slug  # reuse the same slugging rule
    return f"linkedin_company_list_{prefix}_{_slug(scope_label)}_{date.today().isoformat()}.csv"


def _clean_domain(raw: str | None) -> str:
    """Real bug found live 2026-07-29, testing against actual data: a naive
    strip-scheme-and-www pass let non-firm domains through unchanged -- some
    SEC ADV filers list a LinkedIn company page as their "website" field
    (documented elsewhere in this project), so several real rows came out as
    domain="linkedin.com", which would be actively wrong to upload as that
    firm's domain. Reuses enrich._domain_of() instead of reinventing the
    check -- it already blocklists LinkedIn/Facebook/X/etc and validates
    domain syntax, proven correct elsewhere in this codebase."""
    from sec.enrich import _domain_of
    return _domain_of(raw) or ""


def build_rows(df: pd.DataFrame, name_col: str, domain_col: str | None) -> list[dict]:
    rows = []
    seen: set[tuple[str, str]] = set()
    for _, r in df.iterrows():
        name = r.get(name_col)
        if not name or not isinstance(name, str):
            continue
        domain = _clean_domain(r.get(domain_col)) if domain_col else ""
        key = (name.strip().lower(), domain)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"companyname": name.strip(), "domain": domain})
    return rows


def export_csv(df: pd.DataFrame, name_col: str, domain_col: str | None, filename: str) -> "Path":
    rows = build_rows(df, name_col, domain_col)
    path = EXPORTS_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path
