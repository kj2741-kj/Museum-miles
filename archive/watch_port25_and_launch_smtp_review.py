"""Overnight unattended watcher (2026-07-16): port 25 has flipped between
blocked/open multiple times on this network over the course of this
project, unpredictably. Rather than requiring a live check-in to notice when
it reopens, this polls every 5 minutes and launches run_smtp_review.py the
moment a real connection succeeds, then exits (one-shot, not a permanent
supervisor -- run_smtp_review.py handles its own resumability from there).
Meant to be started detached (Start-Process -WindowStyle Hidden) and left
running overnight.

Per direct instruction (2026-07-16 night): the moment port 25 opens, SMTP
review gets priority over any concurrent NFA background work -- any running
run_nfa_enrichment.py process is killed first (NFA is fully resumable, see
its own website_source/status markers, so nothing is lost by pausing it
mid-run), then SMTP review launches at MAX_WORKERS (the full I/O-bound
ceiling this project has empirically tested, not the earlier 90%-while-NFA-
was-also-running figure -- with NFA out of the way, SMTP gets the whole
budget)."""
import socket
import subprocess
import sys
import time
from datetime import datetime

LOG_PATH = "watch_port25_log.txt"
POLL_SECONDS = 300
PYTHON = sys.executable
MAX_WORKERS = 25  # full tested I/O-bound ceiling, used once NFA is paused


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def port25_open() -> bool:
    try:
        s = socket.create_connection(("smtp.gmail.com", 25), timeout=5)
        s.close()
        return True
    except OSError:
        return False


def pause_nfa_pipeline() -> None:
    """Kill any running run_nfa_enrichment.py so SMTP review gets the full
    machine. Uses PowerShell CIM (not `tasklist`/`wmic`) to find it by
    command line, then taskkill /T to also catch any child processes --
    this project's own established gotcha (ProcessPoolExecutor children
    aren't killed by a plain Stop-Process on the parent), applied here
    defensively even though run_nfa_enrichment.py itself is thread- not
    process-pool-based, in case a future phase changes that."""
    try:
        find_cmd = (
            'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | '
            "Where-Object { $_.CommandLine -like '*run_nfa_enrichment.py*' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", find_cmd],
            capture_output=True, text=True, timeout=15,
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip().isdigit()]
        if not pids:
            log("No running NFA pipeline found -- nothing to pause.")
            return
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=15)
            log(f"Paused NFA pipeline (killed PID {pid} + children) to free resources for SMTP review.")
    except Exception as e:
        log(f"Could not check/pause NFA pipeline (non-fatal, continuing to launch SMTP review): {e}")


def main() -> None:
    log("Watcher started -- polling port 25 every 5 minutes")
    attempt = 0
    while True:
        attempt += 1
        if port25_open():
            log(f"Port 25 OPEN (attempt {attempt})")
            pause_nfa_pipeline()
            log(f"Launching run_smtp_review.py at max_workers={MAX_WORKERS}")
            subprocess.Popen(
                [PYTHON, "run_smtp_review.py"],
                stdout=open("run_smtp_review_stdout.log", "a"),
                stderr=open("run_smtp_review_stderr.log", "a"),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            log("Launched. Watcher exiting (one-shot).")
            return
        log(f"Port 25 still blocked (attempt {attempt}) -- next check in {POLL_SECONDS}s")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
