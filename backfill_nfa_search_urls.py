"""One-off backfill (2026-07-27): populate website_search_url for every NFA
firm that already went through P2 enrichment with no website found. No
network calls -- just builds a Google search URL from the firm name already
on file, same as new firms get going forward via nfa_enrich.enrich_firm()."""
from datetime import datetime

from cftc import nfa_db
from core import web_search_url

LOG_PATH = "logs/cftc/backfill_nfa_search_urls_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    nfa_db.init_db()
    firms = [f for f in nfa_db.get_firms() if not f["website"] and not f["website_search_url"]]
    log(f"Backfilling {len(firms)} firms with no website and no search URL yet")
    for firm in firms:
        url = web_search_url.build_firm_website_search_url(firm["firm_name"], firm["state"])
        nfa_db.update_firm(firm["id"], website_search_url=url)
    log(f"DONE: {len(firms)} firms backfilled")


if __name__ == "__main__":
    main()
