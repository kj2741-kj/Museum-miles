"""
SEC Form ADV discovery: finds registered investment advisers (RIAs) who could
be allocators/clients for Museum Mile Funds — CIOs/PMs at CPO/RIA firms,
family offices, and other advisers running private funds.

Data source: sec.gov bulk "Information about Registered Investment Advisers"
feed (Form ADV Part 1A). Public, no API key, no scraping — just a CSV inside
a zip that SEC republishes monthly. SEC requires a descriptive User-Agent on
requests (fair-access policy), not an API key.

Adapted from career-monitor/sec_adv_discovery.py, broadened beyond NY/NJ/CT
and rewired to write into prospects.db instead of a job-search company list.
"""
from __future__ import annotations
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd
import requests

from core import config
from sec import db

HEADERS = {"User-Agent": "Museum Mile Funds research kj2741@nyu.edu"}


def _get_with_retry(url: str, timeout: int, attempts: int = 4) -> requests.Response:
    """SEC.gov intermittently returns 503 under normal load — retry with backoff."""
    last_exc = None
    for i in range(attempts):
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code != 503:
            resp.raise_for_status()
            return resp
        last_exc = requests.exceptions.HTTPError(f"503 on attempt {i + 1}/{attempts}")
        time.sleep(2 ** i)
    raise last_exc

LISTING_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/"
    "information-about-registered-investment-advisers-exempt-reporting-advisers"
)

_CACHE_CSV = config.DATA_DIR / "sec_adv_cache.csv"
_CACHE_META = config.DATA_DIR / "sec_adv_cache_meta.json"

_RAW_DIR = config.DATA_DIR / "raw"
_RAW_DIR.mkdir(exist_ok=True)

_KEEP_COLS = [
    "Primary Business Name", "Organization CRD#", "Main Office City", "Main Office State",
    "Main Office Country", "Website Address", "5A", "Any Hedge Funds", "Any Real Estate Funds",
    "Count of Private Funds - 7B(1)", "5F(2)(c)",
]

# Filename pattern for the non-exempt monthly RIA feed, e.g. ia07012026.zip
_FILENAME_RE = re.compile(r'"(/files/investment/data/[^"]*?/(ia(\d{2})(\d{2})(\d{4})\.zip))"')


def _find_latest_adv_url() -> str:
    """Scrape the SEC listing page for the most recent non-exempt ADV bulk zip URL."""
    resp = _get_with_retry(LISTING_URL, timeout=20)
    candidates = []
    for match in _FILENAME_RE.finditer(resp.text):
        path, _, mm, dd, yyyy = match.groups()
        try:
            date = datetime(int(yyyy), int(mm), int(dd))
        except ValueError:
            continue
        candidates.append((date, path))
    if not candidates:
        raise RuntimeError("Could not find any SEC ADV bulk file link on the listing page.")
    candidates.sort(key=lambda c: c[0])
    return "https://www.sec.gov" + candidates[-1][1]


def _process_and_cache(df: pd.DataFrame, source_label: str, raw_csv_name: str, raw_zip_path=None) -> dict:
    """Shared by refresh_adv_cache() and load_adv_from_upload(): archive the
    full unfiltered data under data/raw/, save the app's lean working cache,
    write cache metadata. Returns the meta dict."""
    raw_csv_path = _RAW_DIR / raw_csv_name
    df.to_csv(raw_csv_path, index=False)

    df = df[[c for c in _KEEP_COLS if c in df.columns]].copy()
    df.to_csv(_CACHE_CSV, index=False)

    meta = {
        "source_url": source_label,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_firms": len(df),
        "raw_zip_path": str(raw_zip_path) if raw_zip_path else None,
        "raw_csv_path": str(raw_csv_path),
    }
    _CACHE_META.write_text(json.dumps(meta, indent=2))
    return meta


