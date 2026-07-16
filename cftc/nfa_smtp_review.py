"""NFA counterpart to smtp_review.py (2026-07-16) -- same "generate every
personal-email pattern variant, try each via real SMTP until one verifies"
treatment, applied to nfa_principals instead of the SEC contact list.
Domain comes from the principal's own firm's website (nfa_firms.website);
principals whose firm has no website, or whose own name doesn't look like a
real individual (corporate/trust owners -- already excluded from having an
email at all by nfa_enrich.py's P2 pass), have nothing to review.

Resumable via nfa_principals.smtp_reviewed, same pattern as the SEC side.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed

from sec import enrich
from sec import iapd
from cftc import nfa_db


def _looks_like_person(name: str | None) -> bool:
    return bool(name) and iapd.looks_like_real_name(name)


def _review_one(email: str, name: str | None, website: str | None) -> dict:
    domain = enrich._domain_of(website) if website else email.split("@")[-1]

    if name and _looks_like_person(name) and domain:
        candidates = enrich.guess_personal_emails(name, domain)
        if email not in candidates:
            candidates = [email] + candidates
        for candidate in candidates:
            verified, reason = enrich.verify_email(candidate)
            if verified:
                out = {"email": candidate, "email_verified": 1}
                if candidate != email:
                    out["email_source"] = "smtp_review_verified"
                if "catch-all" in reason:
                    out["notes"] = reason
                return out
        return {"email": email, "email_verified": 0, "notes": "smtp review: no pattern variant verified"}

    verified, reason = enrich.verify_email(email)
    out = {"email": email, "email_verified": int(verified)}
    if not verified:
        out["notes"] = f"smtp review: {reason}"
    return out


def reset_unverified_for_recheck() -> int:
    """See sec/smtp_review.py's twin function -- same rationale, applied to
    nfa_principals. Returns the count reset."""
    with nfa_db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE nfa_principals SET smtp_reviewed = 0 "
            "WHERE email IS NOT NULL AND email_verified = 0 AND smtp_reviewed = 1"
        )
        return cur.rowcount


def collect_review_tasks() -> list[dict]:
    tasks = []
    for p in nfa_db.get_all_principals():
        if p["email"] and not p["email_verified"] and not p["smtp_reviewed"]:
            tasks.append({
                "id": p["id"], "email": p["email"], "name": p["name"],
                "website": p["firm_website"],
            })
    return tasks


def run_review(tasks: list[dict], progress_callback=None, max_workers: int = 22) -> dict:
    results = {"total": len(tasks), "now_verified": 0, "still_unverified": 0, "errors": 0}

    def _work(task: dict):
        return task, _review_one(task["email"], task["name"], task["website"])

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, t): t for t in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                _, outcome = future.result()
            except Exception:
                results["errors"] += 1
                done += 1
                if progress_callback:
                    progress_callback(done, len(tasks))
                continue

            nfa_db.update_principal(task["id"], **outcome, smtp_reviewed=1)
            if outcome["email_verified"]:
                results["now_verified"] += 1
            else:
                results["still_unverified"] += 1
            done += 1
            if progress_callback:
                progress_callback(done, len(tasks))
    return results
