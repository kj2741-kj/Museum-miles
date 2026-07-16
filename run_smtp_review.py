"""Phase 2 batch driver: SMTP review across the whole SEC contact list
(primary prospects + secondary prospect_contacts). Resumable via
smtp_reviewed. Same Start-Process/kill-relaunch discipline as every other
long-running script in this project -- if it dies silently, just relaunch
the same way; it will only pick up genuinely unreviewed records."""
import time
from datetime import datetime

from sec import db
from sec import smtp_review

LOG_PATH = "logs/sec/run_smtp_review_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    db.init_db()
    tasks = smtp_review.collect_review_tasks()
    log(f"Resuming: {len(tasks)} contacts not yet SMTP-reviewed "
        f"(primary + secondary combined)")

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

    result = smtp_review.run_review(tasks, progress_callback=on_progress, max_workers=25)
    log(f"DONE: {result}")


if __name__ == "__main__":
    main()
