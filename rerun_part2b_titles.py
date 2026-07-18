"""One-off targeted rescan (2026-07-18): re-extract Part 2B titles only,
after fixing a real extraction gap found via a user report (Provision
Asset, LLC / Casey Luke Short) -- some brochures never state the title
under the Part 2B heading at all; it only appears later on a "Supervised
Person Brochure" page, reversed as "<Title> - <Name>" instead of
"<Name>, <Title>" (see iapd.extract_part2b_people()'s fallback pattern).

Scoped to firms with part2b_status='found' (the only ones that actually
have a Part 2B document to re-check) -- title-only, no email/SMTP work,
so this is much faster and lower-risk than a full re-enrichment pass.
Never overwrites an existing non-empty title, only fills in ones that
were blank. Never touches email/verification/status/anything else.

No DB access inside worker processes (process_one() is pure, returns
updates) -- same discipline as enrich.enrich_prospect(), so no SQLite
connection crosses the process boundary."""
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

from sec import db, iapd

LOG_PATH = "logs/sec/rerun_part2b_titles_log.txt"
MAX_WORKERS = 16  # matches the throughput testing already done in this project for this exact PDF-fetch-and-parse workload


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def process_one(prospect: dict, contacts: list[dict]) -> list[tuple[str, int, str]]:
    """Returns [("prospect"|"contact", id, new_title), ...]. Pure -- no DB access."""
    crd = prospect.get("crd_number")
    if not crd:
        return []
    try:
        version_id = iapd.get_brochure_version_id(crd)
        if not version_id:
            return []
        text = iapd.fetch_brochure_text(version_id)
        if not text:
            return []
        people = iapd.extract_part2b_people(text)
    except Exception:
        return []
    people_by_key = {iapd.person_dedup_key(name): title for name, title, _ in people if title}
    if not people_by_key:
        return []

    updates: list[tuple[str, int, str]] = []
    if prospect.get("contact_name") and not prospect.get("contact_title"):
        key = iapd.person_dedup_key(prospect["contact_name"])
        if key in people_by_key:
            updates.append(("prospect", prospect["id"], people_by_key[key]))
    for c in contacts:
        if c.get("contact_name") and not c.get("contact_title"):
            key = iapd.person_dedup_key(c["contact_name"])
            if key in people_by_key:
                updates.append(("contact", c["id"], people_by_key[key]))
    return updates


def main():
    db.init_db()
    prospects = [dict(r) for r in db.get_all_prospects() if r["part2b_status"] == "found"]
    log(f"Starting: {len(prospects)} firms with a Part 2B document to re-check for missed titles")

    # Contacts fetched up front (DB access stays on the main process).
    contacts_by_prospect = {p["id"]: [dict(c) for c in db.get_contacts_for_prospect(p["id"])] for p in prospects}

    start = time.time()
    done = 0
    prospect_titles_filled = 0
    contact_titles_filled = 0

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(process_one, p, contacts_by_prospect[p["id"]]): p
            for p in prospects
        }
        for future in as_completed(futures):
            p = futures[future]
            try:
                updates = future.result()
            except Exception as e:
                log(f"  ERROR on {p['firm_name']!r} (id {p['id']}): {e}")
                updates = []

            for kind, row_id, new_title in updates:
                if kind == "prospect":
                    db.update_prospect(row_id, contact_title=new_title)
                    prospect_titles_filled += 1
                    log(f"  FILLED (primary): {p['firm_name']!r} -> {new_title!r}")
                else:
                    db.update_contact(row_id, contact_title=new_title)
                    contact_titles_filled += 1
                    log(f"  FILLED (secondary): {p['firm_name']!r} -> {new_title!r}")

            done += 1
            if done % 50 == 0 or done == len(prospects):
                elapsed = time.time() - start
                rate = done / elapsed if elapsed else 0
                eta_min = (len(prospects) - done) / rate / 60 if rate else float("inf")
                log(f"{done}/{len(prospects)} checked, {prospect_titles_filled} primary + "
                    f"{contact_titles_filled} secondary titles filled so far, ETA ~{eta_min:.0f}m")

    log(f"DONE: {prospect_titles_filled} primary + {contact_titles_filled} secondary titles "
        f"filled in across {len(prospects)} firms checked")


if __name__ == "__main__":
    main()
