"""
One-off follow-up (2026-07-14): 228 already-processed prospects have
part2b_status IS NULL — meaning the Part 2B/2A brochure fetch itself never
resolved (get_brochure_version_id found nothing, or fetch_brochure_text
failed), as opposed to 'not_found' (brochure fetched fine, just no Part 2B
section in it). All 228 do have a CRD, so this isn't a "no CRD" case — it's
either a transient SEC.gov failure (503s are common, see ingest_sec_adv.py's
own retry logic) or a genuine no-brochure-on-file filer (e.g. Part 2 exempt).
Re-running distinguishes the two and recovers any that were just transient.

Unlike enrich_prospects(), this does NOT skip prospects that already have an
email — a handful of these 228 have an unverified guessed email from a
non-Part2B source (senior person via Part 2A prose, or individual-search
API), and we still want part2b_status resolved for them too, and any better
result to overwrite the guess.
"""
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
import enrich

LOG_PATH = "rerun_part2b_unchecked_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    db.init_db()
    rows = [dict(r) for r in db.get_all_prospects()]
    ids = [r["id"] for r in rows
           if r["status"] == "Enriched" and r["crd_number"] and r["part2b_status"] is None]
    log(f"Re-checking {len(ids)} prospects with unresolved part2b_status")

    start = time.time()
    done = 0
    resolved = {"found": 0, "not_found": 0, "still_null": 0}

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(enrich.enrich_prospect, dict(db.get_prospect(pid))): pid for pid in ids}
        for future in as_completed(futures):
            pid = futures[future]
            done += 1
            try:
                updates = future.result()
            except Exception as e:
                log(f"  id={pid} FAILED: {e}")
                if done % 25 == 0 or done == len(ids):
                    elapsed = time.time() - start
                    log(f"  {done}/{len(ids)}, {elapsed/60:.1f}m elapsed")
                continue
            secondary_contacts = updates.pop("_secondary_contacts", None)
            db.update_prospect(pid, **updates)
            if secondary_contacts is not None:
                db.replace_contacts(pid, secondary_contacts)
            status = updates.get("part2b_status")
            if status == "found":
                resolved["found"] += 1
            elif status == "not_found":
                resolved["not_found"] += 1
            else:
                resolved["still_null"] += 1
            if done % 25 == 0 or done == len(ids):
                elapsed = time.time() - start
                log(f"  {done}/{len(ids)}, {elapsed/60:.1f}m elapsed, {resolved}")

    log(f"DONE: {resolved}")


if __name__ == "__main__":
    main()
