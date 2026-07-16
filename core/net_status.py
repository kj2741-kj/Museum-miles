"""Thread-local tracking of whether a network-level failure (timeout,
connection refused, DNS failure — as opposed to a clean "nothing found")
was hit anywhere during one prospect's enrichment. Lets a hotspot/
connectivity blip be told apart from a genuine dead end, so affected
prospects can be flagged and re-run later instead of silently counted as
"no email found" forever. No other dependencies, importable from iapd.py,
team_page.py, and enrich.py without any circular-import risk.

Thread-local (not a plain module global) because enrich_prospects() runs
many prospects concurrently across worker threads — a global flag would
leak between unrelated prospects being processed at the same time.
"""
from __future__ import annotations
import socket
import threading

import dns.exception
import requests

_local = threading.local()

_NETWORK_ERROR_TYPES = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    socket.timeout,
    socket.gaierror,
    ConnectionError,
    dns.exception.Timeout,
)


def is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, _NETWORK_ERROR_TYPES)


def mark() -> None:
    _local.had_issue = True


def mark_if_network_error(exc: BaseException) -> None:
    if is_network_error(exc):
        mark()


def check_and_clear() -> bool:
    had = getattr(_local, "had_issue", False)
    _local.had_issue = False
    return had
