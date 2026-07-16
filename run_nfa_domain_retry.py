"""Overnight retry pass (2026-07-16): the original P2 sweep only tried
.com for its domain guesses -- this retries the ~1,282 firms left
'none_found' with .net/.org/.co added too (candidate_domains(extra_tlds=True)),
since many boutique CTAs/CPOs don't use .com. Resumable via website_source
staying 'none_found' until a retry actually finds something (see
nfa_enrich.enrich_firms(retry_none_found=True))."""
import time
from datetime import datetime

from cftc import nfa_db
from cftc import nfa_enrich

LOG_PATH = "run_nfa_domain_retry_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    nfa_db.init_db()
    ids = [f["id"] for f in nfa_db.get_firms() if f["website_source"] == "none_found"]
    log(f"Resuming: {len(ids)} firms still 'none_found' after the original .com-only P2 pass")

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

    result = nfa_enrich.enrich_firms(ids, progress_callback=on_progress, max_workers=20, retry_none_found=True)
    log(f"DONE: {result}")


if __name__ == "__main__":
    main()
