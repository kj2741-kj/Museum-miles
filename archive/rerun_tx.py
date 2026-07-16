"""
One-off follow-up to run_all_states_enrichment.py: TX crashed mid-batch
(2026-07-12) on a malformed ADV "website" field that took down the whole
state before enrich.py's domain validation + per-prospect exception
handling were added to fix it. ~886 of TX's 1,061 prospects never got
processed. This waits for the FL leg of the currently-running sweep to
finish (so it doesn't compete for concurrency/throughput with it), then
reprocesses all TX prospects — enrich_prospects() skips anything that
already has an email, so the ~175 TX rows done before the crash are
untouched. Logs to the same file as the main sweep.
"""
import time

import db
import enrich

LOG_PATH = "all_states_enrichment_log.txt"


def log(msg: str) -> None:
    from datetime import datetime
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_for_fl_done(poll_seconds: int = 120) -> None:
    while True:
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        if "FL DONE" in text or "FL FAILED" in text:
            return
        time.sleep(poll_seconds)


def main():
    log("rerun_tx: waiting for FL to finish before reprocessing TX")
    wait_for_fl_done()

    db.init_db()
    rows = [dict(r) for r in db.get_all_prospects()]
    tx_ids = [r["id"] for r in rows if r["hq_state"] == "TX" and not r["email"]]

    log(f"rerun_tx: FL done, reprocessing {len(tx_ids)} still-unenriched TX prospects")

    start = time.time()
    last_logged = 0

    def on_progress(done: int, total: int) -> None:
        nonlocal last_logged
        if done - last_logged >= 25 or done == total:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            eta_min = (total - done) / rate / 60 if rate else float("inf")
            log(f"  TX (rerun): {done}/{total}, {elapsed/60:.1f}m elapsed, ETA ~{eta_min:.0f}m")
            last_logged = done

    try:
        result = enrich.enrich_prospects(tx_ids, progress_callback=on_progress, max_workers=20)
        log(f"  TX (rerun) DONE: {result}")
    except Exception as e:
        log(f"  TX (rerun) FAILED: {e!r}")


if __name__ == "__main__":
    main()
