"""
One-off batch runner: enrich every NY-state prospect that doesn't have an
email yet (broadened from an earlier NYC-city-only run — city-level scoping
was too narrow; state-level is the preferred cut going forward). Meant to be
launched detached (survives after the launching shell exits) since a full
pass can take hours — external SMTP/IAPD server latency is the bottleneck,
not something more of our own concurrency fixes.

Already-enriched prospects (from the earlier NYC-only pass) are automatically
skipped by enrich_prospects() — no wasted work from broadening the scope.

Progress + a running summary are logged to ny_state_enrichment_log.txt so it
can be checked without interrupting the run.
"""
import time
from datetime import datetime

import db
import enrich

LOG_PATH = "ny_state_enrichment_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    db.init_db()
    rows = [dict(r) for r in db.get_all_prospects()]
    targets = [r["id"] for r in rows if r["hq_state"] == "NY" and not r["email"]]
    log(f"Starting NY-state enrichment pass: {len(targets)} prospects to process (max_workers=20)")

    start = time.time()
    last_logged = 0

    def on_progress(done: int, total: int) -> None:
        nonlocal last_logged
        if done - last_logged >= 25 or done == total:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            eta_min = (total - done) / rate / 60 if rate else float("inf")
            log(f"{done}/{total} processed, {elapsed/60:.1f}m elapsed, ETA ~{eta_min:.0f}m")
            last_logged = done

    result = enrich.enrich_prospects(targets, progress_callback=on_progress, max_workers=20)
    log(f"DONE: {result}")


if __name__ == "__main__":
    main()
