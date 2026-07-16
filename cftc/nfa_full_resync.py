"""Unified NFA resync (2026-07-16) -- the right shape for RECURRING
automation, unlike ingest_nfa_firms.py alone: that script's
nfa_sweep_progress table marks every bigram as permanently "swept" after
the first run, so re-running it never finds newly-registered firms (only
useful for resuming a single interrupted initial sweep). A recurring job
needs a full fresh re-sweep every time, which nfa_deregistration.py already
does for detecting deregistrations -- this extends that same one sweep to
ALSO catch new registrants, so one scheduled run does both jobs from a
single pass over NFA's API instead of two.
"""
from __future__ import annotations
import string
from concurrent.futures import ThreadPoolExecutor, as_completed

from cftc import nfa_api
from cftc import nfa_db


def _all_bigrams() -> list[str]:
    letters = string.ascii_lowercase
    return [a + b for a in letters for b in letters]


def fresh_sweep(max_workers: int = 20, progress_callback=None) -> tuple[dict[str, dict], int]:
    """Full re-sweep of every bigram term, unconditionally. Returns
    ({nfa_id: row}, error_count)."""
    terms = _all_bigrams()
    rows_by_id: dict[str, dict] = {}
    errors = 0
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(nfa_api.search_firms_all_pages, t): t for t in terms}
        for future in as_completed(futures):
            try:
                for row in future.result():
                    rows_by_id[row["ENTITY_ID"]] = row
            except Exception:
                errors += 1
            done += 1
            if progress_callback:
                progress_callback(done, len(terms))
    return rows_by_id, errors


def resync(rows_by_id: dict[str, dict]) -> dict:
    """Diffs the fresh sweep against nfa_firms: upserts genuinely new firms,
    flags existing-but-now-missing ones as deregistered. Never touches
    website/principals data on already-known firms (that's P2/P1's job, not
    this sync's) -- only firm_name/reg_types/membership_status/
    has_reg_actions get refreshed on existing rows, since those are the
    fields this sweep actually observes."""
    existing = {f["nfa_id"]: dict(f) for f in nfa_db.get_firms()}
    new_count = 0
    updated_count = 0

    for nfa_id, row in rows_by_id.items():
        fields = dict(
            firm_name=row["FIRM_NAME"], reg_types=row["CURRENT_REG_TYPES"],
            membership_status=row.get("PROCESSED_MEMBERSHIP_STATUS"),
            has_reg_actions=row.get("HAS_REG_ACTIONS"),
        )
        if nfa_id not in existing:
            nfa_db.upsert_firm(nfa_id, **fields)
            new_count += 1
        else:
            old = existing[nfa_id]
            if old.get("deregistered_at"):
                # A previously-flagged-deregistered firm reappeared (re-
                # registered, or was a transient API blip) -- clear the flag.
                nfa_db.update_firm(old["id"], deregistered_at=None, **fields)
                updated_count += 1

    current_ids = set(rows_by_id.keys())
    still_active = [f for f in existing.values() if not f.get("deregistered_at")]
    missing = [f for f in still_active if f["nfa_id"] not in current_ids]
    if missing:
        nfa_db.mark_deregistered([f["id"] for f in missing])

    return {
        "new_firms": new_count, "reactivated_firms": updated_count,
        "newly_deregistered": len(missing), "total_swept": len(rows_by_id),
    }
