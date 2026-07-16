"""
LLM-assisted duplicate detection for prospects.db. Ingest already prevents
exact-normalized-name duplicates (db.normalize_name), but that only lowercases
and collapses whitespace — it doesn't catch "Kahn Brothers Advisors LLC" vs
"Kahn Brothers Advisors, LLC" (punctuation/suffix variants of the same firm).

Candidate generation is cheap and free (canonical-key grouping + fuzzy
blocking), but every candidate pair is adjudicated by the LLM before being
reported — tested this against real data and plain suffix-stripping alone is
NOT safe enough to trust: "Goldman Sachs Asset Management, L.P." and
"...Co., Ltd." look like a match after stripping suffixes, but are almost
certainly a US entity and its separately-registered Asia-Pacific affiliate,
not a duplicate record. HQ city/state is passed to the LLM as context to help
catch exactly that kind of case.

Never auto-merges — surfaces suspected duplicates for manual review; merging
is a one-click action the user takes, not something done silently.

Every adjudication (both "same" and "different") is persisted to the
`dedup_verdicts` table, so a re-scan after a fresh ingest only sends genuinely
new candidate pairs to the LLM — it doesn't re-ask settled questions.
"""
from __future__ import annotations
import difflib
import re
from itertools import combinations

from sec import db
from core import llm

_SUFFIXES = {
    "llc", "inc", "incorporated", "lp", "llp", "corp", "corporation",
    "ltd", "co", "company", "gp", "pllc",
}
_PUNCT_RE = re.compile(r"[^\w\s]")


def canonicalize(name: str) -> str:
    """Strip punctuation + common legal-entity suffixes, collapse whitespace.
    Punctuation is deleted (not replaced with a space) so abbreviations like
    "L.P." collapse to "lp" as one token instead of splitting into "l" "p"."""
    cleaned = _PUNCT_RE.sub("", name.lower())
    tokens = [t for t in cleaned.split() if t not in _SUFFIXES]
    return " ".join(tokens) if tokens else cleaned.strip()


def _block_key(p: dict) -> str:
    tokens = canonicalize(p["firm_name"]).split()
    return tokens[0] if tokens else ""


def find_candidate_pairs(prospects: list[dict], threshold: float = 0.82, max_block_size: int = 40) -> list[tuple[dict, dict]]:
    """Cheap, free candidate generation — not a verdict. Groups sharing an
    identical canonical name are automatic candidates (rank 1); within each
    first-token block, difflib-similar pairs are also candidates (rank 2).
    Every candidate still goes through LLM adjudication before being trusted."""
    canonical_groups: dict[str, list[dict]] = {}
    blocks: dict[str, list[dict]] = {}
    for p in prospects:
        key = canonicalize(p["firm_name"])
        if key:
            canonical_groups.setdefault(key, []).append(p)
        block = _block_key(p)
        if block:
            blocks.setdefault(block, []).append(p)

    pairs: set[tuple[int, int]] = set()
    candidates: list[tuple[dict, dict]] = []

    def _add(a: dict, b: dict) -> None:
        key = tuple(sorted((a["id"], b["id"])))
        if key not in pairs:
            pairs.add(key)
            candidates.append((a, b))

    for group in canonical_groups.values():
        if len(group) > 1:
            for a, b in combinations(group, 2):
                _add(a, b)

    for members in blocks.values():
        if len(members) < 2 or len(members) > max_block_size:
            continue
        for a, b in combinations(members, 2):
            ratio = difflib.SequenceMatcher(None, canonicalize(a["firm_name"]), canonicalize(b["firm_name"])).ratio()
            if ratio >= threshold:
                _add(a, b)

    return candidates


def llm_adjudicate_pair(a: dict, b: dict) -> tuple[bool, str]:
    """Ask the LLM whether two firm records refer to the same real company.
    Returns (same, reasoning). Defaults to False (not a duplicate) if the
    LLM call fails — safer than falsely merging two different real firms."""
    def _describe(p: dict) -> str:
        loc = f"{p.get('hq_city') or '?'}, {p.get('hq_state') or '?'}"
        aum = f"${p['aum']:,.0f} AUM" if p.get("aum") else "AUM unknown"
        return f'"{p["firm_name"]}" (HQ: {loc}; {aum}; CRD {p.get("crd_number") or "?"})'

    prompt = (
        f"Firm A: {_describe(a)}\nFirm B: {_describe(b)}\n\n"
        "Are these the SAME real-world registered investment adviser, just "
        "represented differently (abbreviation, punctuation, legal suffix, "
        "typo)? Or are they genuinely DIFFERENT SEC-registered entities — e.g. "
        "a US entity vs. a separately-registered overseas affiliate, a parent "
        "vs. subsidiary, or two different firms that happen to share a brand "
        "or founder's name? Different HQ locations or CRD numbers are strong "
        "signals they are different entities, not a duplicate."
    )
    system = 'Respond with only JSON: {"same": true|false, "reason": "<one short sentence>"}'
    result, model = llm.chat_json(prompt, system=system)
    if result is None or "same" not in result:
        return False, "LLM unavailable — treated as not a duplicate to be safe"
    return bool(result["same"]), result.get("reason", "")


def find_new_candidate_pairs(prospects: list[dict], threshold: float = 0.82, max_block_size: int = 40) -> list[tuple[dict, dict]]:
    """Same as find_candidate_pairs, minus pairs already adjudicated in a
    previous scan (persisted in dedup_verdicts) — what an incremental re-scan
    actually needs to send to the LLM."""
    already = db.get_adjudicated_pair_keys()
    return [
        (a, b) for a, b in find_candidate_pairs(prospects, threshold, max_block_size)
        if db.pair_key(a["id"], b["id"]) not in already
    ]


def find_duplicates(prospects: list[dict], incremental: bool = True) -> list[tuple[dict, dict, str]]:
    """Adjudicates candidate pairs (skipping already-adjudicated ones when
    incremental=True) and persists every verdict — same and different — so
    future scans don't re-ask settled questions. Returns pairs the LLM
    confirms are the same real firm, as (firm_a, firm_b, reason)."""
    candidates = find_new_candidate_pairs(prospects) if incremental else find_candidate_pairs(prospects)
    confirmed = []
    for a, b in candidates:
        same, reason = llm_adjudicate_pair(a, b)
        db.record_dedup_verdict(a["id"], b["id"], same, reason)
        if same:
            confirmed.append((a, b, reason))
    return confirmed


def merge_prospects(keep_id: int, remove_id: int) -> None:
    """Merge remove_id into keep_id: fills any blank fields on keep_id from
    remove_id's data, then deletes remove_id. Never overwrites data keep_id
    already has."""
    keep = dict(db.get_prospect(keep_id))
    remove = dict(db.get_prospect(remove_id))

    fillable = [
        "website", "website_source", "aum", "employees", "contact_name",
        "contact_title", "email", "email_verified", "email_source", "crd_number",
    ]
    updates = {f: remove[f] for f in fillable if not keep.get(f) and remove.get(f)}
    if updates:
        db.update_prospect(keep_id, **updates)

    with db.get_conn() as conn:
        conn.execute("DELETE FROM prospects WHERE id = ?", (remove_id,))
