"""
One-off follow-up (2026-07-13): every brochure-derived contact (email_source
iapd_brochure_direct / iapd_brochure_pattern_guess — 7,941 records) was reset
to force fresh re-extraction after fixing three bugs found via a user-reported
bad contact (Vinit Sethi / Greenlight Masters):
  1. enrich.py: a named person's guessed email could get silently overridden
     by an unrelated generic firm mailbox while keeping the person's name.
  2. linkedin_url.py: person search over-constrained on the exact SEC legal
     entity name instead of the firm's actual brand name.
  3. iapd.py: blocklist gaps (senior/wealth/office/account) and a regex bug
     truncating Mc/Mac-prefixed surnames.
This reprocesses just those 7,941 records (not a full state-by-state sweep —
enrich_prospects() runs them directly) so today's fixes are reflected before
the meeting.
"""
import time
from datetime import datetime

from sec import db
from sec import enrich

LOG_PATH = "rerun_brochure_names_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    db.init_db()
    rows = [dict(r) for r in db.get_all_prospects()]
    ids = [r["id"] for r in rows if r["status"] == "New" and r["crd_number"]]
    log(f"Resuming: {len(ids)} not-yet-processed brochure-derived records "
        f"(skipping already-Enriched records, including no-email name-only results)")

    start = time.time()
    last_logged = 0

    def on_progress(done: int, total: int) -> None:
        nonlocal last_logged
        if done - last_logged >= 100 or done == total:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            eta_min = (total - done) / rate / 60 if rate else float("inf")
            log(f"  {done}/{total}, {elapsed/60:.1f}m elapsed, ETA ~{eta_min:.0f}m")
            last_logged = done

    result = enrich.enrich_prospects(ids, progress_callback=on_progress, max_workers=16, use_processes=True)
    log(f"DONE: {result}")


if __name__ == "__main__":
    main()
