"""One-off (2026-07-16): populate nfa_firms.sec_prospect_id for every NFA
firm that's also an SEC-registered prospect, by canonicalized firm-name
match (dedup.canonicalize -- same punctuation/legal-suffix-stripping used
throughout this project's dedup logic). Fast, in-memory, no network calls --
runs synchronously, not a detached background job. Safe to re-run anytime
(idempotent overwrite); should be re-run after any fresh SEC or NFA ingest
so newly-added firms on either side get linked too.
"""
from sec import db as sec_db
from sec import dedup
from cftc import nfa_db


def main():
    sec_db.init_db()
    nfa_db.init_db()

    sec_by_canonical: dict[str, int] = {}
    for row in sec_db.get_all_prospects():
        key = dedup.canonicalize(row["firm_name"])
        if key and key not in sec_by_canonical:
            sec_by_canonical[key] = row["id"]

    matched = 0
    checked = 0
    for firm in nfa_db.get_firms():
        checked += 1
        key = dedup.canonicalize(firm["firm_name"])
        sec_id = sec_by_canonical.get(key)
        if sec_id and firm["sec_prospect_id"] != sec_id:
            nfa_db.update_firm(firm["id"], sec_prospect_id=sec_id)
            matched += 1

    print(f"Checked {checked} NFA firms against {len(sec_by_canonical)} SEC canonical names. "
          f"Newly linked/updated: {matched}.")


if __name__ == "__main__":
    main()
