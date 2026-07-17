"""LinkedIn Matched Audiences contact-list CSV export (2026-07-16).

Column format verified directly against LinkedIn's own official template
(fetched live from https://content.linkedin.com/content/dam/help/lms/en-us/
LinkedIn_Ads_Contact_Match_Template.csv), not guessed or taken from
third-party docs:

    email,firstname,lastname,jobtitle,employeecompany,country,googleaid

Per LinkedIn's own published requirements (linkedin.com/help/lms/answer/
a1489764, a424450), a row only needs ONE of these to be matchable:
email OR (firstname + lastname) OR googleaid. We never collect Google
Advertising IDs, so every row here relies on email and/or name.

Scope: only NAMED contacts (a real person, not a generic mailbox) are
included -- LinkedIn's matching works by finding a member whose profile
email matches, and a generic info@/compliance@ address is very unlikely to
be tied to any one member's profile, so including it would just be noise
against LinkedIn's minimum-300-matched-member threshold rather than helping
reach it.

Known limitation, stated plainly rather than silently assumed: SEC's ADV
data has no per-contact country field, so `country` defaults to "US" for
every row -- accurate for the large majority of SEC-registered investment
advisers (the population this whole dataset is drawn from), but not
independently verified per contact. Override or blank it out before
uploading if you know a specific contact is based elsewhere.
"""
from __future__ import annotations
import csv
from datetime import date

import pandas as pd

from core import config
from sec import db
from sec.excel_export import _slug, _format_aum

EXPORTS_DIR = config.BASE_DIR / "exports" / "sec"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Exact column order from LinkedIn's own template -- do not reorder or rename.
_LINKEDIN_COLUMNS = ["email", "firstname", "lastname", "jobtitle", "employeecompany", "country", "googleaid"]

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _split_name(name: str) -> tuple[str, str] | None:
    parts = [p.strip(".,") for p in name.split() if p.strip(".,")]
    parts = [p for p in parts if p.lower() not in _NAME_SUFFIXES]
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


def build_filename(states: list[str], aum_range: tuple[float, float] | None) -> str:
    parts = ["linkedin_matched_audience"]
    if states:
        parts.append(_slug("-".join(sorted(states))))
    else:
        parts.append("ALL")
    if aum_range:
        parts.append(f"AUM{_format_aum(aum_range[0])}-{_format_aum(aum_range[1])}")
    parts.append(date.today().isoformat())
    return "_".join(parts) + ".csv"


def build_rows(df: pd.DataFrame) -> list[dict]:
    """df is the already-filtered (AUM/state) prospects DataFrame from the
    dashboard. Pulls every named contact -- primary AND secondary -- for
    those firms, in LinkedIn's exact column format."""
    rows = []

    def _add(name: str | None, email: str | None, title: str | None, firm: str) -> None:
        split = _split_name(name) if name else None
        if not split and not email:
            return  # neither email nor a splittable name -- can't match on anything
        first, last = split if split else ("", "")
        rows.append({
            "email": email or "", "firstname": first, "lastname": last,
            "jobtitle": title or "", "employeecompany": firm, "country": "US", "googleaid": "",
        })

    for _, p in df.iterrows():
        if p.get("contact_name") or p.get("email"):
            _add(p.get("contact_name"), p.get("email"), p.get("contact_title"), p["firm_name"])

    all_contacts = pd.DataFrame([dict(r) for r in db.get_all_contacts()])
    if not all_contacts.empty:
        extra = all_contacts[all_contacts["prospect_id"].isin(df["id"])]
        firm_names = df.set_index("id")["firm_name"]
        for _, c in extra.iterrows():
            if c.get("contact_name") or c.get("email"):
                _add(c.get("contact_name"), c.get("email"), c.get("contact_title"), firm_names.get(c["prospect_id"], ""))

    return rows


def export_csv(df: pd.DataFrame, states: list[str], aum_range: tuple[float, float] | None) -> "Path":
    rows = build_rows(df)
    filename = build_filename(states, aum_range)
    path = EXPORTS_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_LINKEDIN_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path
