"""P2 batch driver: email discovery for every NFA CPO/CTA firm + principal.
Resumable via website_source IS NULL (the "not yet P2-attempted" marker --
status can't be reused for this since P1 already sets status='Enriched' on
every firm). Same Start-Process/kill-relaunch discipline as every other
long-running script in this project."""
import time
from datetime import datetime

from cftc import nfa_db
from cftc import nfa_enrich

LOG_PATH = "run_nfa_enrichment_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    nfa_db.init_db()
    firms = [dict(f) for f in nfa_db.get_firms()]
    ids = [f["id"] for f in firms if not f.get("website_source")]
    log(f"Resuming: {len(ids)}/{len(firms)} firms not yet P2-processed")

    start = time.time()
    last_logged = 0

    def on_progress(done: int, total: int) -> None:
        nonlocal last_logged
        if done - last_logged >= 50 or done == total:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            eta_min = (total - done) / rate / 60 if rate else float("inf")
            log(f"  {done}/{total}, {elapsed/60:.1f}m elapsed, ETA ~{eta_min:.0f}m")
            last_logged = done

    result = nfa_enrich.enrich_firms(ids, progress_callback=on_progress, max_workers=19)
    log(f"DONE: {result}")


if __name__ == "__main__":
    main()
