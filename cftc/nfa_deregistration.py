"""NFA deregistration detection (2026-07-16) -- mirrors the SEC side's
ingest_sec_adv.detect_deregistered() exactly in spirit: a full FRESH
re-sweep of NFA's CPO/CTA roster (all 676 bigrams again, NOT resumed via
nfa_sweep_progress -- that table is for incremental ingest of NEW firms,
a deregistration check needs the true current full population every time,
including firms already known), diffed against nfa_firms. Anything
currently on file that no longer appears gets flagged via deregistered_at
-- never deleted, CRM history preserved, same as the SEC side.
"""
from __future__ import annotations
import string
from concurrent.futures import ThreadPoolExecutor, as_completed

from cftc import nfa_api
from cftc import nfa_db


def _all_bigrams() -> list[str]:
    letters = string.ascii_lowercase
    return [a + b for a in letters for b in letters]


def fresh_current_ids(max_workers: int = 20, progress_callback=None) -> tuple[set[str], int]:
    """Full re-sweep of every bigram term, unconditionally (ignores
    nfa_sweep_progress on purpose). Returns (current NFA IDs, error count) --
    a term-level failure doesn't abort the whole check, but the error count
    is surfaced so a high value can be told apart from a genuinely clean run
    (an inflated 'missing' list from network flakiness would be a false
    deregistration signal otherwise)."""
    terms = _all_bigrams()
    ids: set[str] = set()
    errors = 0
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(nfa_api.search_firms_all_pages, t): t for t in terms}
        for future in as_completed(futures):
            try:
                rows = future.result()
                ids.update(r["ENTITY_ID"] for r in rows)
            except Exception:
                errors += 1
            done += 1
            if progress_callback:
                progress_callback(done, len(terms))
    return ids, errors


def detect_deregistered(current_ids: set[str]) -> list[dict]:
    existing = [dict(f) for f in nfa_db.get_firms() if not f["deregistered_at"]]
    missing = [f for f in existing if f["nfa_id"] not in current_ids]
    if missing:
        nfa_db.mark_deregistered([f["id"] for f in missing])
    return missing
