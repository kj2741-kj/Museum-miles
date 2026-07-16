"""P0: build the full NFA CPO/CTA firm roster. NFA's BASIC search has no
"list everyone" mode (unlike SEC ADV's bulk file) — only substring search —
so this sweeps every 2-letter combination ('aa'..'zz', 676 terms) as a
covering set guaranteed to match any alphabetic firm name at least once,
filtered server-side to CPO/CTA registrants, and dedupes by NFA ID (the
same firm surfaces under many different bigrams).

Resumable: nfa_sweep_progress tracks which terms are done, so a restart
only processes remaining terms. Deliberately conservative concurrency (5
workers) since this is an undocumented internal endpoint, not a documented
public API — see the 2026-07-15 rate-limit test in project memory (10
concurrent workers showed no strain, this stays well under that).
"""
import string
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from cftc import nfa_api
from cftc import nfa_db

LOG_PATH = "ingest_nfa_firms_log.txt"
MAX_WORKERS = 5


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def all_bigrams() -> list[str]:
    letters = string.ascii_lowercase
    return [a + b for a in letters for b in letters]


def process_term(term: str) -> tuple[str, list[dict], Exception | None]:
    try:
        rows = nfa_api.search_firms_all_pages(term)
        return term, rows, None
    except Exception as e:
        return term, [], e


def main():
    nfa_db.init_db()
    terms = all_bigrams()
    already_swept = nfa_db.get_swept_terms()
    remaining = [t for t in terms if t not in already_swept]
    log(f"Resuming: {len(remaining)}/{len(terms)} bigrams not yet swept")

    start = time.time()
    done = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_term, t): t for t in remaining}
        for future in as_completed(futures):
            term, rows, err = future.result()
            done += 1

            if err is not None:
                errors += 1
                log(f"  ERROR on term {term!r}: {err}")
                # Deliberately NOT marked as swept — a future rerun will retry it,
                # same "leave failed work undone rather than silently lose it"
                # discipline used throughout this project's SEC-side scripts.
            else:
                for row in rows:
                    nfa_db.upsert_firm(
                        row["ENTITY_ID"],
                        firm_name=row["FIRM_NAME"],
                        reg_types=row["CURRENT_REG_TYPES"],
                        membership_status=row.get("PROCESSED_MEMBERSHIP_STATUS"),
                        has_reg_actions=row.get("HAS_REG_ACTIONS"),
                    )
                nfa_db.mark_term_swept(term, total_count=len(rows), pages_fetched=-1)

            if done % 25 == 0 or done == len(remaining):
                elapsed = time.time() - start
                rate = done / elapsed if elapsed else 0
                eta_min = (len(remaining) - done) / rate / 60 if rate else float("inf")
                total_firms = len(nfa_db.get_all_firm_nfa_ids())
                log(f"  {done}/{len(remaining)} terms, {total_firms} unique firms so far, "
                    f"{errors} errors, {elapsed/60:.1f}m elapsed, ETA ~{eta_min:.0f}m")

    total_firms = len(nfa_db.get_all_firm_nfa_ids())
    log(f"DONE: {done} terms processed, {errors} errors, {total_firms} unique firms in nfa_prospects.db")


if __name__ == "__main__":
    main()
