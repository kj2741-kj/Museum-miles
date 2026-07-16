"""Museum Mile Funds — marketing/prospecting dashboard. SEC ADV + NFA CPO/CTA tabs."""
import difflib
import re

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

ENRICH_BATCH_CAP = 200  # keep a single click bounded; re-click to continue

st.set_page_config(page_title="Museum Mile — Prospecting", page_icon="🖼️", layout="wide")
db.init_db()
nfa_db.init_db()

st.title("🖼️ Museum Mile Funds — Prospecting Dashboard")

# --- Sidebar: LLM features toggle ---
with st.sidebar:
    st.header("🤖 LLM Features")
    llm_enabled = st.toggle(
        "Enable LLM-powered duplicate detection",
        value=settings.get("llm_enabled"),
        help="Uses Groq (cloud, needs an API key) with local Ollama as a "
             "fallback. Only affects duplicate detection — email discovery "
             "and verification never use an LLM. Off by default until "
             "Groq/Ollama is set up and confirmed working.",
    )
    if llm_enabled != settings.get("llm_enabled"):
        settings.set("llm_enabled", llm_enabled)
        st.rerun()
    if not llm_enabled:
        st.caption("Duplicate review is hidden while this is off.")

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
        st.caption("No cache yet — click Refresh below.")
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
                            f"file (likely deregistered) — flagged, not deleted: "
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
    st.caption("No filters applied at ingest — every firm in the SEC ADV bulk "
               "file is imported. Narrow down with the AUM/location filters "
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
        cols[4].metric("New in SEC data", "—")
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
            placeholder="Doesn't need to be exact or complete — e.g. \"greenlight\" finds \"Greenlight Masters, LLC\"",
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
        st.dataframe(
            df[display_cols].rename(columns={
                "firm_name": "Firm", "prospect_type": "Type", "hq_city": "City",
                "also_nfa": "Also NFA-Registered",
                "hq_state": "State", "aum_display": "AUM", "contact_name": "Contact",
                "contact_title": "Title", "email": "Email", "email_verified": "Verified",
                "website": "Website", "linkedin_profile_url": "Find This Person",
                "linkedin_search_url": "LinkedIn Search",
                "status": "Status", "deregistered_at": "Deregistered",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Verified": st.column_config.CheckboxColumn(),
                "Website": st.column_config.LinkColumn(display_text=r"https?://(?:www\.)?([^/]+)"),
                "Find This Person": st.column_config.LinkColumn(display_text="👤 Find"),
                "LinkedIn Search": st.column_config.LinkColumn(display_text="🔍 Search"),
                "Also NFA-Registered": st.column_config.CheckboxColumn(),
            },
        )
        st.caption(f"Showing {len(df)} of {total} prospects")

        # --- Additional (secondary) contacts for the firms currently shown ---
        # A firm can have more than one real contact (e.g. several co-founders
        # found on a team page) — the main table above only shows the single
        # "primary" contact per firm; the rest live here instead of being
        # discarded.
        all_contacts = pd.DataFrame([dict(r) for r in db.get_all_contacts()])
        if not all_contacts.empty:
            extra = all_contacts[all_contacts["prospect_id"].isin(df["id"])]
            if not extra.empty:
                with st.expander(f"👥 {len(extra)} additional contact(s) for the firms shown above"):
                    st.dataframe(
                        extra[["firm_name", "contact_name", "contact_title", "email", "email_verified", "linkedin_profile_url"]].rename(columns={
                            "firm_name": "Firm", "contact_name": "Contact", "contact_title": "Title",
                            "email": "Email", "email_verified": "Verified", "linkedin_profile_url": "Find This Person",
                        }),
                        use_container_width=True, hide_index=True,
                        column_config={
                            "Verified": st.column_config.CheckboxColumn(),
                            "Find This Person": st.column_config.LinkColumn(display_text="👤 Find"),
                        },
                    )

        # --- Enrichment, scoped to whatever is currently filtered above ---
        # Only ever-untried prospects — previously-tried-and-failed ones (status
        # 'Enriched' with no email) are deliberately excluded here so a normal
        # click never silently redoes known failures; use the Retry section below
        # for those on purpose.
        st.divider()
        st.subheader("📧 Find emails for this filtered list")
        needs_enrichment = df[df["email"].isna() & (df["status"] == "New")]
        n_needs = len(needs_enrichment)
        if n_needs == 0:
            st.caption("Every prospect in this filtered view has already been enriched at least once.")
        else:
            batch = min(n_needs, ENRICH_BATCH_CAP)
            est_seconds = batch * 8  # ~8s/item effective throughput observed at the default 8 workers
            note = "" if batch == n_needs else f" (capped at {ENRICH_BATCH_CAP} per click — re-click to continue with the rest)"
            st.caption(
                f"{n_needs} of {len(df)} filtered prospects have no email yet. "
                f"This click will process {batch}{note}, est. ~{est_seconds:.0f}s. "
                "For each firm: looks up their SEC ADV brochure for a named senior "
                "person (key-person disclosures, etc.) and guesses their email, or "
                "uses an email directly disclosed on the brochure's cover page. If "
                "the SEC-listed website is a LinkedIn/social page, recovers the "
                "firm's real site from that same brochure. Otherwise scrapes the "
                "firm's website, falling back to info@/contact@/ir@. Verifies via "
                "free syntax + MX + SMTP-handshake checks (no paid tools) where possible."
            )
            if st.button(f"🔎 Enrich {batch} prospects"):
                progress = st.progress(0.0)
                status_text = st.empty()

                def _on_progress(done: int, total_n: int) -> None:
                    progress.progress(done / total_n)
                    status_text.text(f"{done}/{total_n} processed")

                target_ids = needs_enrichment["id"].tolist()[:batch]
                result = enrich.enrich_prospects(target_ids, progress_callback=_on_progress)
                progress.empty()
                status_text.empty()
                st.success(
                    f"Found emails for {result['enriched']} ({result['verified']} SMTP-verified), "
                    f"no email found for {result['no_email']}."
                )
                st.rerun()

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
                "and no email was found — excluded from the button above so it never "
                "silently redoes known failures. Click here to explicitly give them "
                "another full attempt (their website or SEC filing may have changed "
                "since the last try)."
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
                "couldn't be confirmed yet (not necessarily wrong — many mail "
                "servers don't answer the free check on the first try). This "
                "only re-runs the MX+SMTP handshake against the SAME email "
                "already on file — it does not search for a different address "
                "or redo any discovery/scraping."
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

        # --- Excel export, same filtered scope as above ---
        st.divider()
        st.subheader("💾 Export to Excel")
        filename_preview = excel_export.build_filename(state_filter, city_filter, narrowed_aum_range)
        st.caption(f"Exports the {len(df)} currently filtered prospects to `exports/{filename_preview}`.")
        if st.button("💾 Export filtered list to Excel"):
            path = excel_export.export_prospects(df, state_filter, city_filter, narrowed_aum_range)
            st.success(f"Saved to {path}")

        # --- Duplicate review (Phase 2 LLM dedup) — hidden while the LLM toggle is off ---
        if llm_enabled:
            st.divider()
            st.subheader("🔁 Possible duplicates")
            dupes = db.get_confirmed_duplicates()
            if not dupes:
                st.caption(
                    "No duplicates found yet. Run `python run_dedup_scan.py` to scan the full "
                    "database (candidate pairs are cheap to generate; each is LLM-adjudicated "
                    "with HQ/CRD/AUM context before being reported, to avoid flagging genuinely "
                    "different entities like a US firm vs. its overseas affiliate — and every "
                    "verdict is remembered, so re-scanning after a fresh ingest only checks "
                    "genuinely new candidate pairs)."
                )
            else:
                st.caption(f"{len(dupes)} pairs the LLM confirmed as likely the same real firm — review before merging.")
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
            "NFA discloses named officers/owners directly (getPrincipals) — "
            "no PDF brochure parsing needed, unlike the SEC side. Website/"
            "email are discovered via cross-reference against SEC-registered "
            "dual-filers, or verified domain guessing when no cross-ref exists "
            "(see nfa_enrich.py). Corporate/trust entities disclosed as "
            "10%+ owners are excluded from email guessing — only real "
            "individuals get a personal-email attempt. \"Also SEC-registered\" "
            "means the same real firm already exists as a prospect in the SEC "
            "ADV tab (`python crosslink_nfa_sec.py` to refresh this after a "
            "fresh ingest on either side) — worth checking there for a richer "
            "profile (named contact via ADV brochure, AUM) before treating it "
            "as a separate lead."
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
        st.dataframe(
            nfa_df[nfa_display_cols].rename(columns={
                "firm_name": "Firm", "reg_types": "Registration Types", "city": "City",
                "state": "State", "membership_status": "Membership Status",
                "has_reg_actions": "Reg Actions", "website": "Website",
                "website_source": "Website Source", "also_sec": "Also SEC-Registered",
                "crm_stage": "CRM Stage",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Website": st.column_config.LinkColumn(display_text=r"(?:https?://)?(?:www\.)?([^/]+)"),
                "Also SEC-Registered": st.column_config.CheckboxColumn(),
            },
        )
        st.caption(f"Showing {len(nfa_df)} of {nfa_total} NFA firms")

        # --- Bulk CRM stage update, same filtered scope as above ---
        st.divider()
        st.subheader("🏷️ Update CRM stage for filtered firms")
        st.caption(
            f"Moves all {len(nfa_df)} currently filtered NFA firms to the chosen "
            "stage — same pipeline stages as the SEC tab. Outreach itself "
            "(cold email / LinkedIn) isn't built yet; this just tracks where "
            "each firm stands once that starts."
        )
        stage_cols = st.columns([3, 1])
        new_stage = stage_cols[0].selectbox("New stage", config.STATUS_STAGES, key="nfa_crm_stage_select")
        if stage_cols[1].button(f"Apply to {len(nfa_df)} firms"):
            for fid in nfa_df["id"].tolist():
                nfa_db.update_firm(int(fid), crm_stage=new_stage)
            st.success(f"Moved {len(nfa_df)} firms to \"{new_stage}\".")
            st.rerun()

        # --- Principals for the firms currently shown ---
        nfa_principals_df = pd.DataFrame([dict(r) for r in nfa_all_principals_rows])
        if not nfa_principals_df.empty:
            shown = nfa_principals_df[nfa_principals_df["firm_id"].isin(nfa_df["id"])]
            if not shown.empty:
                with st.expander(f"👥 {len(shown)} principal(s) for the firms shown above"):
                    st.dataframe(
                        shown[["firm_name", "name", "title", "ten_percent_owner", "email", "email_verified", "linkedin_profile_url"]].rename(columns={
                            "firm_name": "Firm", "name": "Name", "title": "Title",
                            "ten_percent_owner": "10%+ Owner", "email": "Email",
                            "email_verified": "Verified", "linkedin_profile_url": "Find This Person",
                        }),
                        use_container_width=True, hide_index=True,
                        column_config={
                            "Verified": st.column_config.CheckboxColumn(),
                            "10%+ Owner": st.column_config.CheckboxColumn(),
                            "Find This Person": st.column_config.LinkColumn(display_text="👤 Find"),
                        },
                    )

        # --- Excel export, same filtered scope as above ---
        st.divider()
        st.subheader("💾 Export to Excel")
        nfa_filename_preview = nfa_excel_export.build_filename(nfa_state_filter, reg_type_filter)
        st.caption(f"Exports the {len(nfa_df)} currently filtered NFA firms (+ their principals) to `exports/{nfa_filename_preview}`.")
        if st.button("💾 Export filtered NFA list to Excel"):
            path = nfa_excel_export.export_firms(nfa_df, nfa_state_filter, reg_type_filter)
            st.success(f"Saved to {path}")