def refresh_adv_cache() -> dict:
    """
    Download the latest SEC ADV bulk file, archive the raw zip + full unfiltered
    CSV under data/raw/ for manual inspection later, and save a small filtered
    working cache (only the columns the app uses) so we don't re-download on
    every dashboard load. Returns a status dict.
    """
    url = _find_latest_adv_url()
    resp = _get_with_retry(url, timeout=60)

    raw_name = url.rsplit("/", 1)[-1]  # e.g. ia07012026.zip
    raw_zip_path = _RAW_DIR / raw_name
    raw_zip_path.write_bytes(resp.content)

    with zipfile.ZipFile(BytesIO(resp.content)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            df = pd.read_csv(f, encoding="latin1", low_memory=False)

    return _process_and_cache(df, url, raw_name.replace(".zip", "_full.csv"), raw_zip_path)


def load_adv_from_upload(file_bytes: bytes, filename: str) -> dict:
    """Process a manually-uploaded SEC ADV bulk file (.zip as SEC publishes
    it, or an already-extracted .csv) instead of downloading from SEC.gov —
    for when Mayank already has one on his system, or SEC.gov is temporarily
    unavailable. Same downstream processing as refresh_adv_cache()."""
    lower = filename.lower()
    if lower.endswith(".zip"):
        raw_zip_path = _RAW_DIR / filename
        raw_zip_path.write_bytes(file_bytes)
        with zipfile.ZipFile(BytesIO(file_bytes)) as z:
            csv_name = z.namelist()[0]
            with z.open(csv_name) as f:
                df = pd.read_csv(f, encoding="latin1", low_memory=False)
        raw_csv_name = filename[:-4] + "_full.csv"
    elif lower.endswith(".csv"):
        raw_zip_path = None
        df = pd.read_csv(BytesIO(file_bytes), encoding="latin1", low_memory=False)
        raw_csv_name = filename[:-4] + "_full.csv"
    else:
        raise ValueError("Please upload a .zip or .csv file (the SEC ADV bulk filing format).")

    return _process_and_cache(df, f"manual upload: {filename}", raw_csv_name, raw_zip_path)


def cache_status() -> dict | None:
    """Return cache metadata (source URL, fetch time, row count), or None if never fetched."""
    if not _CACHE_META.exists():
        return None
    return json.loads(_CACHE_META.read_text())


def _clean_money(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.replace(".00", "", regex=False),
        errors="coerce",
    )


def _infer_prospect_type(row) -> str:
    if str(row.get("Any Hedge Funds", "")).strip().upper() == "Y":
        return "Hedge Fund / RIA"
    if str(row.get("Any Real Estate Funds", "")).strip().upper() == "Y":
        return "Real Estate / RIA"
    return "RIA"


def get_adv_candidates() -> list[dict]:
    """
    Return every adviser from the cached SEC ADV data, sorted by AUM descending,
    skipping firms already in prospects.db. No AUM/employee/location filtering
    here — that happens interactively in the dashboard once data is imported.

    Raises FileNotFoundError if refresh_adv_cache() hasn't been run yet.
    """
    if not _CACHE_CSV.exists():
        raise FileNotFoundError("No SEC ADV cache yet — call refresh_adv_cache() first.")

    df = pd.read_csv(_CACHE_CSV, encoding="utf-8", low_memory=False)
    sub = df.copy()
    sub["_aum"] = _clean_money(df["5F(2)(c)"])
    sub["_employees"] = pd.to_numeric(df["5A"], errors="coerce")
    sub = sub.sort_values("_aum", ascending=False, na_position="last")

    existing = db.get_existing_normalized_names()
    out = []
    seen: set[str] = set()
    for _, row in sub.iterrows():
        name = str(row["Primary Business Name"]).strip().title()
        normalized = db.normalize_name(name)
        if not name or normalized in seen or normalized in existing:
            continue
        seen.add(normalized)
        website = row.get("Website Address", "")
        website = website if isinstance(website, str) and website.lower().startswith("http") else None
        crd = row.get("Organization CRD#", "")
        crd = str(int(crd)) if pd.notna(crd) and str(crd).strip() else None
        state = row["Main Office State"]
        if not (isinstance(state, str) and state.strip()):
            # No US state on file — check country before leaving this blank.
            # Genuinely foreign firms (per SEC's own "Main Office Country"
            # field) get bucketed as "Foreign" so they're filterable in the
            # dashboard's State dropdown, instead of silently disappearing
            # from it. Firms where SEC's country field is ALSO blank are
            # left as None — that's a real data gap (mostly small domestic
            # RIAs), not evidence they're foreign, so they must not be
            # mislabeled as "Foreign" (verified 2026-07-13: zero cases of a
            # populated "United States" country paired with a blank state,
            # so this split is clean).
            country = row.get("Main Office Country", "")
            state = "Foreign" if isinstance(country, str) and country.strip() else None
        out.append({
            "firm_name": name,
            "source": "SEC_ADV",
            "prospect_type": _infer_prospect_type(row),
            "crd_number": crd,
            "hq_city": row.get("Main Office City", ""),
            "hq_state": state,
            "website": website,
            "website_source": "SEC_ADV" if website else None,
            "aum": float(row["_aum"]) if pd.notna(row["_aum"]) else None,
            "employees": int(row["_employees"]) if pd.notna(row["_employees"]) else None,
        })
    return out


def ingest_new_prospects() -> dict:
    """Pull all ADV candidates (using cached data) and write new ones into prospects.db."""
    candidates = get_adv_candidates()
    return db.bulk_add_prospects(candidates)


def get_current_crds() -> set[str]:
    """CRD numbers present in the most recently downloaded cache."""
    if not _CACHE_CSV.exists():
        raise FileNotFoundError("No SEC ADV cache yet — call refresh_adv_cache() first.")
    df = pd.read_csv(_CACHE_CSV, encoding="utf-8", low_memory=False)
    crds = pd.to_numeric(df["Organization CRD#"], errors="coerce").dropna().astype(int).astype(str)
    return set(crds)


def detect_deregistered() -> list[dict]:
    """Compare prospects.db against the latest cache: any SEC_ADV-sourced
    prospect whose CRD no longer appears in the bulk file has likely
    deregistered. Flags them (deregistered_at set, nothing deleted) and
    returns the newly-flagged records."""
    current_crds = get_current_crds()
    active = [dict(r) for r in db.get_active_sec_adv_prospects()]
    newly_deregistered = [p for p in active if p["crd_number"] not in current_crds]
    db.mark_deregistered([p["id"] for p in newly_deregistered])
    return newly_deregistered
