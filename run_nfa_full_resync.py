"""Driver for nfa_full_resync.py -- meant to be the scheduled/recurring
entry point (see setup_nfa_scheduled_task.ps1), not a one-off. Safe to
run anytime: idempotent, only ever reflects the CURRENT true state of
NFA's roster."""
import time
from datetime import datetime

from cftc import nfa_db
from cftc import nfa_full_resync

LOG_PATH = "logs/cftc/run_nfa_full_resync_log.txt"
MAX_ERROR_RATE = 0.05


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    nfa_db.init_db()
    log("Starting full fresh re-sweep (all 676 bigrams)")

    start = time.time()
    last_logged = 0

    def on_progress(done: int, total: int) -> None:
        nonlocal last_logged
        if done - last_logged >= 50 or done == total:
            log(f"  {done}/{total} terms, {time.time()-start:.0f}s elapsed")
            last_logged = done

    rows_by_id, errors = nfa_full_resync.fresh_sweep(max_workers=20, progress_callback=on_progress)
    error_rate = errors / 676
    log(f"Sweep complete: {len(rows_by_id)} active firm IDs found, {errors}/676 errors ({error_rate:.1%})")

    if error_rate > MAX_ERROR_RATE:
        log(f"ABORTING resync: error rate {error_rate:.1%} exceeds {MAX_ERROR_RATE:.0%} -- "
            f"an unreliable sweep would produce false new/deregistered signals. Re-run later.")
        return

    result = nfa_full_resync.resync(rows_by_id)
    log(f"DONE: {result}")


if __name__ == "__main__":
    main()
