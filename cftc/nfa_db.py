"""SQLite storage for nfa_prospects.db — separate from prospects.db by
design (2026-07-15): the NFA CPO/CTA track is examined and operated on
independently from the SEC ADV track, even for dual-registered firms."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

from core import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nfa_firms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nfa_id TEXT NOT NULL UNIQUE,
    firm_name TEXT NOT NULL,
    reg_types TEXT,
    membership_status TEXT,
    has_reg_actions TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    zip_code TEXT,
    street_1 TEXT,
    street_2 TEXT,
    phone TEXT,
    website TEXT,
    website_source TEXT,
    status TEXT DEFAULT 'New',
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- Every principal returned by getPrincipals for a firm — real structured
-- name+title, no PDF parsing/regex extraction needed (unlike the SEC side).
CREATE TABLE IF NOT EXISTS nfa_principals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER NOT NULL,
    principal_nfa_id TEXT,
    name TEXT NOT NULL,
    title TEXT,
    ten_percent_owner INTEGER DEFAULT 0,
    email TEXT,
    email_verified INTEGER DEFAULT 0,
    email_source TEXT,
    linkedin_profile_url TEXT,
    notes TEXT,
    created_at TEXT,
    FOREIGN KEY (firm_id) REFERENCES nfa_firms(id)
);

-- Tracks which search terms (bigram sweep) have been run, so the sweep is
-- resumable/idempotent like every other ingest script in this project.
CREATE TABLE IF NOT EXISTS nfa_sweep_progress (
    term TEXT PRIMARY KEY,
    total_count INTEGER,
    pages_fetched INTEGER,
    completed_at TEXT
);
"""

# CREATE TABLE IF NOT EXISTS never adds a column to an already-created table
# -- nfa_prospects.db was created before P2 needed `notes` on nfa_principals,
# same migration pattern as db.py.
_MIGRATIONS = [
    "ALTER TABLE nfa_principals ADD COLUMN notes TEXT",
    # Cross-track link (2026-07-16): a meaningful share of NFA CPO/CTA
    # registrants are ALSO SEC-registered RIAs, already tracked as a
    # separate prospect in the SEC-side prospects.db (a different SQLite
    # file, no foreign key possible across files -- just store the SEC
    # prospects.id as a plain nullable int and join it in application code).
    # Without this, Mayank's two tracks would show the same real firm twice
    # as unrelated leads.
    "ALTER TABLE nfa_firms ADD COLUMN sec_prospect_id INTEGER",
    # Deregistration detection (2026-07-16), mirrors the SEC side's
    # deregistered_at column/semantics exactly: never deleted, CRM history
    # preserved, just flagged.
    "ALTER TABLE nfa_firms ADD COLUMN deregistered_at TEXT",
    # SMTP review pass (2026-07-16), same resumability marker pattern as
    # the SEC side's smtp_reviewed column (see db.py) -- "reviewed, nothing
    # verified" is a valid terminal state, email_verified alone can't
    # distinguish that from "never reviewed".
    "ALTER TABLE nfa_principals ADD COLUMN smtp_reviewed INTEGER DEFAULT 0",
    # Outreach-stage CRM tracking (2026-07-16): nfa_firms.status is already
    # used for P0-P2 pipeline progress ('New'/'Enriched'), can't double as
    # an outreach stage too -- needs its own column, mirroring the SEC
    # side's 7-stage prospects.status semantics but kept separate so the
    # pipeline-progress meaning of nfa_firms.status is never disturbed.
    "ALTER TABLE nfa_firms ADD COLUMN crm_stage TEXT DEFAULT 'New'",
    # Same user-confirmed LinkedIn correction mechanism as prospects.db
    # (see sec/db.py's migration comment + core/linkedin_override.py).
    "ALTER TABLE nfa_firms ADD COLUMN linkedin_firm_override TEXT",
    "ALTER TABLE nfa_principals ADD COLUMN linkedin_person_override TEXT",
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.NFA_DB_PATH, timeout=30)  # see db.py's get_conn for why
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists — migration already applied


def get_swept_terms() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT term FROM nfa_sweep_progress").fetchall()
        return {r["term"] for r in rows}


def mark_term_swept(term: str, total_count: int, pages_fetched: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO nfa_sweep_progress (term, total_count, pages_fetched, completed_at) "
            "VALUES (?, ?, ?, ?)",
            (term, total_count, pages_fetched, now),
        )


def upsert_firm(nfa_id: str, **fields) -> int:
    """Insert a firm, or update it in place if the nfa_id is already known
    (the same real firm surfaces under many different bigram searches)."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM nfa_firms WHERE nfa_id = ?", (nfa_id,)
        ).fetchone()
        if existing:
            if fields:
                fields["updated_at"] = now
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE nfa_firms SET {set_clause} WHERE nfa_id = ?",
                    (*fields.values(), nfa_id),
                )
            return existing["id"]

        fields["nfa_id"] = nfa_id
        fields.setdefault("status", "New")
        fields["created_at"] = now
        fields["updated_at"] = now
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO nfa_firms ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def mark_deregistered(ids: list[int]) -> None:
    if not ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "UPDATE nfa_firms SET deregistered_at = ?, updated_at = ? WHERE id = ?",
            [(now, now, i) for i in ids],
        )


def get_all_firm_nfa_ids() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT nfa_id FROM nfa_firms").fetchall()
        return {r["nfa_id"] for r in rows}


def get_firms(status: str | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM nfa_firms WHERE status = ? ORDER BY firm_name", (status,)
            ).fetchall()
        return conn.execute("SELECT * FROM nfa_firms ORDER BY firm_name").fetchall()


def update_firm(firm_id: int, **fields) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE nfa_firms SET {set_clause} WHERE id = ?",
            (*fields.values(), firm_id),
        )


def replace_principals(firm_id: int, principals: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM nfa_principals WHERE firm_id = ?", (firm_id,))
        for p in principals:
            conn.execute(
                "INSERT INTO nfa_principals "
                "(firm_id, principal_nfa_id, name, title, ten_percent_owner, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    firm_id, p.get("principal_nfa_id"), p["name"], p.get("title"),
                    int(p.get("ten_percent_owner", 0)), now,
                ),
            )


def counts_by_status() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM nfa_firms GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}


def get_all_principals() -> list[sqlite3.Row]:
    """Every principal across every firm, joined with the firm name/website --
    same pattern as db.get_all_contacts() on the SEC side, for the dashboard's
    NFA tab and for nfa_smtp_review.py (needs the firm's website to derive a
    domain, same as db.get_all_prospects() does for the SEC side)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT np.*, nf.firm_name, nf.state AS firm_state, nf.website AS firm_website "
            "FROM nfa_principals np "
            "JOIN nfa_firms nf ON nf.id = np.firm_id "
            "ORDER BY np.firm_id, np.ten_percent_owner DESC, np.id"
        ).fetchall()


def get_principals_for_firm(firm_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM nfa_principals WHERE firm_id = ? "
            "ORDER BY ten_percent_owner DESC, id ASC",
            (firm_id,),
        ).fetchall()


def update_principal(principal_id: int, **fields) -> None:
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE nfa_principals SET {set_clause} WHERE id = ?",
            (*fields.values(), principal_id),
        )
