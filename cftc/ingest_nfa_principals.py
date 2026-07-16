"""P1: for every firm in nfa_prospects.db, fetch getPrincipals (named
officers/directors/owners with real structured titles — no PDF parsing
needed, unlike the SEC side) and getProfileBootstrap (address/phone).
Resumable: only processes status='New' firms, so a restart skips anything
already done. Same conservative concurrency (5 workers) as the P0 sweep.
"""
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from cftc import nfa_api
from cftc import nfa_db

LOG_PATH = "ingest_nfa_principals_log.txt"
MAX_WORKERS = 5


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def process_firm(firm: dict) -> tuple[dict, list[dict], dict, Exception | None]:
    try:
        principals = nfa_api.get_principals(firm["nfa_id"])
        vitals = nfa_api.get_profile_bootstrap(firm["nfa_id"])
        return firm, principals, vitals, None
    except Exception as e:
        return firm, [], {}, e


def main():
    nfa_db.init_db()
    firms = [dict(f) for f in nfa_db.get_firms(status="New")]
    log(f"Resuming: {len(firms)} firms not yet enriched with principals")

    start = time.time()
    done = 0
    errors = 0
    total_principals = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_firm, f): f["id"] for f in firms}
        for future in as_completed(futures):
            firm, principals, vitals, err = future.result()
            done += 1

            if err is not None:
                errors += 1
                log(f"  ERROR on firm {firm['firm_name']!r} (nfa_id={firm['nfa_id']}): {err}")
                # Left as status='New' on purpose — a future rerun retries it,
                # same discipline as ingest_nfa_firms.py and every SEC-side script.
            else:
                nfa_db.replace_principals(firm["id"], principals)
                nfa_db.update_firm(firm["id"], status="Enriched", **vitals)
                total_principals += len(principals)

            if done % 50 == 0 or done == len(firms):
                elapsed = time.time() - start
                rate = done / elapsed if elapsed else 0
                eta_min = (len(firms) - done) / rate / 60 if rate else float("inf")
                log(f"  {done}/{len(firms)} firms, {total_principals} principals found, "
                    f"{errors} errors, {elapsed/60:.1f}m elapsed, ETA ~{eta_min:.0f}m")

    log(f"DONE: {done} firms processed, {errors} errors, {total_principals} total principals")


if __name__ == "__main__":
    main()
