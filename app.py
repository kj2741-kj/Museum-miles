"""Museum Mile Funds — marketing/prospecting dashboard. SEC ADV + NFA CPO/CTA tabs."""
import difflib
import re
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from core import config
from sec import db
from sec import dedup
from sec import enrich
from sec import excel_export
from core import formatting
from sec import ingest_sec_adv
from cftc import nfa_db
from cftc import nfa_excel_export
from core import settings
from core import linkedin_url
from core import linkedin_override

ENRICH_BATCH_CAP = 200  # keep a single click bounded; re-click to continue

st.set_page_config(page_title="Museum Mile Prospecting", page_icon="🖼️", layout="wide")
db.init_db()
nfa_db.init_db()

st.title("🖼️ Museum Mile Funds Prospecting Dashboard")

# --- Sidebar: LLM features toggle ---
with st.sidebar:
    st.header("🤖 LLM Features")
    llm_enabled = st.toggle(
        "Enable LLM-powered features",
        value=settings.get("llm_enabled"),
        help="Uses Groq (cloud, needs an API key) with local Ollama as a "
             "fallback. Covers duplicate detection and parsing LinkedIn "
             "name-mismatch corrections you type in. Email discovery and "
             "verification never use an LLM. Off by default until "
             "Groq/Ollama is set up and confirmed working.",
    )
    if llm_enabled != settings.get("llm_enabled"):
        settings.set("llm_enabled", llm_enabled)
        st.rerun()
    if not llm_enabled:
        st.caption("Duplicate review and LinkedIn-correction saving are hidden while this is off.")

    st.divider()
    st.header("SEC ADV Data")
    cache = ingest_sec_adv.cache_status()
    if cache:
        st.caption(f"Cached: {cache['total_firms']} firms")
        st.caption(f"Fetched: {cache['fetched_at'][:19].replace('T', ' ')}")
        st.markdown(f"[Source file (SEC bulk ADV zip)]({cache['source_url']})")
        if cache.get("raw_csv_path"):
            st.caption(f"Raw archive: {cache['raw_csv_path']}")
    else:
        st.caption("No cache yet. Click Refresh below.")
    st.markdown(f"[SEC ADV data listing page]({ingest_sec_adv.LISTING_URL})")

    if st.button("🔄 Refresh SEC ADV cache", help="Downloads the latest SEC bulk ADV filing (~1-2 min)"):
        with st.spinner("Downloading SEC ADV bulk filing..."):
            try:
                meta = ingest_sec_adv.refresh_adv_cache()
                st.success(f"Cached {meta['total_firms']} firms.")
            except Exception as e:
                st.error(f"Failed: {e}")
                meta = None
        if meta:
            with st.spinner("Checking for deregistered firms..."):
                try:
                    deregistered = ingest_sec_adv.detect_deregistered()
                    if deregistered:
                        st.warning(
                            f"{len(deregistered)} firm(s) no longer appear in the SEC ADV bulk "
                            f"file, likely deregistered. Flagged, not deleted: "
                            + ", ".join(p["firm_name"] for p in deregistered[:5])
                            + (f" +{len(deregistered) - 5} more" if len(deregistered) > 5 else "")
                        )
                except Exception as e:
                    st.error(f"Deregistration check failed: {e}")

    with st.expander("📁 Already have the SEC ADV file? Upload it"):
        st.caption("If you've already downloaded the SEC ADV bulk filing "
                   "yourself (.zip as SEC publishes it, or an extracted "
                   ".csv), upload it here instead of refreshing from SEC.gov.")
        uploaded = st.file_uploader("SEC ADV bulk file", type=["zip", "csv"], label_visibility="collapsed")
        if uploaded is not None:
            with st.spinner("Processing uploaded file..."):
                try:
                    meta = ingest_sec_adv.load_adv_from_upload(uploaded.getvalue(), uploaded.name)
                    st.success(f"Cached {meta['total_firms']} firms from {uploaded.name}.")
                except Exception as e:
                    st.error(f"Failed: {e}")

    if st.button("➕ Ingest all prospects from cache"):
        if not cache:
            st.warning("Refresh the ADV cache first.")
        else:
            with st.spinner("Adding new prospects..."):
                try:
                    result = ingest_sec_adv.ingest_new_prospects()
                    st.success(f"Added {result['added']} new, skipped {result['skipped']} duplicates.")
                except Exception as e:
                    st.error(f"Failed: {e}")

    st.divider()
    st.caption("No filters applied at ingest. Every firm in the SEC ADV bulk "
               "file is imported; narrow down with the AUM/location filters "
               "below once data is in.")

    st.divider()
    st.header("NFA CPO/CTA Data")
    nfa_status = nfa_db.counts_by_status()
    st.caption(f"{sum(nfa_status.values())} firms in nfa_prospects.db")
    st.caption(
        "NFA ingest/enrichment run as standalone background scripts (each "
        "takes 20min–2hrs+, too long for a dashboard click): "
        "`ingest_nfa_firms.py` (firm sweep), `ingest_nfa_principals.py` "
        "(named officers/owners), `run_nfa_enrichment.py` (website + email "
        "discovery, P2). Re-run any of them from a terminal to refresh."
    )

