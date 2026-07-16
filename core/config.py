"""Central config for the Museum Mile Funds marketing dashboard."""
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent  # core/config.py -> project root
DATA_DIR = BASE_DIR / "data" / "sec"  # only ever used by sec/ingest_sec_adv.py
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "prospects.db"
EXCEL_PATH = BASE_DIR / "prospects.xlsx"

# NFA CPO/CTA track — deliberately a SEPARATE database from prospects.db
# (user's explicit choice, 2026-07-15): examined and operated on independently
# from the SEC ADV track, not merged even when the same firm is dual-registered.
NFA_DB_PATH = BASE_DIR / "nfa_prospects.db"

GROQ_KEY_FILE = BASE_DIR / ".groq_key"
EMAIL_CONFIG_FILE = BASE_DIR / ".email_config"

STREAMLIT_PORT = 8675

# SEC ADV ingest pulls every firm in the bulk filing, no AUM/employee/location
# filter applied at ingest time. Filtering happens interactively in the
# dashboard (AUM slider + location multiselect) once data is imported.

# --- 7-stage CRM pipeline ---
STATUS_STAGES = [
    "New",
    "Enriched",
    "Ready to Contact",
    "Contacted",
    "Replied",
    "Meeting Scheduled",
    "Closed",
]
