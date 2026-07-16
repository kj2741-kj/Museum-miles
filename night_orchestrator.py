"""Overnight resource orchestrator (2026-07-16), per direct instruction:
run the pending-task queue at max resources while port 25 is blocked; the
moment it opens, pause whatever pending task is running and give SMTP
review the whole machine; if port 25 closes again mid-SMTP-run, pause SMTP
and resume the pending-task queue where it left off.

Design: kill-and-relaunch, not in-process pause/resume -- every task here
is independently resumable via its own DB markers (website_source,
smtp_reviewed, deregistered_at, etc.), the same discipline this whole
project already relies on for every other background job. So "pause" always
just means "kill the process" and "resume" always just means "relaunch the
same script" -- no special checkpoint logic needed per task.

State persisted to night_orchestrator_state.json so the orchestrator itself
is resumable if IT gets killed/restarted (same philosophy applied one level
up).
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_PATH = "logs/core/night_orchestrator_log.txt"
STATE_PATH = Path("logs/core/night_orchestrator_state.json")
POLL_SECONDS = 180  # more responsive than the old 5-min watcher, since this toggles both ways
PYTHON = sys.executable

# Ordered queue of pending tasks to run while port 25 is blocked. Each is a
# standalone, independently-resumable script -- extend this list as more
# pending-phase scripts get built later tonight. Real bug found live
# (2026-07-16 06:02 ET): a formulaic stem+"_log.txt" guess at each script's
# log file doesn't match every script's actual hardcoded LOG_PATH (e.g.
# run_nfa_deregistration_check.py logs to "run_nfa_deregistration_log.txt",
# not "run_nfa_deregistration_check_log.txt") -- caused the orchestrator to
# never see the DONE marker and pointlessly relaunch an already-finished
# ~10-minute sweep from scratch. Fixed by mapping each task explicitly to
# its real log file instead of guessing. All log paths below moved under
# logs/sec/ or logs/cftc/ as part of the 2026-07-16 folder restructure.
PENDING_TASK_QUEUE = [
    "run_nfa_deregistration_check.py",
    "run_nfa_domain_retry.py",
]
TASK_LOG_FILES = {
    "run_nfa_deregistration_check.py": "logs/cftc/run_nfa_deregistration_log.txt",
    "run_nfa_domain_retry.py": "logs/cftc/run_nfa_domain_retry_log.txt",
}

# SEC first (57,410 contacts, the bigger backlog), then NFA (2,957
# principals) -- run sequentially while port 25 is open, same kill-and-
# relaunch/queue-index design as the pending queue.
SMTP_TASK_QUEUE = [
    "run_smtp_review.py",
    "run_nfa_smtp_review.py",
]
SMTP_TASK_LOG_FILES = {
    "run_smtp_review.py": "logs/sec/run_smtp_review_log.txt",
    "run_nfa_smtp_review.py": "logs/cftc/run_nfa_smtp_review_log.txt",
}


def _sec_smtp_has_work() -> bool:
    from sec import smtp_review
    return len(smtp_review.collect_review_tasks()) > 0


def _nfa_smtp_has_work() -> bool:
    from cftc import nfa_smtp_review
    return len(nfa_smtp_review.collect_review_tasks()) > 0


SMTP_HAS_WORK_CHECK = {
    "run_smtp_review.py": _sec_smtp_has_work,
    "run_nfa_smtp_review.py": _nfa_smtp_has_work,
}

# Where each launched script's stdout/stderr should land -- mirrors the
# logs/sec vs logs/cftc split; night_orchestrator.py's own log stays in
# logs/core.
SCRIPT_LOG_DIR = {
    "run_smtp_review.py": "logs/sec",
    "rerun_brochure_names.py": "logs/sec",
}


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
        state.setdefault("smtp_task_index", 0)
        return state
    return {"task_index": 0, "smtp_task_index": 0, "mode": "pending"}  # mode: "pending" or "smtp"


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state))


def port25_open() -> bool:
    try:
        s = socket.create_connection(("smtp.gmail.com", 25), timeout=5)
        s.close()
        return True
    except OSError:
        return False


def find_pids_by_script(script_name: str) -> list[str]:
    find_cmd = (
        'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | '
        f"Where-Object {{ $_.CommandLine -like '*{script_name}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", find_cmd],
        capture_output=True, text=True, timeout=15,
    )
    return [p.strip() for p in result.stdout.splitlines() if p.strip().isdigit()]


def kill_script(script_name: str) -> bool:
    pids = find_pids_by_script(script_name)
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=15)
        log(f"Killed {script_name} (PID {pid} + children)")
    return bool(pids)


def launch_script(script_name: str, skip_smtp: bool) -> None:
    """Real bug found live 2026-07-16 06:52 ET: env vars set in a prior,
    separate PowerShell tool call do NOT carry over to a later Start-Process
    launch of the orchestrator (each tool invocation is its own shell) --
    SKIP_SMTP_VERIFY was never actually set on the orchestrator's own
    process, so every child it spawned inherited an environment WITHOUT it,
    silently attempting real (blocked) port-25 connects and eating full 5s
    timeouts instead of skipping them. Fixed by building each child's
    environment explicitly here rather than relying on inheritance -- ON for
    pending-queue (discovery-only) tasks, OFF for the SMTP queue (whose
    entire point is a real handshake)."""
    stem = script_name.replace(".py", "")
    log_dir = SCRIPT_LOG_DIR.get(script_name, "logs/cftc")
    env = os.environ.copy()
    if skip_smtp:
        env["SKIP_SMTP_VERIFY"] = "1"
    else:
        env.pop("SKIP_SMTP_VERIFY", None)
    subprocess.Popen(
        [PYTHON, script_name],
        stdout=open(f"{log_dir}/{stem}_stdout.log", "a"),
        stderr=open(f"{log_dir}/{stem}_stderr.log", "a"),
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=env,
    )
    log(f"Launched {script_name} (SKIP_SMTP_VERIFY={'1' if skip_smtp else 'unset'})")


def task_log_shows_done(log_file_name: str) -> bool:
    """Each task script writes a 'DONE:' line to its own log file on
    completion -- do not guess the filename, look it up explicitly (real
    bug found live 2026-07-16: a formulaic stem+'_log.txt' guess didn't
    match every script's actual hardcoded LOG_PATH, causing a pointless
    ~10-minute relaunch of an already-finished sweep). These are fresh logs
    created tonight, not long-lived files with old DONE: lines from past
    runs -- so a plain substring check is safe here (unlike
    rerun_brochure_names_log.txt, which has months of history and needs an
    offset-based check instead)."""
    log_file = Path(log_file_name)
    if not log_file.exists():
        return False
    return "DONE:" in log_file.read_text(encoding="utf-8", errors="ignore")


def main() -> None:
    # Real bug found live 2026-07-16 (portability test, fresh DB with no
    # schema yet): every other entry-point script in this project calls
    # db.init_db()/nfa_db.init_db() before touching the database, but this
    # orchestrator never did -- on a genuinely fresh setup (a new machine,
    # no prior enrichment runs), SMTP_HAS_WORK_CHECK's first call would hit
    # "sqlite3.OperationalError: no such table: prospects", unhandled, and
    # crash the whole orchestrator on its very first cycle. Invisible on an
    # already-populated database (which is why this went unnoticed all
    # night), fatal on a fresh one.
    from sec import db
    from cftc import nfa_db
    db.init_db()
    nfa_db.init_db()

    state = load_state()
    log(f"Orchestrator started -- resuming state: {state}")

    while True:
        open_now = port25_open()

        if open_now:
            if state["mode"] == "pending":
                log("Port 25 OPEN -- switching from pending-task queue to SMTP review")
                current_task = PENDING_TASK_QUEUE[state["task_index"]] if state["task_index"] < len(PENDING_TASK_QUEUE) else None
                if current_task:
                    kill_script(current_task)
                state["mode"] = "smtp"
                save_state(state)

            if state["smtp_task_index"] >= len(SMTP_TASK_QUEUE):
                log("SMTP task queue fully complete -- idling in SMTP mode until port 25 closes or queue is extended.")
            else:
                task = SMTP_TASK_QUEUE[state["smtp_task_index"]]
                if task_log_shows_done(SMTP_TASK_LOG_FILES[task]):
                    log(f"{task} is DONE -- advancing to next SMTP task in queue")
                    state["smtp_task_index"] += 1
                    save_state(state)
                elif not find_pids_by_script(task):
                    if SMTP_HAS_WORK_CHECK[task]():
                        log(f"{task} not running and not done yet -- launching")
                        launch_script(task, skip_smtp=False)
                    else:
                        log(f"{task} has no remaining work -- advancing without running it")
                        state["smtp_task_index"] += 1
                        save_state(state)
                # else: still running, nothing to do this cycle

        else:  # port 25 blocked
            if state["mode"] == "smtp":
                log("Port 25 CLOSED again -- pausing SMTP review, resuming pending-task queue")
                current_smtp_task = SMTP_TASK_QUEUE[state["smtp_task_index"]] if state["smtp_task_index"] < len(SMTP_TASK_QUEUE) else None
                if current_smtp_task:
                    kill_script(current_smtp_task)
                state["mode"] = "pending"
                save_state(state)

            if state["task_index"] >= len(PENDING_TASK_QUEUE):
                log("Pending-task queue fully complete -- idling until port 25 opens or queue is extended.")
            else:
                task = PENDING_TASK_QUEUE[state["task_index"]]
                if task_log_shows_done(TASK_LOG_FILES[task]):
                    log(f"{task} is DONE -- advancing to next task in queue")
                    state["task_index"] += 1
                    save_state(state)
                elif not find_pids_by_script(task):
                    log(f"{task} not running and not done yet -- launching")
                    launch_script(task, skip_smtp=True)
                # else: still running, nothing to do this cycle

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
