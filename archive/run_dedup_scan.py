"""One-off dedup scan: candidate generation + LLM adjudication across every
prospect. Incremental — skips pairs already adjudicated in a previous run
(persisted in the dedup_verdicts table), so re-running after a fresh ingest
only costs LLM calls for genuinely new candidate pairs. Every verdict (same
AND different) is persisted as it goes; the dashboard reads confirmed
duplicates straight from the db, no separate results file needed."""
import time
from datetime import datetime

import db
import dedup

LOG_PATH = "dedup_scan_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    db.init_db()
    rows = [dict(r) for r in db.get_all_prospects()]
    candidates = dedup.find_new_candidate_pairs(rows)
    log(f"Starting incremental dedup scan: {len(candidates)} new candidate pairs to adjudicate "
        f"({len(db.get_adjudicated_pair_keys())} already adjudicated in past runs, skipped)")

    confirmed = 0
    start = time.time()
    for i, (a, b) in enumerate(candidates, start=1):
        same, reason = dedup.llm_adjudicate_pair(a, b)
        db.record_dedup_verdict(a["id"], b["id"], same, reason)
        if same:
            confirmed += 1
            log(f"  DUPLICATE: {a['firm_name']!r} <-> {b['firm_name']!r} ({reason})")
        if i % 50 == 0 or i == len(candidates):
            elapsed = time.time() - start
            rate = i / elapsed if elapsed else 0
            eta_min = (len(candidates) - i) / rate / 60 if rate else float("inf")
            log(f"{i}/{len(candidates)} adjudicated, {confirmed} confirmed duplicates so far, ETA ~{eta_min:.0f}m")

    log(f"DONE: {confirmed} newly confirmed duplicate pairs out of {len(candidates)} new candidates "
        f"({len(db.get_confirmed_duplicates())} total confirmed duplicates in the db)")


if __name__ == "__main__":
    main()
