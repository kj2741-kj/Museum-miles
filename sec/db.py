"""SQLite storage for prospects.db — the CRM layer of the pipeline."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

from core import config
from sec import iapd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    source TEXT,
    prospect_type TEXT,
    crd_number TEXT,
    hq_city TEXT,
    hq_state TEXT,
    website TEXT,
    website_source TEXT,
    aum REAL,
    employees INTEGER,
    contact_name TEXT,
    contact_title TEXT,
    email TEXT,
    email_verified INTEGER DEFAULT 0,
    email_source TEXT,
    linkedin_search_url TEXT,
    linkedin_profile_url TEXT,
    status TEXT DEFAULT 'New',
    notes TEXT,
    deregistered_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS dedup_verdicts (
    pair_key TEXT PRIMARY KEY,
    a_id INTEGER NOT NULL,
    b_id INTEGER NOT NULL,
    same INTEGER NOT NULL,
    reason TEXT,
    adjudicated_at TEXT
);

-- A firm can have more than one real contact (e.g. a team page lists
-- several co-founders). prospects.contact_name/email stays the single
-- "primary" contact used for filtering/display; this table holds the
-- rest (rank 2-5), same shape, so real people found alongside the
-- primary aren't just discarded.
CREATE TABLE IF NOT EXISTS prospect_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    contact_name TEXT NOT NULL,
    contact_title TEXT,
    email TEXT,
    email_verified INTEGER DEFAULT 0,
    email_source TEXT,
    linkedin_profile_url TEXT,
    notes TEXT,
    created_at TEXT,
    FOREIGN KEY (prospect_id) REFERENCES prospects(id)
);
"""

_MIGRATIONS = [
    "ALTER TABLE prospects ADD COLUMN crd_number TEXT",
    "ALTER TABLE prospects ADD COLUMN website_source TEXT",
    "ALTER TABLE prospects ADD COLUMN deregistered_at TEXT",
    # 'found' / 'not_found' / NULL (never checked — no CRD, or the brochure
    # itself couldn't be fetched at all). Requested 2026-07-13 for
    # reconciliation: which firms have actually had their SEC Part 2B
    # ("Brochure Supplement") checked, separate from whether a named
    # contact was ultimately found by any method.
    "ALTER TABLE prospects ADD COLUMN part2b_status TEXT",
    "ALTER TABLE prospect_contacts ADD COLUMN notes TEXT",
    # 1 = a genuine network-level failure (timeout/connection/DNS) was hit
    # somewhere during this prospect's enrichment, as opposed to a clean
    # "nothing found" — requested 2026-07-13 (traveling, on a mobile
    # hotspot) so connectivity-caused gaps can be told apart from real dead
    # ends and re-run later rather than silently counted as one.
    "ALTER TABLE prospects ADD COLUMN network_issue INTEGER DEFAULT 0",
    # Phase 2 (2026-07-16): "reviewed and still unverified" is a valid
    # terminal state, same lesson as status vs. email_verified elsewhere in
    # this project (email_verified alone can't distinguish "never checked"
    # from "checked, none of the 4 pattern variants verified") -- needs its
    # own resumability marker, not reused from an existing column.
    "ALTER TABLE prospects ADD COLUMN smtp_reviewed INTEGER DEFAULT 0",
    "ALTER TABLE prospect_contacts ADD COLUMN smtp_reviewed INTEGER DEFAULT 0",
    # User-confirmed LinkedIn corrections (2026-07-17): a human directly
    # observed the real LinkedIn firm/person name differs from the SEC-filed
    # one (DBA/brand name, or a nickname) and described it in plain text;
    # core.linkedin_override parses that into these fields via the LLM.
    # Deliberately NOT an LLM guessing from scratch -- see linkedin_override.py
    # docstring for why that was rejected. Once set, these take priority over
    # the raw firm_name/contact_name for LinkedIn URL generation, permanently,
    # until cleared.
    "ALTER TABLE prospects ADD COLUMN linkedin_firm_override TEXT",
    "ALTER TABLE prospects ADD COLUMN linkedin_person_override TEXT",
    "ALTER TABLE prospect_contacts ADD COLUMN linkedin_person_override TEXT",
    # A pasted real LinkedIn profile URL (core.linkedin_override.extract_profile_url)
    # is stronger than a name-based override -- it's the confirmed profile
    # itself, not a search query. When set, linkedin_profile_url holds that
    # exact URL and must never be overwritten by a freshly generated search
    # link during re-enrichment.
    "ALTER TABLE prospects ADD COLUMN linkedin_url_confirmed INTEGER DEFAULT 0",
    "ALTER TABLE prospect_contacts ADD COLUMN linkedin_url_confirmed INTEGER DEFAULT 0",
]


def normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


@contextmanager
def get_conn():
    # Real crash found live 2026-07-16: sqlite3's default connect timeout
    # (5s) let a second process's read (nfa_enrich.build_sec_crossref_map(),
    # opening its own connection to this same file) collide with
    # rerun_brochure_names.py's ProcessPoolExecutor mid-write and raise
    # "database is locked" -- unhandled at the db.replace_contacts() call
    # site (outside enrich_prospects()'s per-worker try/except), which
    # crashed the entire batch script instead of just skipping one record.
    # A 30s busy-timeout makes sqlite retry internally instead of raising
    # immediately, absorbing normal transient contention between the two
    # concurrent enrichment tracks (SEC track vs. NFA track's SEC cross-ref
    # reads) without needing every caller to add its own retry logic.
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
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


def firm_exists(firm_name: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM prospects WHERE normalized_name = ?",
            (normalize_name(firm_name),),
        ).fetchone()
        return row is not None


def add_prospect(**fields) -> int | None:
    """Insert a new prospect. Returns the new row id, or None if it's a duplicate."""
    normalized = normalize_name(fields["firm_name"])
    if firm_exists(fields["firm_name"]):
        return None

    now = datetime.now(timezone.utc).isoformat()
    fields["normalized_name"] = normalized
    fields.setdefault("status", "New")
    fields["created_at"] = now
    fields["updated_at"] = now

    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO prospects ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def get_existing_normalized_names() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT normalized_name FROM prospects").fetchall()
        return {row["normalized_name"] for row in rows}


def bulk_add_prospects(records: list[dict]) -> dict:
    """Add many prospects in one connection/commit, skipping duplicates in-memory."""
    existing = get_existing_normalized_names()
    now = datetime.now(timezone.utc).isoformat()
    added, skipped = 0, 0

    with get_conn() as conn:
        for rec in records:
            normalized = normalize_name(rec["firm_name"])
            if normalized in existing:
                skipped += 1
                continue
            existing.add(normalized)

            fields = dict(rec)
            fields["normalized_name"] = normalized
            fields.setdefault("status", "New")
            fields["created_at"] = now
            fields["updated_at"] = now

            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO prospects ({cols}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
            added += 1

    return {"added": added, "skipped": skipped, "total": len(records)}


def update_prospect(prospect_id: int, **fields) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE prospects SET {set_clause} WHERE id = ?",
            (*fields.values(), prospect_id),
        )


def get_all_prospects(status: str | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM prospects WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM prospects ORDER BY updated_at DESC"
        ).fetchall()


def get_prospect(prospect_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM prospects WHERE id = ?", (prospect_id,)
        ).fetchone()


def counts_by_status() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM prospects GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}


# --- Deregistration tracking ---

def get_active_sec_adv_prospects() -> list[sqlite3.Row]:
    """Prospects sourced from SEC ADV with a CRD, not already flagged as
    deregistered — the set to check against a fresh bulk file."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM prospects WHERE source = 'SEC_ADV' "
            "AND crd_number IS NOT NULL AND deregistered_at IS NULL"
        ).fetchall()


def mark_deregistered(ids: list[int]) -> None:
    if not ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "UPDATE prospects SET deregistered_at = ?, updated_at = ? WHERE id = ?",
            [(now, now, i) for i in ids],
        )


# --- Persisted dedup verdicts (so re-scans only adjudicate genuinely new pairs) ---

def pair_key(a_id: int, b_id: int) -> str:
    lo, hi = sorted((a_id, b_id))
    return f"{lo}-{hi}"


def get_adjudicated_pair_keys() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT pair_key FROM dedup_verdicts").fetchall()
        return {r["pair_key"] for r in rows}


def record_dedup_verdict(a_id: int, b_id: int, same: bool, reason: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dedup_verdicts "
            "(pair_key, a_id, b_id, same, reason, adjudicated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pair_key(a_id, b_id), a_id, b_id, int(same), reason, now),
        )


def get_contact_overrides(prospect_id: int) -> dict[str, dict]:
    """{person_dedup_key: {"person_override": str|None, "confirmed_url": str|None}}
    for this prospect's existing secondary contacts that have a saved
    correction — used by replace_contacts() to carry it forward across a
    full re-enrichment pass, keyed the same way iapd.py already dedupes
    people across differently-formatted name strings. A pasted, confirmed
    profile URL takes priority over a name-based override wherever both
    somehow exist, since it's the actual verified profile rather than a
    search query built from a corrected name."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT contact_name, linkedin_person_override, linkedin_profile_url, linkedin_url_confirmed "
            "FROM prospect_contacts WHERE prospect_id = ? "
            "AND (linkedin_person_override IS NOT NULL OR linkedin_url_confirmed = 1)",
            (prospect_id,),
        ).fetchall()
    return {
        iapd.person_dedup_key(r["contact_name"]): {
            "person_override": r["linkedin_person_override"],
            "confirmed_url": r["linkedin_profile_url"] if r["linkedin_url_confirmed"] else None,
        }
        for r in rows
    }


