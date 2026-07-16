"""Client for NFA's undocumented BASIC-system JSON-RPC API (Jayrock), found
2026-07-15 by inspecting basic-search-results.js / basic-profile-firm.js —
not a documented public API like SEC's IAPD, so kept deliberately
conservative (see rate-limit test notes in project memory): low concurrency,
no aggressive polling, back off on any non-200/error response.
"""
from __future__ import annotations
import time

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MuseumMileResearch/1.0; kj2741@nyu.edu)",
           "Content-Type": "application/json"}
SEARCH_URL = "https://www.nfa.futures.org/BasicNet/basic-api/DataHandlerSearch.ashx"
PROFILE_URL = "https://www.nfa.futures.org/BasicNet/basic-api/DataHandler.ashx"
TIMEOUT = 15

CPO = "Commodity Pool Operator"
CTA = "Commodity Trading Advisor"


def _reg_type_clauses(value: str) -> list[str]:
    # Mirrors basic-search-results.js's buildColumnClauses() exactly — the
    # server expects a raw SQL WHERE-fragment string, not a plain value.
    escaped = value.replace("'", "''")
    return [
        f"CURRENT_REG_TYPES = '{escaped}'",
        f"CURRENT_REG_TYPES LIKE '{escaped}, %'",
        f"CURRENT_REG_TYPES LIKE '%, {escaped}, %'",
        f"CURRENT_REG_TYPES LIKE '%, {escaped}'",
    ]


def cpo_cta_filter() -> str:
    """Combined filter matching CPO OR CTA in one request — halves the
    sweep's request count vs. querying each registration type separately.
    Verified live 2026-07-15: correctly returns both types plus firms
    registered as both (and other combos, e.g. also FCM)."""
    clauses = _reg_type_clauses(CPO) + _reg_type_clauses(CTA)
    return "(" + " OR ".join(clauses) + ")"


def _post(url: str, payload: dict, retries: int = 3) -> dict:
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_exc = e
            time.sleep(2 ** attempt)  # back off: 1s, 2s, 4s
    raise last_exc


def search_firms(search_text: str, page_index: int = 0, page_size: int = 50) -> dict:
    """One page of firm search results, filtered to CPO/CTA registrants
    only. Returns {'rows': [...], 'total_count': int, 'total_pages': int}.
    Raises on a malformed/failed response rather than returning something
    that looks like an empty-but-valid result."""
    payload = {
        "id": 1, "method": "getFirmSearchResults",
        "params": [search_text, {
            "pageIndex": page_index, "pageSize": page_size, "totalPages": 0, "totalCount": 0,
            "sort": [{"active": True, "column": "FIRM_NAME", "direction": "asc", "ctrl": "x"}],
            "filters": {"memStatus": "", "regTypes": cpo_cta_filter(), "regActions": ""},
            "filterOptions": {"memStatus": None, "regTypes": None, "regActions": None},
        }],
    }
    d = _post(SEARCH_URL, payload)
    result = d.get("result", {})
    if not result.get("success"):
        raise RuntimeError(f"search_firms({search_text!r}) failed: {result.get('message')}")
    inner = result["result"]["result"]
    return {
        "rows": inner["rows"],
        "total_count": inner["options"]["totalCount"],
        "total_pages": inner["options"]["totalPages"],
    }


def search_firms_all_pages(search_text: str, page_size: int = 50) -> list[dict]:
    """All rows for a search term, paginating until exhausted."""
    first = search_firms(search_text, 0, page_size)
    rows = list(first["rows"])
    for page in range(1, first["total_pages"]):
        more = search_firms(search_text, page, page_size)
        rows.extend(more["rows"])
    return rows


def get_principals(nfa_id: str) -> list[dict]:
    """Real named officers/directors/owners with structured titles — no PDF
    parsing needed. Empty list if the firm has none disclosed."""
    payload = {"id": 1, "method": "getPrincipals", "params": [nfa_id]}
    d = _post(PROFILE_URL, payload)
    result = d.get("result", {})
    if not result.get("success"):
        return []
    return [
        {
            "name": p["NAME"].strip(),
            "title": (p.get("TITLE_NAME") or "").strip() or None,
            "principal_nfa_id": p.get("ENTITY_ID_decrypted"),
            "ten_percent_owner": p.get("TEN_PERCENT_IND") == "YES",
        }
        for p in result["result"]
    ]


def get_profile_bootstrap(nfa_id: str) -> dict:
    """Firm vitals: address, phone, registration history. No email/website
    field exists anywhere in this payload (confirmed by manual inspection,
    2026-07-15) — matches SEC ADV/IAPD's own gap, same downstream handling
    needed (website scrape / pattern-guess)."""
    payload = {"id": 1, "method": "getProfileBootstrap", "params": [nfa_id]}
    d = _post(PROFILE_URL, payload)
    result = d.get("result", {})
    if not result.get("success", True) and "result" not in result:
        return {}
    vitals = result.get("result", {}).get("vitals", {})
    addr = (vitals.get("address") or [{}])[0]
    return {
        "street_1": addr.get("STREET_1_ADDR"),
        "street_2": addr.get("STREET_2_ADDR"),
        "city": addr.get("CITY_NAME"),
        "state": addr.get("STATE_CODE"),
        "country": addr.get("COUNTRY_CODE"),
        "zip_code": addr.get("ZIP_CODE"),
        "phone": addr.get("PHONE_NUM"),
    }
