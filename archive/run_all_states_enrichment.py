"""
One-time full enrichment sweep: every remaining US state (NY already done),
processed one state at a time — biggest state first — so each gets full
worker throughput rather than splitting concurrency across states at once.
Meant to run for a long time (a full pass over ~14,300 prospects at the
measured ~0.14 items/sec is roughly a day) — launched detached so it survives
independent of any single session. Resilient: each state is wrapped so one
failure doesn't kill the rest of the run, and enrich_prospects() already
skips anything already enriched, so this is safe to stop and restart anytime.
"""
import time
from datetime import datetime

import db
import enrich

LOG_PATH = "all_states_enrichment_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    db.init_db()
    rows = [dict(r) for r in db.get_all_prospects()]

    by_state: dict[str | None, list[dict]] = {}
    for r in rows:
        if r["hq_state"] == "NY" or r["email"]:
            continue
        by_state.setdefault(r["hq_state"], []).append(r)

    # Biggest state first; unknown/blank state (None) goes last.
    ordered = sorted(
        by_state.items(),
        key=lambda kv: (kv[0] is None, -len(kv[1])),
    )

    total_remaining = sum(len(v) for _, v in ordered)
    log(f"Starting all-states sweep: {len(ordered)} states/groups, {total_remaining} prospects total")

    grand_start = time.time()
    grand_done = 0

    for state, prospects in ordered:
        label = state or "(blank state)"
        ids = [p["id"] for p in prospects]
        log(f"--- {label}: {len(ids)} prospects ---")

        state_start = time.time()
        last_logged = 0

        def on_progress(done: int, total: int, _label=label, _start=state_start) -> None:
            nonlocal last_logged
            if done - last_logged >= 25 or done == total:
                elapsed = time.time() - _start
                rate = done / elapsed if elapsed else 0
                eta_min = (total - done) / rate / 60 if rate else float("inf")
                log(f"  {_label}: {done}/{total}, {elapsed/60:.1f}m elapsed, ETA ~{eta_min:.0f}m")
                last_logged = done

        try:
            result = enrich.enrich_prospects(ids, progress_callback=on_progress, max_workers=20)
            grand_done += len(ids)
            grand_elapsed = time.time() - grand_start
            grand_remaining = total_remaining - grand_done
            grand_rate = grand_done / grand_elapsed if grand_elapsed else 0
            grand_eta_hr = grand_remaining / grand_rate / 3600 if grand_rate else float("inf")
            log(f"  {label} DONE: {result} | overall {grand_done}/{total_remaining}, "
                f"ETA for remaining states ~{grand_eta_hr:.1f}h")
        except Exception as e:
            log(f"  {label} FAILED, skipping to next state: {e!r}")

    log(f"ALL STATES SWEEP COMPLETE: {grand_done}/{total_remaining} processed")


if __name__ == "__main__":
    main()