def replace_contacts(prospect_id: int, contacts: list[dict]) -> None:
    """Replace all secondary contacts for a prospect (clear + reinsert) —
    keeps re-enrichment idempotent, same pattern as update_prospect().

    Carries forward each existing contact's linkedin_person_override and/or
    confirmed profile URL (2026-07-17): a plain clear+reinsert would
    otherwise silently discard a user-confirmed LinkedIn correction the
    moment this prospect gets re-enriched, since it lived on the row being
    deleted. Matched by person_dedup_key so it survives even if the
    freshly-discovered name string isn't byte-identical to the corrected
    one (e.g. "Bradley Benz" rediscovered again after "Brad Benz" was
    saved as the override)."""
    now = datetime.now(timezone.utc).isoformat()
    existing_overrides = get_contact_overrides(prospect_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM prospect_contacts WHERE prospect_id = ?", (prospect_id,))
        for i, c in enumerate(contacts, start=1):
            name = c.get("contact_name")
            saved = existing_overrides.get(iapd.person_dedup_key(name)) if name else None
            override = saved.get("person_override") if saved else None
            confirmed_url = saved.get("confirmed_url") if saved else None
            linkedin_profile_url = confirmed_url or c.get("linkedin_profile_url")
            conn.execute(
                "INSERT INTO prospect_contacts "
                "(prospect_id, rank, contact_name, contact_title, email, email_verified, "
                "email_source, linkedin_profile_url, linkedin_person_override, linkedin_url_confirmed, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    prospect_id, i, name, c.get("contact_title"), c.get("email"),
                    int(c.get("email_verified", 0)), c.get("email_source"),
                    linkedin_profile_url, override, int(bool(confirmed_url)), c.get("notes"), now,
                ),
            )


def update_contact(contact_id: int, **fields) -> None:
    """Targeted single-row update, unlike replace_contacts() (which clears +
    reinserts a prospect's whole contact list) -- needed for the Phase 2 SMTP
    review pass, which touches one already-existing contact row at a time."""
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE prospect_contacts SET {set_clause} WHERE id = ?",
            (*fields.values(), contact_id),
        )


def get_all_contacts() -> list[sqlite3.Row]:
    """Every secondary contact across every prospect, joined with the
    firm name — the dashboard filters this in-memory alongside the
    already-loaded main prospect list, same pattern used everywhere else
    in the app rather than a per-filter query."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT pc.*, p.firm_name FROM prospect_contacts pc "
            "JOIN prospects p ON p.id = pc.prospect_id "
            "ORDER BY pc.prospect_id, pc.rank"
        ).fetchall()


def get_contacts_for_prospect(prospect_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM prospect_contacts WHERE prospect_id = ? ORDER BY rank",
            (prospect_id,),
        ).fetchall()


def get_confirmed_duplicates() -> list[dict]:
    """All LLM-confirmed duplicate pairs where both prospects still exist
    (not already merged) — the dashboard's review queue."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM dedup_verdicts WHERE same = 1").fetchall()

    out = []
    for r in rows:
        a, b = get_prospect(r["a_id"]), get_prospect(r["b_id"])
        if a and b:
            out.append({
                "a_id": a["id"], "a_name": a["firm_name"],
                "b_id": b["id"], "b_name": b["firm_name"],
                "reason": r["reason"],
            })
    return out