tab_sec, tab_nfa = st.tabs(["📊 SEC ADV", "🏛️ NFA CPO/CTA"])

# ============================================================
# SEC ADV TAB
# ============================================================
with tab_sec:
    # --- Metrics ---
    @st.cache_data(show_spinner=False)
    def _count_new_sec_entries(cache_fetched_at: str) -> int:
        """Cached per SEC cache refresh (keyed by fetched_at) — get_adv_candidates()
        already filters to firms not yet in prospects.db, so its length is exactly
        'how many new entries are available to ingest'. Avoids re-scanning the
        ~17k-row cache on every dashboard rerun."""
        try:
            return len(ingest_sec_adv.get_adv_candidates())
        except FileNotFoundError:
            return 0


    counts = db.counts_by_status()
    total = sum(counts.values())
    all_rows = db.get_all_prospects()
    n_enriched = sum(1 for r in all_rows if r["email"])
    n_verified = sum(1 for r in all_rows if r["email_verified"])
    n_named = sum(1 for r in all_rows if r["contact_name"])
    n_deregistered = sum(1 for r in all_rows if r["deregistered_at"])

    cols = st.columns(6)
    cols[0].metric("Total prospects", total)
    cols[1].metric("Enriched", n_enriched)
    cols[2].metric("SMTP-verified", n_verified)
    cols[3].metric("Named contact", n_named)
    if cache:
        new_in_sec = _count_new_sec_entries(cache["fetched_at"])
        cols[4].metric("New in SEC data", new_in_sec if new_in_sec else "None")
    else:
        cols[4].metric("New in SEC data", "N/A")
    cols[5].metric("Deregistered", n_deregistered)

    st.divider()

    # --- Prospects table ---
    rows = all_rows
    if not rows:
        st.info("No prospects yet. Use the sidebar to refresh the SEC ADV cache and ingest prospects.")
    else:
        df = pd.DataFrame([dict(r) for r in rows])

        n_deregistered = df["deregistered_at"].notna().sum()
        show_deregistered = False
        if n_deregistered:
            show_deregistered = st.checkbox(f"Show {n_deregistered} deregistered firm(s)", value=False)
        if not show_deregistered:
            df = df[df["deregistered_at"].isna()]

        narrowed_aum_range = None
        aum_series = df["aum"].dropna()
        if len(aum_series):
            aum_min, aum_max = float(aum_series.min()), float(aum_series.max())
            breakpoints = formatting.aum_breakpoints(aum_min, aum_max)
            aum_range = st.select_slider(
                "AUM range",
                options=breakpoints,
                value=(breakpoints[0], breakpoints[-1]),
                format_func=formatting.format_aum,
            )
            if aum_range != (aum_min, aum_max):
                df = df[df["aum"].between(aum_range[0], aum_range[1])]
                narrowed_aum_range = aum_range

        firm_search = st.text_input(
            "🔎 Search firm name",
            placeholder="Doesn't need to be exact. For example, \"greenlight\" finds \"Greenlight Masters, LLC\"",
            key="sec_firm_search",
        )

        filter_cols = st.columns(2)
        type_filter = filter_cols[0].multiselect("Prospect type", sorted(df["prospect_type"].dropna().unique()))
        state_filter = filter_cols[1].multiselect("State", sorted(df["hq_state"].dropna().unique()))
        city_filter: list[str] = []

        if type_filter:
            df = df[df["prospect_type"].isin(type_filter)]
        if state_filter:
            df = df[df["hq_state"].isin(state_filter)]

        if firm_search.strip():
            query = firm_search.strip().lower()
            substring_hits = df[df["firm_name"].str.lower().str.contains(re.escape(query), na=False)]
            if not substring_hits.empty:
                df = substring_hits
            else:
                # No literal substring match anywhere (typo, abbreviation, word
                # order, missing legal suffix, etc.) — fall back to closest
                # matches by name similarity rather than returning nothing.
                scores = df["firm_name"].apply(
                    lambda name: difflib.SequenceMatcher(None, query, str(name).lower()).ratio()
                )
                df = df.assign(_match_score=scores).sort_values("_match_score", ascending=False)
                df = df[df["_match_score"] > 0.4].head(25).drop(columns="_match_score")
                if df.empty:
                    st.caption(f"No close matches found for \"{firm_search}\".")

        df = df.assign(aum_display=df["aum"].apply(formatting.format_aum))

        # Reverse of the NFA tab's "Also SEC-Registered" flag -- built from
        # the same nfa_firms.sec_prospect_id link (crosslink_nfa_sec.py),
        # just queried in the other direction here.
        also_nfa_ids = {f["sec_prospect_id"] for f in nfa_db.get_firms() if f["sec_prospect_id"]}
        df = df.assign(also_nfa=df["id"].isin(also_nfa_ids))

        display_cols = [
            "firm_name", "prospect_type", "hq_city", "hq_state", "aum_display",
            "contact_name", "contact_title", "email", "email_verified", "website",
            "linkedin_profile_url", "linkedin_search_url", "status", "also_nfa",
        ]
        if show_deregistered:
            display_cols.append("deregistered_at")
        table_event = st.dataframe(
            df[display_cols].rename(columns={
                "firm_name": "Firm", "prospect_type": "Type", "hq_city": "City",
                "also_nfa": "Also NFA-Registered",
                "hq_state": "State", "aum_display": "AUM", "contact_name": "Contact",
                "contact_title": "Title", "email": "Email", "email_verified": "Verified",
                "website": "Website", "linkedin_profile_url": "Find This Person",
                "linkedin_search_url": "LinkedIn Search",
                "status": "Status", "deregistered_at": "Deregistered",
            }),
            width="stretch",
            hide_index=True,
            column_config={
                "Verified": st.column_config.CheckboxColumn(),
                "Website": st.column_config.LinkColumn(display_text=r"https?://(?:www\.)?([^/]+)"),
                "Find This Person": st.column_config.LinkColumn(display_text="👤 Find"),
                "LinkedIn Search": st.column_config.LinkColumn(display_text="🔍 Search"),
                "Also NFA-Registered": st.column_config.CheckboxColumn(),
            },
            on_select="rerun",
            selection_mode="single-row",
            key="sec_main_table",
        )
        st.caption(f"Showing {len(df)} of {total} prospects. Click a row to jump to it below.")

        # Clicking a row above jumps the "Contacts by firm" dropdown straight
        # to that firm (2026-07-17, requested in place of double-click, which
        # st.dataframe has no event for). Setting session_state for the
        # selectbox's own key BEFORE it's instantiated below is what makes it
        # jump rather than just changing the default on first render.
        clicked_rows = table_event["selection"]["rows"] if table_event else []
        if clicked_rows:
            st.session_state["contacts_firm_select"] = df.iloc[clicked_rows[0]]["firm_name"]

        # --- Contacts by firm: pick a firm from the filtered list above,
        # see all its contacts (primary + secondary) together ---
        st.divider()
        st.subheader("👥 Contacts by firm")
        if df.empty:
            st.caption("No firms match the current filters.")
        else:
            firm_names = sorted(df["firm_name"].unique().tolist())
            if st.session_state.get("contacts_firm_select") not in firm_names:
                st.session_state.pop("contacts_firm_select", None)  # stale pick from before a filter changed
            selected_firm = st.selectbox("Select a firm", options=firm_names, key="contacts_firm_select")
            selected_row = df.loc[df["firm_name"] == selected_firm].iloc[0]
            selected_id = int(selected_row["id"])

            contact_rows = []
            if pd.notna(selected_row.get("contact_name")):
                contact_rows.append({
                    "Contact Type": "Main contact", "Contact": selected_row["contact_name"],
                    "Title": selected_row.get("contact_title") or "",
                    "Email": selected_row.get("email") or "",
                    "Verified": bool(selected_row.get("email_verified")),
                    "Find This Person": selected_row.get("linkedin_profile_url") or "",
                })
            for c in db.get_contacts_for_prospect(selected_id):
                contact_rows.append({
                    "Contact Type": "Additional contact", "Contact": c["contact_name"],
                    "Title": c["contact_title"] or "", "Email": c["email"] or "",
                    "Verified": bool(c["email_verified"]),
                    "Find This Person": c["linkedin_profile_url"] or "",
                })

            if not contact_rows:
                st.caption(f"No contacts found yet for {selected_firm}.")
            else:
                st.dataframe(
                    pd.DataFrame(contact_rows),
                    width="stretch", hide_index=True,
                    column_config={
                        "Verified": st.column_config.CheckboxColumn(),
                        "Find This Person": st.column_config.LinkColumn(display_text="👤 Find"),
                    },
                )

            # --- LinkedIn mismatch correction for the selected firm ---
            # A human has already confirmed the real name/firm on a live
            # LinkedIn account; the LLM's only job is parsing that free-text
            # description into a structured value -- see
            # core/linkedin_override.py for why this is safe where guessing
            # a brand name from scratch wouldn't be.
            if llm_enabled:
                st.markdown("**Correct a LinkedIn mismatch for this firm**")
                correction_text = st.text_area(
                    "Describe the correction, or paste a LinkedIn profile URL. "
                    "For example: \"the firm is called The Suby Group on LinkedIn\" "
                    "or \"he goes by Brad Benz\"",
                    key="sec_linkedin_correction_text",
                )
                if st.button("💾 Save correction", key="sec_linkedin_correction_save"):
                    if not correction_text.strip():
                        st.warning("Type a description first.")
                        pasted_url = None
                    else:
                        pasted_url = linkedin_override.extract_profile_url(correction_text.strip())
                    if pasted_url:
                        # A real profile URL is the confirmed answer itself --
                        # no LLM call needed, and it must never be regenerated
                        # by a future re-enrichment pass.
                        db.update_prospect(
                            selected_id, linkedin_profile_url=pasted_url, linkedin_url_confirmed=1,
                        )
                        st.success(f"Saved confirmed profile link: {pasted_url}")
                        st.rerun()
                    elif correction_text.strip():
                        with st.spinner("Parsing correction..."):
                            parsed = linkedin_override.parse_correction(
                                selected_firm, selected_row.get("contact_name"), correction_text.strip(),
                            )
                        if parsed["model"] == "none":
                            st.error("LLM unavailable: Groq and Ollama both failed. Try again later.")
                        elif not parsed["firm_override"] and not parsed["person_override"]:
                            st.warning("Couldn't extract a correction from that text. Try rephrasing.")
                        else:
                            hq_state = selected_row.get("hq_state")
                            effective_firm = parsed["firm_override"] or selected_firm
                            effective_person = parsed["person_override"] or selected_row.get("contact_name")
                            update_fields = {"linkedin_search_url": linkedin_url.build_search_url(effective_firm, hq_state)}
                            if parsed["firm_override"]:
                                update_fields["linkedin_firm_override"] = parsed["firm_override"]
                            if parsed["person_override"]:
                                update_fields["linkedin_person_override"] = parsed["person_override"]
                            if effective_person and pd.notna(selected_row.get("contact_name")):
                                update_fields["linkedin_profile_url"] = linkedin_url.build_person_url(effective_person, effective_firm, hq_state)
                            db.update_prospect(selected_id, **update_fields)
                            st.success(
                                f"Saved. Firm: {parsed['firm_override'] or 'unchanged'}. "
                                f"Person: {parsed['person_override'] or 'unchanged'}. "
                                "This correction will keep applying after future re-enrichment."
                            )
                            st.rerun()
            else:
                st.caption("Turn on \"Enable LLM-powered features\" in the sidebar to correct a LinkedIn mismatch.")

        # --- Excel export, same filtered scope as above ---
        st.divider()
        st.subheader("💾 Export to Excel")
        export_cols = st.columns(2)
        with export_cols[0]:
            filename_preview = excel_export.build_filename(state_filter, city_filter, narrowed_aum_range)
            st.caption(f"**Current view**: the {len(df)} currently filtered prospects, "
                       f"2 sheets (Prospects and Additional Contacts).")
            if st.button("💾 Export current view"):
                path = excel_export.export_prospects(df, state_filter, city_filter, narrowed_aum_range)
                st.success(f"Saved to {path}")
        with export_cols[1]:
            st.caption("**Full contact database**: every SEC prospect and contact, "
                       "one row per person, with AUM, City, and State on every row for "
                       "easy filtering in Excel. Ignores the filters above; always exports "
                       "the full list.")
            if st.button("💾 Export full contact database"):
                with st.spinner("Building full export (may take a minute for 60k+ rows)..."):
                    path = excel_export.export_full_database()
                st.success(f"Saved to {path}")

        # --- Retry previously-failed, scoped to whatever is currently filtered above ---
        st.divider()
        st.subheader("♻️ Retry companies with no email found")
        already_failed = df[df["email"].isna() & (df["status"] == "Enriched")]
        n_failed = len(already_failed)
        if n_failed == 0:
            st.caption("No previously-tried-and-failed companies in this filtered view.")
        else:
            st.caption(
                f"{n_failed} of {len(df)} filtered prospects were already enriched once "
                "and no email was found, so they're excluded from the button above to "
                "avoid silently redoing known failures. Click here to give them another "
                "full attempt; their website or SEC filing may have changed since the "
                "last try."
            )
            if st.button(f"♻️ Retry {min(n_failed, ENRICH_BATCH_CAP)} failed companies"):
                progress = st.progress(0.0)
                status_text = st.empty()

                def _on_retry_progress(done: int, total_n: int) -> None:
                    progress.progress(done / total_n)
                    status_text.text(f"{done}/{total_n} retried")

                retry_ids = already_failed["id"].tolist()[:ENRICH_BATCH_CAP]
                result = enrich.enrich_prospects(retry_ids, progress_callback=_on_retry_progress)
                progress.empty()
                status_text.empty()
                st.success(
                    f"Found emails for {result['enriched']} ({result['verified']} SMTP-verified) "
                    f"that had nothing before; still no email for {result['no_email']}."
                )
                st.rerun()

        # --- Re-verify, scoped to whatever is currently filtered above ---
        st.divider()
        st.subheader("🔁 Re-verify unconfirmed emails")
        needs_reverify = df[df["email"].notna() & (df["email_verified"] == 0)]
        n_reverify = len(needs_reverify)
        if n_reverify == 0:
            st.caption("No unverified emails in this filtered view.")
        else:
            st.caption(
                f"{n_reverify} of {len(df)} filtered prospects have an email that "
                "couldn't be confirmed yet. This isn't necessarily wrong; many mail "
                "servers don't answer the free check on the first try. This only "
                "re-runs the MX and SMTP handshake against the same email already "
                "on file. It does not search for a different address or redo any "
                "discovery or scraping."
            )
            if st.button(f"🔁 Re-verify {n_reverify} emails"):
                progress = st.progress(0.0)
                status_text = st.empty()

                def _on_reverify_progress(done: int, total_n: int) -> None:
                    progress.progress(done / total_n)
                    status_text.text(f"{done}/{total_n} checked")

                reverify_ids = needs_reverify["id"].tolist()
                result = enrich.reverify_emails(reverify_ids, progress_callback=_on_reverify_progress)
                progress.empty()
                status_text.empty()
                st.success(
                    f"{result['now_verified']} now verified, "
                    f"{result['still_unverified']} still unconfirmed."
                )
                st.rerun()

        # --- Duplicate review (Phase 2 LLM dedup) — hidden while the LLM toggle is off ---
        if llm_enabled:
            st.divider()
            st.subheader("🔁 Possible duplicates")
            dupes = db.get_confirmed_duplicates()
            last_scan = settings.get("dedup_last_scan_at")
            last_scan_display = f"{last_scan[:19].replace('T', ' ')} UTC" if last_scan else "never"
            pending_candidates = dedup.find_new_candidate_pairs([dict(r) for r in db.get_all_prospects()])
            st.caption(
                f"{len(dupes)} confirmed duplicate pair(s) on file, "
                f"{len(pending_candidates)} new candidate pair(s) pending review. "
                f"Last checked: {last_scan_display}."
            )
            st.caption(
                "Candidate pairs are cheap to generate; each is LLM-adjudicated with "
                "HQ, CRD, and AUM context before being reported, to avoid flagging "
                "genuinely different entities such as a US firm and its overseas "
                "affiliate. Every verdict is remembered, so a scan only checks "
                "genuinely new candidate pairs."
            )
            if st.button(f"🔎 Run duplicate scan ({len(pending_candidates)} pending)", disabled=not pending_candidates):
                progress = st.progress(0.0)
                status_text = st.empty()
                confirmed = 0
                for i, (a, b) in enumerate(pending_candidates, start=1):
                    same, reason = dedup.llm_adjudicate_pair(a, b)
                    db.record_dedup_verdict(a["id"], b["id"], same, reason)
                    if same:
                        confirmed += 1
                    progress.progress(i / len(pending_candidates))
                    status_text.text(f"{i}/{len(pending_candidates)} checked, {confirmed} duplicate(s) found")
                progress.empty()
                status_text.empty()
                settings.set("dedup_last_scan_at", datetime.now(timezone.utc).isoformat())
                st.success(f"Scan complete: {confirmed} new duplicate pair(s) found out of {len(pending_candidates)} checked.")
                st.rerun()

            if dupes:
                st.caption("Review before merging:")
                for i, pair in enumerate(dupes):
                    with st.container(border=True):
                        cols = st.columns([3, 3, 2, 1, 1])
                        cols[0].write(f"**{pair['a_name']}**  \n`id {pair['a_id']}`")
                        cols[1].write(f"**{pair['b_name']}**  \n`id {pair['b_id']}`")
                        cols[2].caption(pair["reason"])
                        if cols[3].button("Merge", key=f"merge_{i}"):
                            dedup.merge_prospects(keep_id=pair["a_id"], remove_id=pair["b_id"])
                            st.rerun()
                        if cols[4].button("Not a dupe", key=f"dismiss_{i}"):
                            db.record_dedup_verdict(pair["a_id"], pair["b_id"], same=False, reason="dismissed by user")
                            st.rerun()

