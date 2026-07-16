"""Push local DB changes to the private GitHub repo so Streamlit Cloud's
auto-redeploy-on-push picks up fresh data (2026-07-16). prospects.db and
nfa_prospects.db are committed on purpose (private repo, explicit exception
to the *.db gitignore rule -- see the initial "Commit prospects.db..."
commit) specifically so the Cloud deployment has real data to show.

Cloud doesn't have any way to reach back into this local machine, so this is
the only sync direction that exists: local -> git -> Cloud redeploy. Safe to
run anytime; a no-op (does nothing, no empty commit) if nothing has actually
changed since the last push.
"""
import subprocess
from datetime import datetime
from pathlib import Path

LOG_PATH = Path("logs/core/sync_db_to_cloud_log.txt")


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent)


def main() -> None:
    status = _run(["git", "status", "--short", "prospects.db", "nfa_prospects.db"])
    if not status.stdout.strip():
        log("No DB changes since last push -- nothing to sync.")
        return

    log(f"DB changes detected:\n{status.stdout.strip()}")
    _run(["git", "add", "prospects.db", "nfa_prospects.db"])
    commit_msg = f"Sync prospects.db/nfa_prospects.db for Streamlit Cloud ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
    commit = _run(["git", "commit", "-m", commit_msg])
    if commit.returncode != 0:
        log(f"Commit failed (non-fatal, will retry next run): {commit.stderr.strip()}")
        return
    log(f"Committed: {commit_msg}")

    push = _run(["git", "push", "origin", "main"])
    if push.returncode != 0:
        log(f"Push failed -- commit is local, will retry next run: {push.stderr.strip()}")
        return
    log("Pushed to origin/main -- Streamlit Cloud will auto-redeploy with the new data.")


if __name__ == "__main__":
    main()
