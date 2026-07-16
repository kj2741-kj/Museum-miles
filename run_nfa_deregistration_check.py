"""Driver for nfa_deregistration.py -- full fresh re-sweep + diff, logged,
resumable in the sense that it's safe to re-run anytime (idempotent: only
ever-flags firms that are STILL missing on the latest check; never
un-flags, matching the SEC side's one-way semantics)."""
import time
from datetime import datetime

from cftc import nfa_db
from cftc import nfa_deregistration

LOG_PATH = "logs/cftc/run_nfa_deregistration_log.txt"
MAX_ERROR_RATE = 0.05  # >5% of terms failing means the sweep itself is unreliable right now


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    nfa_db.init_db()
    log("Starting full fresh re-sweep (all 676 bigrams, ignoring nfa_sweep_progress cache)")

    start = time.time()
    last_logged = 0

    def on_progress(done: int, total: int) -> None:
        nonlocal last_logged
        if done - last_logged >= 50 or done == total:
            elapsed = time.time() - start
            log(f"  {done}/{total} terms, {elapsed:.0f}s elapsed")
            last_logged = done

    current_ids, errors = nfa_deregistration.fresh_current_ids(max_workers=20, progress_callback=on_progress)
    error_rate = errors / 676
    log(f"Fresh sweep complete: {len(current_ids)} currently-active firm IDs found, "
        f"{errors}/676 term errors ({error_rate:.1%})")

    if error_rate > MAX_ERROR_RATE:
        log(f"ABORTING deregistration flagging: error rate {error_rate:.1%} exceeds "
            f"{MAX_ERROR_RATE:.0%} threshold -- an unreliable sweep would produce false "
            f"deregistration flags. No firms marked. Re-run this script later.")
        return

    missing = nfa_deregistration.detect_deregistered(current_ids)
    log(f"DONE: {len(missing)} firm(s) newly flagged as deregistered.")
    for f in missing[:30]:
        log(f"  - {f['firm_name']} (NFA ID {f['nfa_id']})")


if __name__ == "__main__":
    main()