# ============================================================
# NFA CPO/CTA TAB
# ============================================================
with tab_nfa:
    nfa_all_rows = nfa_db.get_firms()
    nfa_total = len(nfa_all_rows)

    if nfa_total == 0:
        st.info("No NFA firms yet. Run `python ingest_nfa_firms.py` from a terminal to build the roster.")
    else:
        nfa_with_website = sum(1 for r in nfa_all_rows if r["website"])
        nfa_all_principals_rows = nfa_db.get_all_principals()
        nfa_with_email = sum(1 for r in nfa_all_principals_rows if r["email"])
        nfa_verified = sum(1 for r in nfa_all_principals_rows if r["email_verified"])
        nfa_dual_registered = sum(1 for r in nfa_all_rows if r["sec_prospect_id"])

        cols = st.columns(6)
        cols[0].metric("Total NFA firms", nfa_total)
        cols[1].metric("Firms with website", nfa_with_website)
        cols[2].metric("Principals w/ email", nfa_with_email)
        cols[3].metric("SMTP-verified", nfa_verified)
        cols[4].metric("Total principals", len(nfa_all_principals_rows))
        cols[5].metric("Also SEC-registered", nfa_dual_registered)

        st.caption(
            "NFA discloses named officers and owners directly (getPrincipals), "
            "so no PDF brochure parsing is needed here, unlike the SEC side. "
            "Website and email are discovered via cross-reference against "
            "SEC-registered dual-filers, or verified domain guessing when no "
            "cross-reference exists (see nfa_enrich.py). Corporate and trust "
            "entities disclosed as 10%+ owners are excluded from email "
            "guessing; only real individuals get a personal-email attempt. "
            "\"Also SEC-registered\" means the same firm already exists as a "
            "prospect in the SEC ADV tab (run `python crosslink_nfa_sec.py` "
            "to refresh this after a fresh ingest on either side). Worth "
            "checking there for a richer profile, such as a named contact "
            "from the ADV brochure or AUM, before treating it as a separate lead."
        )

        st.divider()

        nfa_df = pd.DataFrame([dict(r) for r in nfa_all_rows])

        nfa_firm_search = st.text_input(
            "🔎 Search firm name",
            placeholder="e.g. \"capital\" finds every firm with Capital in the name",
            key="nfa_firm_search",
        )

        nfa_filter_cols = st.columns(5)
        reg_type_options = sorted({
            rt.strip() for types in nfa_df["reg_types"].dropna() for rt in types.split(",")
        })
        reg_type_filter = nfa_filter_cols[0].multiselect("Registration type", reg_type_options)
        nfa_state_filter = nfa_filter_cols[1].multiselect("State", sorted(nfa_df["state"].dropna().unique()))
        reg_actions_filter = nfa_filter_cols[2].selectbox(
            "Disciplinary history", ["All", "Only firms WITH reg actions", "Only firms with NO reg actions"],
        )
        dual_filter = nfa_filter_cols[3].selectbox(
            "SEC dual-registration", ["All", "Only dual-registered (also SEC)", "Only NFA-only firms"],
        )
        crm_stage_filter = nfa_filter_cols[4].multiselect("CRM stage", config.STATUS_STAGES)

        if reg_type_filter:
            nfa_df = nfa_df[nfa_df["reg_types"].apply(
                lambda v: isinstance(v, str) and any(rt in v for rt in reg_type_filter)
            )]
        if nfa_state_filter:
            nfa_df = nfa_df[nfa_df["state"].isin(nfa_state_filter)]
        if reg_actions_filter == "Only firms WITH reg actions":
            nfa_df = nfa_df[nfa_df["has_reg_actions"] == "Yes"]
        elif reg_actions_filter == "Only firms with NO reg actions":
            nfa_df = nfa_df[nfa_df["has_reg_actions"] != "Yes"]
        if dual_filter == "Only dual-registered (also SEC)":
            nfa_df = nfa_df[nfa_df["sec_prospect_id"].notna()]
        elif dual_filter == "Only NFA-only firms":
            nfa_df = nfa_df[nfa_df["sec_prospect_id"].isna()]
        if crm_stage_filter:
            nfa_df = nfa_df[nfa_df["crm_stage"].isin(crm_stage_filter)]

        if nfa_firm_search.strip():
            query = nfa_firm_search.strip().lower()
            substring_hits = nfa_df[nfa_df["firm_name"].str.lower().str.contains(re.escape(query), na=False)]
            if not substring_hits.empty:
                nfa_df = substring_hits
            else:
                scores = nfa_df["firm_name"].apply(
                    lambda name: difflib.SequenceMatcher(None, query, str(name).lower()).ratio()
                )
                nfa_df = nfa_df.assign(_match_score=scores).sort_values("_match_score", ascending=False)
                nfa_df = nfa_df[nfa_df["_match_score"] > 0.4].head(25).drop(columns="_match_score")
                if nfa_df.empty:
                    st.caption(f"No close matches found for \"{nfa_firm_search}\".")

        nfa_df = nfa_df.assign(also_sec=nfa_df["sec_prospect_id"].notna())

        nfa_display_cols = [
            "firm_name", "reg_types", "city", "state", "membership_status",
            "has_reg_actions", "website", "website_source", "also_sec", "crm_stage",
        ]
        nfa_table_event = st.dataframe(
            nfa_df[nfa_display_cols].rename(columns={
                "firm_name": "Firm", "reg_types": "Registration Types", "city": "City",
                "state": "State", "membership_status": "Membership Status",
                "has_reg_actions": "Reg Actions", "website": "Website",
                "website_source": "Website Source", "also_sec": "Also SEC-Registered",
                "crm_stage": "CRM Stage",
            }),
            width="stretch",
            hide_index=True,
            column_config={
                "Website": st.column_config.LinkColumn(display_text=r"(?:https?://)?(?:www\.)?([^/]+)"),
                "Also SEC-Registered": st.column_config.CheckboxColumn(),
            },
            on_select="rerun",
            selection_mode="single-row",
            key="nfa_main_table",
        )
        st.caption(f"Showing {len(nfa_df)} of {nfa_total} NFA firms. Click a row to jump to it below.")

        nfa_clicked_rows = nfa_table_event["selection"]["rows"] if nfa_table_event else []
        if nfa_clicked_rows:
            st.session_state["nfa_contacts_firm_select"] = nfa_df.iloc[nfa_clicked_rows[0]]["firm_name"]

        # --- Bulk CRM stage update, same filtered scope as above ---
        st.divider()
        st.subheader("🏷️ Update CRM stage for filtered firms")
        st.caption(
            f"Moves all {len(nfa_df)} currently filtered NFA firms to the chosen "
            "stage, using the same pipeline stages as the SEC tab. Outreach "
            "itself (cold email or LinkedIn) isn't built yet; this just tracks "
            "where each firm stands once that starts."
        )
        stage_cols = st.columns([3, 1])
        new_stage = stage_cols[0].selectbox("New stage", config.STATUS_STAGES, key="nfa_crm_stage_select")
        if stage_cols[1].button(f"Apply to {len(nfa_df)} firms"):
            for fid in nfa_df["id"].tolist():
                nfa_db.update_firm(int(fid), crm_stage=new_stage)
            st.success(f"Moved {len(nfa_df)} firms to \"{new_stage}\".")
            st.rerun()

        # --- Principals by firm: pick a firm from the filtered list above,
        # see all its principals (with email/verification) together ---
        st.divider()
        st.subheader("👥 Principals by firm")
        if nfa_df.empty:
            st.caption("No firms match the current filters.")
        else:
            nfa_firm_names = sorted(nfa_df["firm_name"].unique().tolist())
            if st.session_state.get("nfa_contacts_firm_select") not in nfa_firm_names:
                st.session_state.pop("nfa_contacts_firm_select", None)  # stale pick from before a filter changed
            selected_nfa_firm = st.selectbox("Select a firm", options=nfa_firm_names, key="nfa_contacts_firm_select")
            selected_nfa_id = int(nfa_df.loc[nfa_df["firm_name"] == selected_nfa_firm, "id"].iloc[0])

            firm_principals = nfa_db.get_principals_for_firm(selected_nfa_id)
            if not firm_principals:
                st.caption(f"No principals found yet for {selected_nfa_firm}.")
            else:
                st.dataframe(
                    pd.DataFrame([dict(r) for r in firm_principals])[
                        ["name", "title", "ten_percent_owner", "email", "email_verified", "linkedin_profile_url"]
                    ].rename(columns={
                        "name": "Name", "title": "Title", "ten_percent_owner": "10%+ Owner",
                        "email": "Email", "email_verified": "Verified",
                        "linkedin_profile_url": "Find This Person",
                    }),
                    width="stretch", hide_index=True,
                    column_config={
                        "Verified": st.column_config.CheckboxColumn(),
                        "10%+ Owner": st.column_config.CheckboxColumn(),
                        "Find This Person": st.column_config.LinkColumn(display_text="👤 Find"),
                    },
                )

            # --- LinkedIn mismatch correction for the selected firm ---
            # Same mechanism as the SEC tab (see core/linkedin_override.py) --
            # a firm can have several principals here, so the correction also
            # needs to say which one (if any) a person-name fix applies to.
            selected_nfa_row = nfa_df.loc[nfa_df["firm_name"] == selected_nfa_firm].iloc[0]
            if llm_enabled:
                st.markdown("**Correct a LinkedIn mismatch for this firm**")
                principal_dicts = [dict(r) for r in firm_principals]
                principal_choice = None
                if principal_dicts:
                    principal_choice = st.selectbox(
                        "Which principal does this correction apply to? Leave as "
                        "firm name only if it's just the firm's public or DBA name.",
                        options=["(firm name only)"] + [p["name"] for p in principal_dicts],
                        key="nfa_correction_principal_select",
                    )
                    if principal_choice == "(firm name only)":
                        principal_choice = None
                nfa_correction_text = st.text_area(
                    "Describe the correction, or paste a LinkedIn profile URL. "
                    "For example: \"the firm is called X on LinkedIn\" or \"he goes by Y\"",
                    key="nfa_linkedin_correction_text",
                )
                if st.button("💾 Save correction", key="nfa_linkedin_correction_save"):
                    if not nfa_correction_text.strip():
                        st.warning("Type a description first.")
                        pasted_url = None
                    else:
                        pasted_url = linkedin_override.extract_profile_url(nfa_correction_text.strip())
                    if pasted_url and not principal_choice:
                        st.warning("A pasted profile URL applies to one specific person. Pick which principal it belongs to above first.")
                    elif pasted_url:
                        # A real profile URL is the confirmed answer itself --
                        # no LLM call needed, and it must never be regenerated
                        # by a future re-enrichment pass.
                        p = next(p for p in principal_dicts if p["name"] == principal_choice)
                        nfa_db.update_principal(p["id"], linkedin_profile_url=pasted_url, linkedin_url_confirmed=1)
                        st.success(f"Saved confirmed profile link for {principal_choice}: {pasted_url}")
                        st.rerun()
                    elif nfa_correction_text.strip():
                        with st.spinner("Parsing correction..."):
                            parsed = linkedin_override.parse_correction(
                                selected_nfa_firm, principal_choice, nfa_correction_text.strip(),
                            )
                        if parsed["model"] == "none":
                            st.error("LLM unavailable: Groq and Ollama both failed. Try again later.")
                        elif not parsed["firm_override"] and not parsed["person_override"]:
                            st.warning("Couldn't extract a correction from that text. Try rephrasing.")
                        else:
                            hq_state = selected_nfa_row.get("state")
                            effective_firm = parsed["firm_override"] or selected_nfa_firm
                            if parsed["firm_override"]:
                                nfa_db.update_firm(selected_nfa_id, linkedin_firm_override=parsed["firm_override"])
                            if parsed["person_override"] and principal_choice:
                                p = next(p for p in principal_dicts if p["name"] == principal_choice)
                                new_url = linkedin_url.build_person_url(parsed["person_override"], effective_firm, hq_state)
                                nfa_db.update_principal(p["id"], linkedin_person_override=parsed["person_override"], linkedin_profile_url=new_url)
                            elif parsed["firm_override"]:
                                # A firm-name-only correction still changes every
                                # principal's search URL -- regenerate them all now
                                # instead of waiting for a future re-enrichment pass.
                                for p in principal_dicts:
                                    person_name = p.get("linkedin_person_override") or p["name"]
                                    new_url = linkedin_url.build_person_url(person_name, effective_firm, hq_state)
                                    nfa_db.update_principal(p["id"], linkedin_profile_url=new_url)
                            st.success(
                                f"Saved. Firm: {parsed['firm_override'] or 'unchanged'}. "
                                f"Person: {parsed['person_override'] or 'unchanged'}. "
                                "This correction will keep applying after future re-enrichment."
                            )
                            st.rerun()
            else:
                st.caption("Turn on \"Enable LLM-powered features\" in the sidebar to correct a LinkedIn mismatch.")

        # --- Excel export, same filtered scope as above ---
        st.divider()
        st.subheader("💾 Export to Excel")
        nfa_export_cols = st.columns(2)
        with nfa_export_cols[0]:
            nfa_filename_preview = nfa_excel_export.build_filename(nfa_state_filter, reg_type_filter)
            st.caption(f"**Current view**: the {len(nfa_df)} currently filtered NFA firms, "
                       f"2 sheets (Firms and Principals).")
            if st.button("💾 Export current view", key="nfa_export_current"):
                path = nfa_excel_export.export_firms(nfa_df, nfa_state_filter, reg_type_filter)
                st.success(f"Saved to {path}")
        with nfa_export_cols[1]:
            st.caption("**Full contact database**: every NFA firm and principal, one row "
                       "per person, with City and State on every row for easy filtering. "
                       "Ignores the filters above; always exports the full list.")
            if st.button("💾 Export full contact database", key="nfa_export_full"):
                with st.spinner("Building full export..."):
                    path = nfa_excel_export.export_full_database()
                st.success(f"Saved to {path}")
