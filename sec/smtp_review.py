"""
Phase 2 (2026-07-16): SMTP review pass across the whole SEC contact list --
for every named contact (primary prospects + secondary prospect_contacts)
with a domain to guess against, generate all 4 personal-email pattern
variants (first.last@ / flast@ / firstlast@ / f.last@ -- same as
enrich.guess_personal_emails()) and SMTP-verify each in turn, stopping at
the first one that verifies ("try till you succeed", per direct
instruction). The already-on-file email is tried first too, in case it's
already correct and simply never got SMTP-checked. If a verified variant
differs from what's on file, it replaces it -- a confirmed mailbox beats an
unconfirmed guess. If none of the 4 verify, the existing best-guess email is
left in place (still worth more than nothing, per this project's established
philosophy) and the record is marked reviewed so a rerun doesn't redo it.

Generic-mailbox contacts (no real name, or a name that doesn't look like a
real person -- info@/compliance@/etc) have nothing to pattern-guess against;
these just get the single address on file re-verified (same behavior as
enrich.reverify_emails()).

Resumable via smtp_reviewed (a dedicated marker, not email_verified --
"reviewed, nothing verified" is a valid terminal state, same status-vs-
email_verified lesson this project already learned on the SEC-discovery
side).
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed

from sec import db
from sec import enrich
from sec import iapd


def _looks_like_person(name: str | None) -> bool:
    return bool(name) and iapd.looks_like_real_name(name)


def _review_one(email: str, name: str | None, website: str | None) -> dict:
    domain = enrich._domain_of(website) if website else email.split("@")[-1]

    if name and _looks_like_person(name) and domain:
        candidates = enrich.guess_personal_emails(name, domain)
        if email not in candidates:
            candidates = [email] + candidates  # try the existing guess first
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


def collect_review_tasks() -> list[dict]:
    tasks = []
    for p in db.get_all_prospects():
        if p["email"] and not p["email_verified"] and not p["smtp_reviewed"]:
            tasks.append({
                "kind": "primary", "id": p["id"], "email": p["email"],
                "name": p["contact_name"], "website": p["website"],
            })
    for c in db.get_all_contacts():
        if c["email"] and not c["email_verified"] and not c["smtp_reviewed"]:
            tasks.append({
                "kind": "secondary", "id": c["id"], "email": c["email"],
                "name": c["contact_name"], "website": None,
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
                # Same discipline as every other batch script in this
                # project: one contact's failure must never take down the
                # rest -- leave smtp_reviewed unset so a future rerun retries it.
                results["errors"] += 1
                done += 1
                if progress_callback:
                    progress_callback(done, len(tasks))
                continue

            update_fields = {**outcome, "smtp_reviewed": 1}
            if task["kind"] == "primary":
                db.update_prospect(task["id"], **update_fields)
            else:
                db.update_contact(task["id"], **update_fields)

            if outcome["email_verified"]:
                results["now_verified"] += 1
            else:
                results["still_unverified"] += 1
            done += 1
            if progress_callback:
                progress_callback(done, len(tasks))
    return results
