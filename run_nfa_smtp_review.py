"""Batch driver for nfa_smtp_review.py -- same shape as run_smtp_review.py.
Runs after (or alongside) the SEC SMTP review whenever port 25 is open."""
import time
from datetime import datetime

from cftc import nfa_db
from cftc import nfa_smtp_review

LOG_PATH = "logs/cftc/run_nfa_smtp_review_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    nfa_db.init_db()
    tasks = nfa_smtp_review.collect_review_tasks()
    log(f"Resuming: {len(tasks)} NFA principals not yet SMTP-reviewed")

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

    result = nfa_smtp_review.run_review(tasks, progress_callback=on_progress, max_workers=25)
    log(f"DONE: {result}")


if __name__ == "__main__":
    main()
