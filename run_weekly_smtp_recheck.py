"""Weekly SMTP re-check (2026-07-16): "reviewed, unverified" isn't a
permanent verdict -- mail servers often just don't answer a first attempt
(temporary block, greylisting, server hiccup). This resets smtp_reviewed on
every still-unverified contact (SEC + NFA) so a fresh SMTP review pass picks
them back up, then clears the DONE marker from both review logs and resets
night_orchestrator.py's smtp_task_index to 0 so the orchestrator naturally
re-runs both passes the next time port 25 is open -- no separate SMTP logic
duplicated here, just resurfacing the work for the orchestrator to pick up.

Meant to run on a schedule (see setup instructions) alongside
run_nfa_full_resync.py's existing weekly task."""
import json
from datetime import datetime
from pathlib import Path

from sec import db
from sec import smtp_review
from cftc import nfa_db
from cftc import nfa_smtp_review

LOG_PATH = Path("logs/core/run_weekly_smtp_recheck_log.txt")
STATE_PATH = Path("logs/core/night_orchestrator_state.json")
SEC_SMTP_LOG = Path("logs/sec/run_smtp_review_log.txt")
NFA_SMTP_LOG = Path("logs/cftc/run_nfa_smtp_review_log.txt")


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _archive_and_clear(log_file: Path) -> None:
    """Keep the old log's history (rename with a timestamp) rather than
    deleting it outright, then start a fresh empty log so
    task_log_shows_done() sees no DONE: marker and re-runs."""
    if not log_file.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    archived = log_file.with_name(f"{log_file.stem}_{stamp}{log_file.suffix}")
    log_file.rename(archived)
    log(f"Archived {log_file} -> {archived.name}")


def main() -> None:
    db.init_db()
    nfa_db.init_db()

    sec_reset = smtp_review.reset_unverified_for_recheck()
    nfa_reset = nfa_smtp_review.reset_unverified_for_recheck()
    log(f"Reset for recheck: {sec_reset} SEC contacts, {nfa_reset} NFA principals")

    _archive_and_clear(SEC_SMTP_LOG)
    _archive_and_clear(NFA_SMTP_LOG)

    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
        state["smtp_task_index"] = 0
        STATE_PATH.write_text(json.dumps(state))
        log(f"Reset night_orchestrator.py's smtp_task_index to 0 (was tracking: {state})")
    else:
        log("No orchestrator state file found -- nothing to reset there (orchestrator not run yet).")

    log("DONE: reset complete. The orchestrator will re-run both SMTP passes next time port 25 is open.")


if __name__ == "__main__":
    main()
