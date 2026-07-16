# Museum Mile Funds — Prospecting Dashboard

Local Streamlit tool built for Mayank Mohan (Founder, Museum Mile Funds — ~$193M AUM
CTA+RIA, NYC, solo operator). Goal: find allocator prospects (RIAs, family offices,
CPOs) for his S&P futures-basis carry + box-spread financing strategies, and run
dual-track outreach to them. Local-only delivery — no GitHub, no cloud. Folder is
handed to Mayank directly.

## Pipeline (5 stages)
1. **Ingest** — SEC ADV bulk filing → `prospects.db`, diff against existing firms
   (NFA CPO data has no bulk file, only a search UI — deferred, would need scraping)
2. **Enrich** — email discovery (ADV website / pattern guess), LinkedIn people-search URLs
3. **CRM** — `prospects.db`, 7-stage status pipeline, Excel sync
4. **Outreach** — cold email (SMTP) + LinkedIn Matched Audiences CSV export
5. **Automation** — Windows Task Scheduler, monthly

## Build discipline
Strict staged build, test before advancing:
1. Foundation (ingest + CRM skeleton + basic UI) — DONE
2. High priority: multi-LLM routing (DONE), free email verification (DONE, part
   of enrichment), LLM dedup (DONE) — **current phase**
3. Enrichment (DONE) + basic outreach (cold email SMTP + LinkedIn CSV) — outreach not started
4. Test & validate on real data — ongoing (full NYC enrichment pass in progress)
5. Medium priority (only after #4 passes): 3-touch sequence, LinkedIn content
   calendar, prospect research brief
6. Automation

## File map
| File | Purpose |
|---|---|
| `config.py` | Paths, port 8675, 7-stage status list |
| `db.py` | `prospects.db` SQLite — schema, bulk add/update/query, dedup by normalized firm name |
| `ingest_sec_adv.py` | SEC ADV bulk puller (adapted from career-monitor), archives raw file, writes to db |
| `app.py` | Streamlit dashboard — ingest controls + AUM/state/city filters + prospects table + scoped enrichment |
| `enrich.py` | Named-senior-person discovery (via `iapd.py`) + email discovery (website scrape + generic pattern-guess) + free verification (syntax/MX/SMTP), concurrent, runs only against whatever's currently filtered |
| `iapd.py` | Looks up a firm's SEC Form ADV Part 2 brochure by CRD and extracts a named senior person from its prose, if any (best-effort — not every brochure names anyone) |
| `linkedin_url.py` | Builds a plain LinkedIn people-search URL per firm (not scraping) — the manual-assist fallback when automated name discovery finds nothing |
| `formatting.py` | Shared display helpers — `format_aum()` renders `$498.6M`/`$1.2B`/`$11.1T`, used by the dashboard table, AUM slider caption, and Excel export |
| ~~`websearch.py`~~ | **Tried and deleted (Session 9)** — automated web search for named executives. DuckDuckGo/Bing/Google all block automated requests (plain HTTP and full Playwright browser rendering, including a literal CAPTCHA wall from Bing). Not viable without a paid search API or CAPTCHA-solving service. |
| `.streamlit/config.toml` | Port 8675 |
| `start_dashboard.bat` | One-click launcher, auto-frees port 8675 |
| `data/raw/` | Every downloaded SEC ADV bulk file, kept as-is (zip + full unfiltered CSV, all 448 original columns) — for manual inspection, not touched by the app |
| `data/sec_adv_cache.csv` | App's lean working cache (9 columns) derived from the latest raw file |
| `llm.py` | Multi-LLM routing — Groq (`llama-3.3-70b-versatile`, cloud, primary) falls back to local Ollama (`llama3.2:3b`) if Groq is unavailable |
| `dedup.py` | LLM-adjudicated duplicate detection — cheap candidate generation (canonical-key + fuzzy blocking), every candidate confirmed/rejected by the LLM with HQ/CRD/AUM context before being trusted; `merge_prospects()` for one-click merges |
| `excel_export.py` | Exports the currently filtered prospects to `exports/prospects_<location>_<AUM-band>_<date>.xlsx` |
| `run_ny_state_enrichment.py` | One-off batch script: enriches every NY-state prospect without an email. Meant to be launched detached (`nohup ... & disown`) — a full pass takes hours, bottlenecked by external SMTP/IAPD server latency, not our own concurrency (see Session 6/8 below) |
| `run_dedup_scan.py` | One-off batch script: incremental LLM dedup scan — only adjudicates candidate pairs not already recorded in `db.dedup_verdicts` |
| `settings.py` | Tiny persisted dashboard settings (`dashboard_settings.json`) — currently just `llm_enabled` (default off) |
| `.groq_key` | Groq API key (copied from career-monitor's own key — same user, same free account) |

## Filtering philosophy
No AUM/employee/location filters are applied at ingest — every firm in the SEC
ADV bulk file goes into `prospects.db`. Filtering happens interactively in the
dashboard (AUM range slider + location multiselect), with no default filter
applied on page load, so nothing is hidden until Mayank/you narrow it down.

## Tech stack (locked)
Streamlit / SQLite / openpyxl (Excel) / Groq `llama-3.3-70b-versatile` (LLM, free) /
requests + BeautifulSoup4 (scraping) / LinkedIn via URL builders only, no scraping.

## Run
```
Double-click: start_dashboard.bat
```
URL: http://localhost:8675 | Python: `C:\Users\Kartavya\anaconda3\python.exe`

## Open questions for Mayank (ask when prepping a call)
1. NFA Sales Navigator — does he have it? (affects LinkedIn URL builder format)
2. Email volume — 10-20/month (SMTP fine) or 100+ (needs SendGrid)?
3. Filtering criteria — min AUM threshold? geography priority? strategy types?
4. Hunter.io/Apollo.io — willing to pay ~$49/month for verified email enrichment?
5. LinkedIn ad budget — how much/month for Campaign Manager Matched Audiences?
6. Email sending — directly from dashboard, or build list + send separately?
7. Prospect prioritization — CPOs first or RIAs first?

## Session log

### Session 1 (2026-07-10) — Phase 1 foundation
- Scaffolded project, `prospects.db` schema (7-stage CRM pipeline)
- SEC ADV bulk ingest adapted from career-monitor's `sec_adv_discovery.py`,
  broadened beyond NY/NJ/CT (Mayank's allocators can be anywhere in the US)
- Minimal Streamlit UI: refresh ADV cache, ingest new prospects, filter/view table
- Confirmed NFA CPO data has no bulk download (BASIC search UI only) — deferred

### Session 2 (2026-07-10/11) — Real-data test, filter redesign, perf fix
- Tested Phase 1 against the real live SEC ADV bulk file (16,935 firms, all US
  states). SEC.gov's data-research pages intermittently 503 under normal load —
  added retry-with-backoff (`_get_with_retry`) to `ingest_sec_adv.py`.
- Redesigned filtering per feedback: ingest no longer applies AUM/employee/state
  filters — it pulls everything. The dashboard now has an AUM range slider and
  a location multiselect, both defaulting to "no filter" (full range / empty).
  This surfaced immediately why it matters: unfiltered, the AUM-sorted top of
  the list is mega-managers (Vanguard $11T, Fidelity $5.7T) — completely
  irrelevant as Museum Mile allocator prospects. The slider is how Mayank/you
  narrow to a useful AUM band.
- **Found and fixed a real perf bug**: original ingest opened a new SQLite
  connection per row (2x per row, once in `get_adv_candidates` dedup check via
  `db.firm_exists`, once in `add_prospect`). For 16,935 rows this took ~5
  minutes. Fixed by pulling all existing normalized names once
  (`db.get_existing_normalized_names()`) and doing dedup in-memory, plus a
  single connection/commit for the whole batch in `db.bulk_add_prospects()`.
  Same result (16,869 added), now takes ~6 seconds.
- Removed dead code: `"Any Private Equity Funds"` column doesn't actually exist
  in the real SEC ADV feed (verified against the raw file) — the
  `_infer_prospect_type` branch for it could never fire. Removed.
- **Added `data/raw/` archive**: every `refresh_adv_cache()` run now saves the
  original zip and the full unfiltered CSV (448 columns) to `data/raw/`,
  untouched by the app's 9-column working cache — for manual inspection later.
- Data quality snapshot from the real pull: 16,869 unique firms, 16,258 with
  AUM, 15,522 with a website, ~2,233 with a blank HQ state (worth investigating
  before Phase 3 enrichment — likely exempt/international filers).
- Added a **City** filter next to State (both default to empty = no filter).

### Session 3 (2026-07-11) — Scoped email enrichment (`enrich.py`)
Built on request: once Mayank filters (e.g. AUM band + NYC), a button enriches
only that filtered set — not the whole 16,869-row table.
- **Discovery**: scrape the firm's own website (homepage, then `/contact`,
  then `/about`) for `mailto:` links / email text. Falls back to guessing
  `info@`/`contact@`/`ir@` against the firm's domain if nothing found on-site.
  Named-person addresses (e.g. a partner's direct email found on a team page)
  rank above generic mailboxes when both are found.
- **Verification**: syntax check → MX lookup (`dnspython`) → SMTP `RCPT TO`
  handshake (no message ever sent, no paid Hunter.io/Apollo). Confirmed
  outbound port 25 is open on this network before committing to this approach.
- **Real bugs found+fixed during testing**: (1) domain parsing broke on
  `HTTP://...` (uppercase scheme) — `_domain_of` used a case-sensitive
  `.startswith("http")`, producing garbage like `info@HTTP:`; fixed to
  `.lower().startswith(...)`. (2) some ADV "Website Address" values are
  LinkedIn company URLs, not real firm sites — pattern-guessing against
  `linkedin.com` produced `info@linkedin.com`; added a blocklist of
  non-firm domains (LinkedIn, Facebook, X, etc.) that skips discovery/guessing
  entirely. (3) per-firm latency ranged 0.2s–48.5s sequentially (slow/silent
  mail servers on SMTP checks) — capped SMTP verification to the top 2 ranked
  candidates, cut page/SMTP timeouts to 5s, and parallelized across prospects
  with a `ThreadPoolExecutor` (I/O-bound, 8 workers); 20 real NYC firms:
  16 got an email, 7 SMTP-verified, in ~69s total (vs. minutes sequential).
- Dashboard: "🔎 Enrich N prospects" button appears under the table, scoped to
  whatever's currently filtered, capped at 200/click (re-click to continue),
  shows a live progress bar and an estimated time before running.
- Note: SMTP "verified=0" doesn't mean the email is bad — many mail servers
  don't do strict RCPT-TO verification (accept-all/greylisting), so it just
  means free verification couldn't confirm it either way.

### Session 4 (2026-07-11) — Named senior person via IAPD (`iapd.py`)
User asked: on SEC download, auto-fetch+verify emails, and for firms with a
LinkedIn page, go find a senior person there and try their email. Pushed back
on both asks with reasoning, then built the compliant/reliable version:
- **Rejected auto-enrich-on-download**: unfiltered ingest is 16,869 firms;
  even parallelized that's 2+ hours per refresh against firms (Vanguard,
  Fidelity) that will never be real prospects. Enrichment stays scoped to
  whatever's filtered, per [[project-museum-mile-marketing]] build discipline.
- **Rejected LinkedIn people-page scraping**: requires login to view search
  results at all, aggressively rate-limits/blocks, and violates LinkedIn's
  ToS at scale — the same reason career-monitor locked LinkedIn to URL
  builders only, never scraping.
- **Tested (not assumed) two SEC-native alternatives before building anything:**
  - Form ADV Part 1A ("compilation report" PDF, `reports.adviserinfo.sec.gov/
    reports/ADV/{crd}/PDF/{crd}.pdf`) has explicit "Chief Compliance Officer:
    Name / Email" fields — but SEC redacts the actual values on the public
    report. Confirmed blank across 2 real firms (Kahn Brothers, Blue Opal).
    No Schedule A (owners/officers) table appeared either. Also found these
    PDFs can be huge and slow — Blue Opal's was 213 pages / 10MB for one firm.
    **Ruled out.**
  - Form ADV Part 2 ("Firm Brochure") — a narrative document, different URL
    (`files.adviserinfo.sec.gov/IAPD/Content/Common/crd_iapd_Brochure.aspx?
    BRCHR_VRSN_ID=...`; note this legacy domain 404s on non-browser User-Agents,
    needs a real browser UA). This one **works**: brochures disclose things like
    fund "key person" risk and management persons who are also broker-dealer
    reps, both naming real senior individuals in prose. Confirmed on real
    filings: Kahn Brothers -> "Thomas Kahn" (mgmt/broker-dealer disclosure),
    Lion Point Capital -> "Didric Cederholm, founding partner... Chief
    Investment Officer" (key-person disclosure). Blue Opal -> no name found
    (not every brochure names anyone — best-effort, not guaranteed).
- **Built `iapd.py`**: CRD -> `api.adviserinfo.sec.gov` brochure list ->
  download PDF -> `pdfplumber` text -> regex heuristics for name+title
  patterns (key-person, founding-partner, management-persons/broker-dealer).
- **Added `crd_number` to the schema** (`db.py` migration) and to
  `ingest_sec_adv.py`'s `_KEEP_COLS`/candidate dict; backfilled all 16,869
  existing prospects from the already-downloaded raw archive (no re-download).
- **Wired into `enrich.py`**: if a firm has a CRD, try IAPD person lookup
  first. If a person is found, commit to their guessed email (first.last@,
  flast@, etc.) — even unverified, a named person is more useful for outreach
  than falling back to a generic mailbox. Only fall through to website-scrape/
  generic-guess if no person was identified at all.
- **Real throughput cost**: IAPD lookup roughly doubles per-firm time (~3.5s ->
  ~8s effective with 8 workers) since it adds a JSON API call + PDF download +
  parse. On a 15-firm real-data test, 9/15 firms had a LinkedIn/Instagram/
  Twitter URL listed as their "website" in the SEC filing itself (correctly
  skipped — nothing to guess against), and 2 of the remaining 6 got a real
  personalized IAPD-sourced email (`avital.pardo@pagaya.com`,
  `neel.parekh@nightsquared.com`) — consistent with the earlier 2/3 hit rate.
- Dashboard table now shows Contact/Title/Verified columns.

### Session 5 (2026-07-11) — Website recovery for LinkedIn-listed firms
User asked what to do about the firms whose ADV "website" field is a LinkedIn/
Instagram/Twitter URL (skipped entirely in Session 4, ~60% of one real test
batch). Tested before building: does the brochure PDF (already fetched for
named-person lookup) reveal the firm's *real* domain on its cover page? **2/2
real firms confirmed yes** — OnyxPoint Global Management (ADV listed LinkedIn)
-> brochure cover page has `onyxpointglobal.com` and a direct email
`compliance@opglp.com`; Tishman Capital Partners (ADV listed LinkedIn) ->
brochure has `tishmancapitalpartners.com`. Free, since the brochure is already
being downloaded for the person lookup — just parsing text already in hand.
- **Restructured `iapd.py`**: `find_senior_person()` replaced by
  `lookup_brochure(crd)`, which fetches the brochure PDF once and returns both
  the named person AND `extract_firm_contact()`'s recovered website/direct
  email from the first ~1500 chars (cover page), excluding SEC/FINRA/IARD
  boilerplate domains that show up in the disclaimer text.
- **`enrich.py` priority ladder, in order**: (1) verified personal-guess email
  for a named person, (2) email directly disclosed on the brochure cover page
  (verified or not — self-disclosed by the firm, so trustworthy either way),
  (3) unverified personal-guess, (4) website scrape, (5) generic pattern-guess.
  When the ADV website is missing/social-media-only and a CRD exists, the
  brochure-recovered domain is used for all of steps 3-5 too, not just the
  direct email.
- **Added `website_source` to the schema** (`'SEC_ADV'` vs `'ADV_brochure'`)
  so provenance is visible instead of silently overwriting the original field.
- **Verified end-to-end on the two known LinkedIn firms**: Tishman -> website
  recovered + `ir@tishman.com` found via scraping the recovered site (no named
  person on this one). OnyxPoint -> website recovered + `compliance@opglp.com`
  used directly (`email_source='iapd_brochure_direct'`). Both previously
  landed in "no usable website" — now both have real, usable contact info.
- Dashboard: added a clickable **Website** column (`LinkColumn`, shows just
  the domain); enrichment caption updated to describe the recovery step; no
  new buttons — reuses the existing scoped "🔎 Enrich N prospects" flow.

### Session 6 (2026-07-11) — Full NYC pass, multi-LLM, LLM dedup, Excel export
User asked for: (1) a full enrichment pass on all NYC prospects, (2) the rest
of Phase 2 (multi-LLM routing, LLM dedup — free verification was already
done), (3) enrichment tied to the State+AUM filter flow already in place, plus
a dated Excel export.

**Concurrency reality check before committing to a plan.** 1,986 NYC-area
prospects (`hq_state='NY'` and `hq_city` containing "NEW YORK") needed
enrichment. Measured real throughput at increasing worker counts instead of
guessing: 8 workers -> ~0.126 items/s; 25 workers -> ~0.155 items/s (modest
gain); **50 workers -> ~0.070 items/s, actually slower**. More of our own
concurrency stopped helping and then hurt — the bottleneck is external
(SMTP/IAPD server-side latency and likely throttling under burst load), not
fixable by adding workers. Settled on 20 workers as the real sweet spot.
Honest ETA for the full NYC batch: **~3.5-4.5 hours**. Rather than block the
session, launched `run_nyc_enrichment.py` as a truly OS-detached background
process (`nohup ... & disown`, not tied to the tool's own process tracking, so
it survives independent of this conversation) and moved on to Phase 2 work
while it ran. Progress/ETA logged to `nyc_enrichment_log.txt`.

**Multi-LLM routing (`llm.py`)**: Groq (`llama-3.3-70b-versatile`, cloud,
primary) -> local Ollama (`llama3.2:3b`, already pulled) fallback, same
pattern as career-monitor. Copied the existing `.groq_key` over (same user's
own free account). Verified both paths work, including JSON mode (used for
dedup). One false alarm during testing: the fallback path failed once with a
30s timeout — turned out to be Ollama's cold-start model-load time (~17s)
competing with the NYC enrichment job's CPU/network load, not a real bug;
bumped the Ollama timeout to 45s for headroom in real use.

**LLM dedup (`dedup.py`) — found and fixed a real design flaw via testing.**
First version stripped generic business words ("Capital", "Advisors",
"Management", "Partners", "Group") as if they were legal suffixes, to build a
canonical-name grouping. Ran it against the real 16,869-row dataset: 531
"duplicate" groups, but inspection showed things like "Sculptor Capital LP"
grouped with "Sculptor Advisors LLC", and "Vanguard Group Inc" with "Vanguard
Capital Management LLC" — genuinely different SEC-registered entities, not
duplicates. Fixed by restricting the suffix list to actual legal-entity
suffixes only (LLC, Inc, LP, Corp, Ltd, Co, GP...), which cut it to 127 groups
— but spot-testing those still surfaced false positives (e.g. "Goldman Sachs
Asset Management, L.P." vs "...Co., Ltd." look like a match after suffix
stripping, but are almost certainly the US entity and a separately-registered
Asia-Pacific affiliate). **Conclusion: name-string similarity alone is never
safe enough to auto-flag as certain.** Redesigned so canonical-match and
fuzzy-match are both just cheap *candidate generation* — every candidate pair
is adjudicated by the LLM with HQ city/state, AUM, and CRD number as context
before being reported, and defaults to "not a duplicate" if the LLM call
fails. Verified both directions: correctly said "different" for the Goldman
Sachs pair and for "Eagle Capital Management Llc" vs "Eagle Capital
Management, Llc" (checked the real data — different CRDs, NYC vs. Mandeville
LA, $35.8B vs $455M AUM: genuinely different firms despite the near-identical
name); correctly said "same" for a constructed identical-CRD/HQ/AUM test case.
1,072 candidate pairs generated from the full dataset; full scan launched the
same way as the NYC job (`run_dedup_scan.py`, detached, ~40min ETA, writes
`dedup_results.json`). Zero confirmed duplicates in the first 200/1072 —
plausible and not obviously wrong, since SEC ADV data is inherently
deduplicated by CRD already; most "duplicates" surfaced by name similarity so
far have turned out to be real distinct registrants.

**Excel export (`excel_export.py`)**: exports whatever's currently filtered
(same State/AUM/City filters as enrichment) to `exports/prospects_<location>_
<AUM-band>_<date>.xlsx` — e.g. `prospects_NEW-YORK_AUM5M-500M_2026-07-11.xlsx`.
Column-width autofit via openpyxl. Wired as a button right after the
enrichment section in the dashboard, so the intended flow (filter by
State+AUM -> enrich -> export) is now literally top-to-bottom on the page.

**Dashboard additions**: "💾 Export to Excel" section (filename preview shown
before clicking) and a "🔁 Possible duplicates" review section that reads
`dedup_results.json` — shows each LLM-confirmed pair with its reasoning and
Merge/Not-a-dupe buttons (`dedup.merge_prospects()` fills blank fields on the
kept record from the removed one, never overwrites existing data). No
in-dashboard "scan now" trigger built yet — review section reads whatever the
standalone script last produced. Full dashboard re-verified end-to-end with
all sections present, no errors.

**Status at end of session**: both `run_nyc_enrichment.py` and
`run_dedup_scan.py` still running in the background (detached, will keep
running after this session ends) — check `nyc_enrichment_log.txt` /
`dedup_scan_log.txt` for progress on return.

### Session 7 (2026-07-11) — LLM on/off toggle
User asked where the LLM actually fits in, since it wasn't obvious it's
*only* used by `dedup.py` — `enrich.py` (email discovery/verification) is
entirely regex/rule-based, no LLM involved at all, so there's no overlap or
redundancy between the two. Also raised a real handoff concern: Mayank may
not have Ollama set up or be comfortable with it yet (though Ollama is only
the fallback — Groq, cloud + API key, is primary).
- **Added `settings.py`**: tiny persisted JSON settings file
  (`dashboard_settings.json`, gitignored) for dashboard-level feature
  toggles — currently just `llm_enabled` (default `False`).
- **Sidebar toggle**: "🤖 Enable LLM-powered duplicate detection" — persists
  across restarts. When off, the "🔁 Possible duplicates" section is hidden
  entirely from the dashboard (not just visually collapsed).
- Nothing else changed behavior-wise — the standalone `run_dedup_scan.py`
  script is unaffected by this toggle (it's for Kartavya's own use, not
  Mayank's dashboard). When Mayank is ready, he flips the toggle on and can
  start using duplicate review; if a cold-email-generation feature gets built
  later (Track 1 outreach, still not started), it should gate on this same
  toggle rather than introducing a second one.

### Session 8 (2026-07-11) — Broadened scope to NY state, deregistration + incremental dedup
User confirmed a preference: state-level filtering over city-level. The
running enrichment job was scoped to NYC city only (`hq_city` containing
"NEW YORK"), narrower than the state — asked whether to broaden it.
- **Stopped and relaunched the enrichment job scoped to all of NY state**
  (`run_ny_state_enrichment.py`, replacing `run_nyc_enrichment.py`). No work
  lost: `enrich_prospects()` already skips prospects with an email set, so
  the ~400+ already-enriched NYC firms were automatically skipped; the new
  job picked up the remaining NYC firms plus the ~653 other-NY-state firms
  (2,145 total remaining). Verified via `wmic` that only the correct PID was
  killed, leaving the dedup scan process untouched.

**Two real gaps identified and built, after confirming where data lives**
(`prospects.db`, persists indefinitely — nothing is recomputed on dashboard
load) and validating the user's instinct that ingest+enrichment are already
incremental (`bulk_add_prospects` only inserts new firms; `enrich_prospects`
only touches prospects with no email yet):

1. **Deregistration detection** (`ingest_sec_adv.get_current_crds()` +
   `detect_deregistered()`): on each SEC ADV refresh, compares the new
   bulk file's CRDs against `prospects.db`'s active SEC_ADV-sourced
   prospects; anything missing gets flagged via a new `deregistered_at`
   column (never deleted — CRM status/history is preserved). Wired into the
   sidebar's "🔄 Refresh SEC ADV cache" button, shows a warning banner with
   names if any are found. Dashboard gained a "Show N deregistered firm(s)"
   checkbox (default off — they're hidden from the main table/filters unless
   explicitly requested). **Found a real bug while testing**: the working
   cache (`data/sec_adv_cache.csv`) on disk predated this session's addition
   of `Organization CRD#` to `_KEEP_COLS`, so `get_current_crds()` threw a
   `KeyError` immediately — the cache had just never been regenerated since
   that column was added. Fixed by re-running `refresh_adv_cache()`. Verified
   both directions after the fix: zero false positives against an unchanged
   file, and a simulated single-CRD removal correctly flagged exactly that
   one firm.
2. **Persisted/incremental dedup** (`db.dedup_verdicts` table): every
   adjudication — both "same" and "different" — is now recorded via
   `db.record_dedup_verdict()`, keyed by a stable sorted `pair_key`. New
   `dedup.find_new_candidate_pairs()` filters out anything already
   adjudicated. `run_dedup_scan.py` rewritten to use this; the dashboard's
   "🔁 Possible duplicates" section now reads `db.get_confirmed_duplicates()`
   directly instead of a JSON file — `dedup_results.json` is retired
   entirely. Verified live against the actually-running scan: candidate
   count dropped from 1,072 to exactly 1,047 after 25 verdicts were
   persisted, confirming the skip-logic works correctly, not just in theory.
   The old (pre-incremental) dedup scan was only 500/1072 through with zero
   confirmed duplicates — killed rather than migrated, since its results
   weren't persisted anywhere the new system reads from, and restarting cost
   was low.
3. Renamed `_pair_key` to public `pair_key` in `db.py` since `dedup.py` now
   calls it externally — a `sed` rename mid-session mangled
   `get_adjudicated_pair_keys` into `get_adjudicatedpair_keys` (substring
   collision), caught and fixed immediately via grep.

Both background jobs (enrichment, dedup) confirmed still running correctly
after all schema/logic changes — SQLite handled the concurrent schema
migration fine. Full dashboard re-verified end-to-end, no errors.

### Session 9 (2026-07-11) — Named-contact hit rate: what worked, what didn't
Real data check: of ~2,000 enriched prospects, only 18% (361) got a specific
named senior person's email — the rest (82%) got a real, verified, but
generic mailbox (mostly `compliance@`/`info@` disclosed on SEC brochures).
User asked how to raise that, specifically floating automated web search.

**Tested and rejected: automated web search for named executives.**
- My own agent WebSearch tool found a real name easily ("Seahorse Financial
  Advisers" -> "Edvard Jorgensen, President since 1993, also CCO") — but that
  tool isn't callable from `enrich.py`'s own code, only from an active Claude
  Code session. Had to build a programmatic equivalent.
- Adapted career-monitor's proven DDG->Bing HTML-scraping pattern
  (`company_discovery.py`) into a new `websearch.py`. **Zero results** — DDG
  returned a 202 bot-challenge redirect, Bing returned 200 but with no
  `.b_algo` result markup at all (JS-shell page). Confirmed this wasn't a bug
  in the adaptation: ran career-monitor's own unmodified `search_web()`
  against the same query — also zero results. Both engines have tightened
  anti-bot measures since that code was last verified working.
- User asked to try Google instead — tested directly: 200 status but a
  `<noscript>` JS-redirect shell, no real content. Same dead end.
- User then asked to try Playwright (real headless browser rendering) before
  giving up. Tested against Bing: got a literal **CAPTCHA challenge page**
  ("Please solve the challenge below to continue") even with full browser
  rendering. Tried DuckDuckGo via Playwright too: a "please email us" error
  page. All three engines block automated requests even through a real
  browser — the next step up would be a paid CAPTCHA-solving service or a
  paid search API (Google Custom Search: 100 free queries/day, then $5/1000),
  which is out of scope. **`websearch.py` was deleted — not shipped, since
  it would silently return nothing for every firm forever.**

**Built the two things that actually work, no fragile dependency:**
1. **`enrich.infer_name_from_email()`** — a scraped/guessed email's
   local-part sometimes already encodes a name: `firstname.lastname@` is
   self-evident, or a local-part ending in the firm's own likely-founder
   surname (e.g. `eeagan@` at "Eagan Capital Management" -> "E. Eagan").
   Verified against 6 real cases (4 real positives, 2 correct rejections of
   generic addresses like `info@`/`compliance@`). Free — no network call, so
   backfilled immediately against all already-enriched records: **162
   additional named contacts recovered for free** out of 1,944 candidates
   that had an email but no name.
2. **`linkedin_url.py`** — builds a plain LinkedIn people-search URL (firm +
   target titles: CIO, Managing Director, Portfolio Manager, President,
   Founder, Managing Partner). Not scraping — just constructs a URL Mayank
   clicks and views in his own logged-in browser, same pattern already
   proven safe in career-monitor's `networking_utils.py`. Backfilled onto
   all 16,869 prospects (free, no network call). Wired into `enrich_prospect()`
   so every future enrichment gets one too, and added as a clickable
   "🔍 Search" column in the dashboard table. This is the honest ceiling for
   automation — when neither the SEC brochure lookup nor the email-inference
   heuristic finds a name, this gives Mayank a one-click manual path, which
   is exactly what he asked for as the fallback ("manual input from LinkedIn").

**NY-state enrichment job finished during this session**: 1,968 enriched
(851 SMTP-verified) out of 2,145, 177 with no findable email. Dedup scan
still running in the background.

### Session 10 (2026-07-11) — LinkedIn URL bug, dashboard cleanup
User launched the dashboard and reported the "🔍 Search" LinkedIn link wasn't
returning results. Found two real bugs in `linkedin_url.py`:
1. The full **legal** firm name (e.g. "Seahorse Financial Advisers Inc.") was
   wrapped in exact-phrase quotes — but almost no LinkedIn profile/company
   page includes the legal suffix in free text, so the exact match essentially
   never hit. Combined with a nested OR/parentheses boolean expression
   (unreliable on LinkedIn's standard, non-Sales-Navigator search), the query
   was over-constrained to nothing.
2. Fixed by stripping legal suffixes before searching — but the first suffix
   regex had its own bug: alternatives like `L\.P\.` consumed their own
   trailing period, leaving a required `\b` (word boundary) to anchor right
   after a non-word character, which never matches (periods aren't word
   characters) — so anything with a literal trailing period, like "L.P.",
   silently failed to strip. Fixed by moving the trailing period out of each
   alternative into one shared optional `\.?` at the end.
3. Also found (via testing against real firm names, not just the one bug
   report): stripping "Co."/"Company" mangled "Donald Smith & Co., Inc." into
   "Donald Smith &" — a dangling ampersand, since "& Co." is often part of a
   firm's actual identity, not a generic legal suffix. Removed "Co"/"Company"
   from the strippable-suffix list entirely (kept LLC/Inc/Corp/Ltd/LP/GP,
   which are unambiguous).
   Also simplified the title-matching portion — dropped the OR/parentheses
   boolean in favor of plain space-separated keywords with phrase-quotes only
   on genuine multi-word titles (safer, standard search syntax LinkedIn's
   default relevance ranking already handles).
   Regenerated `linkedin_search_url` for all 16,869 prospects with the fix.
   **Not independently verified against a live LinkedIn account** (no login
   access) — the fix is based on known search-syntax best practices and
   tested string output, but worth Mayank confirming it actually surfaces
   relevant people once he's using it.

**Dashboard UI simplified per request:**
- Removed the City and Status filter dropdowns (State + Prospect Type remain).
  City and Status stay visible as table columns, just not filterable via a
  dropdown.
- AUM is now shown in human-readable M/B/T form everywhere — the filter
  slider caption, the main table, and the Excel export's AUM column — via a
  new shared `formatting.format_aum()` (e.g. `$498.6M`, `$1.2B`, `$11.1T` for
  the handful of trillion-AUM mega-managers like Vanguard still in the
  unfiltered data). Added a trillion tier beyond what was literally asked
  (Million/Billion) since a few real records in this dataset are AUM
  $1T+ and would otherwise render as an unreadable 5-digit billion figure.

### Session 11 (2026-07-11) — AUM slider labels, LinkedIn root cause, all-states sweep
- **AUM slider now shows M/B/T on the control itself**, not just a caption
  below it. `st.slider` has no built-in SI-suffix formatting, so switched to
  `st.select_slider` with a new `formatting.aum_breakpoints()` (log-spaced —
  linear would put almost every real prospect in the first sliver of the
  track, since AUM here spans $2 to $11.1T) and `format_func=format_aum`.
- **LinkedIn — found the actual root cause, not just query syntax.** User
  reported still no results after the Session 10 fix. Tested the URL's raw
  HTTP behavior directly: LinkedIn redirects **every unauthenticated search**
  to `/uas/login?session_redirect=...` — confirmed the query string survives
  intact inside that redirect, so this looked at first like just "log in and
  it'll work." User confirmed they *were* already logged into LinkedIn in
  that browser and still got nothing — ruling that out. Root cause was
  actually a Session 10 overcorrection: removed exact-phrase quoting
  entirely when fixing the "quoted legal name never matches" bug, but
  career-monitor's own **proven, actively-used** `linkedin_search_url()`
  quotes the company name — just not a legal-suffix-laden one. Fixed by
  quoting the *cleaned* (suffix-stripped) name instead of dropping quotes
  altogether. Also simplified the title list to exactly what was requested:
  `Director President "Vice President"` (was CIO/Chief Investment Officer/
  Managing Director/Portfolio Manager/Founder/Managing Partner). Regenerated
  for all 16,869 prospects. **Still not independently verified against a
  live account** — next test result from Mayank/Kartavya will confirm
  whether this is right or needs another iteration.
- **Launched a full all-remaining-states enrichment sweep**
  (`run_all_states_enrichment.py`) — one-time job per user's framing. 14,317
  prospects across 52 other states/territories + blank-state records,
  processed **one state at a time, biggest first** (CA, TX, FL, IL, MA...),
  rather than all-at-once, since Session 8 found more concurrency actively
  hurts throughput past ~20-25 workers — better to give each state full
  throughput sequentially. Resilient: each state wrapped in try/except so
  one failure doesn't kill a job expected to run roughly a day; safe to
  restart anytime since `enrich_prospects()` already skips completed work.
  Progress in `all_states_enrichment_log.txt`.

### Session 12 (2026-07-11) — Real data-quality bug in named-contact extraction
User asked whether we'd only found Chief Compliance Officers and nothing
else. Checked real numbers rather than guess: **113/618 (18%) of named
contacts were CCOs** — the single largest group, but not exclusive (also
33 Presidents, 22 Founders, 16 CEOs, 6 Portfolio Managers, 5 CIOs). CCO
dominance makes sense structurally — Form ADV brochures have a
near-mandatory, standardized CCO-contact disclosure, while other titles
(CIO, President, founder) only appear when a firm happens to discuss
key-person risk or broker-dealer overlap, which is optional/circumstantial.

**But that check surfaced a real, serious bug: ~70+ "titles" were garbage**
("Selection and", "and Chief", "of the", "as applicable" — more entries than
the legitimate CCO count). Root cause, confirmed against real brochure text
(not theorized): `iapd.py`'s `_NAME_PATTERNS` were all compiled with
`re.IGNORECASE`, which makes `[A-Z]` also match lowercase letters — silently
defeating the entire point of `_NAME`'s capitalization check (how it tells a
real proper noun apart from ordinary text). Concrete example: the source
text was a **table-of-contents line** — "Item 6: Portfolio Manager Selection
and Evaluation .... Page 8" — and the pattern matched "Portfolio Manager" as
a title and "Selection and" as if it were a name. Compounding bug: the
post-match disambiguation logic (guessing which captured group was the name
vs. the title, by checking which one *looked* name-shaped) broke down
whenever a title like "Portfolio Manager" or "Founding Partner" happened to
also be two-capitalized-words — indistinguishable from a real name by shape
alone, so "Portfolio Manager" itself got misidentified as the "name".
**Fixed both**: removed the blanket `re.I`, scoped case-insensitivity to only
the title/connector-word portions via inline `(?i:...)`, and replaced the
shape-guessing with explicit named capture groups (`(?P<name>...)`,
`(?P<title>...)`) so there's no more ambiguity about which group is which.
Verified the fix rejects the exact bug case and still correctly extracts the
two previously-known-good cases (Kahn Brothers -> Thomas Kahn, Lion Point ->
Didric Cederholm / Chief Investment Officer).

**Remediation** (precise, not a blind reset): 219 records with
`email_source='iapd_brochure_pattern_guess'` were unambiguously bug-affected
(the guessed *email* was derived from the corrupted name, so both were
wrong) — fully reset and requeued for re-enrichment. A separate 395 records
had `email_source='iapd_brochure_direct'` with a name attached — ambiguous,
since a manual backfill run earlier in this session (`infer_name_from_email`,
an unrelated and correct heuristic) could have supplied that name instead of
the buggy path, and that email doesn't depend on the name at all. Rather than
guess, wrote `_remediate_names.py` to re-fetch each brochure and re-run the
*fixed* extraction, updating the name only if it changed (clearing it if the
fixed code no longer finds anyone) — preserves already-good data, corrects
only what's actually wrong. Launched concurrently with a small NY-specific
catch-up enrichment pass (the all-states sweep explicitly skips NY, already
complete from Session 8) for the reset records. Running with the existing
all-states sweep — real resource contention from 3 simultaneous jobs, throughput
dropped, but correctness mattered more than speed for a one-time fix.

**LinkedIn — found the actual, actual root cause (third round).** User
diagnosed it precisely: LinkedIn's keyword search treats space-separated
terms as an implicit AND. Session 11's fix removed explicit "OR" between
title alternatives, assuming that would just be relevance-ranked — but
packing 7 mutually-exclusive titles as plain AND'd keywords required a
single profile to match all seven simultaneously, which is why every
attempt failed regardless of quoting. Fixed by restoring `" OR "` between
title alternatives inside parentheses, kept the legal-suffix-stripped
company name (that part of the diagnosis was still correct) outside as an
implicit AND against the OR-group — standard boolean search convention.
Regenerated for all 16,869 prospects. This is now the third LinkedIn fix
this session — still genuinely unverified against a live account each time;
next real test result should be treated as the actual signal, not further
reasoning from first principles.

### Session 13 (2026-07-11) — LinkedIn simplified, missed-name gap closed
User reported the OR-fixed LinkedIn query was *still* erroring, and directed
a much simpler design: firm name + registered-location filter only, no title
keywords at all. Rebuilt `linkedin_url.py` around that — dropped every title
keyword (the repeated point of failure across 3 rounds), kept the
legal-suffix-stripped firm name, added a `hq_state` parameter using
LinkedIn's structured `geoUrn` filter for the handful of states with a
verified ID (reused from career-monitor's own proven `GEO_URNS`), falling
back to appending the state as plain keyword text for the other 43 —
deliberately not guessing unverified geoUrn IDs, since guessing is what
caused the earlier silent failures. `build_search_url()`/`build_person_url()`
now both take `hq_state`; `enrich_prospect()` passes it through. Regenerated
for all 16,869 prospects.

**Found and closed a real coverage gap** in named-contact detection: user
noticed some emails clearly encode a name but `contact_name` stayed blank.
Checked directly — 142 real cases (e.g. `shilpi.mcgrath@forgeglobal.com` ->
should be "Shilpi Mcgrath"). Root cause: `infer_name_from_email()` was only
ever called on the website-scrape/generic-guess fallback path in
`enrich_prospect()` — never on the `iapd_brochure_direct` path (a directly-
disclosed email from the brochure cover page, independent of whether a named
person was also found). Fixed by applying the same inference to that path
too (only when no IAPD-sourced person/title was already found, so a real
extracted name is never overwritten by a guess). Backfilled immediately
across the whole db (free, no network call) — 142 additional names recovered.

### Session 14 (2026-07-11) — Re-verify button, clarified two things
User asked what "Find emails for this filtered list" actually does (answer:
only touches prospects with no email yet — anyone with an email, verified or
not, is skipped, at both the UI-filter level and inside
`enrich_prospects()` itself) and what "SMTP-verified" means (answer: a free
MX-lookup + SMTP `HELO`/`MAIL FROM`/`RCPT TO` handshake that stops before
actually sending anything — confirms deliverability without a paid tool;
"unverified" means the mail server didn't answer either way, not that the
address is wrong).

Built the requested follow-up: **`enrich.reverify_emails()`** + a new
"🔁 Re-verify unconfirmed emails" section in the dashboard, scoped to the
current filter like everything else. Deliberately narrow scope, explained
up front: this ONLY re-runs the free verification check against the SAME
email already on file — no rediscovery, no scraping, no IAPD lookup. Mail
servers can be temporarily unreachable or rate-limit a first attempt, so a
retry can succeed where the original check didn't. Skips prospects with no
email or one already marked verified. Tested against 10 real unverified
records — ran correctly (0/10 newly verified that round, which is expected;
most non-responsive servers stay non-responsive, this isn't a guaranteed fix,
just another chance).

### Session 15 (2026-07-11) — Retry action for genuinely-failed companies
User asked what happens to no-email companies on repeat clicks of "Find
emails." Checked real numbers: 213 prospects had `status='Enriched'` +
`email=NULL` — meaning the pipeline already ran, checked their brochure,
scraped their site, and genuinely found nothing. Confirmed via the code that
the main button's filter (`email IS NULL`) doesn't distinguish these from the
13,918 never-touched prospects — both have a null email, so both get
silently redone on every click, wasting a full discovery pass on known
failures every time.

Fixed by splitting into two explicit sections:
- **Main "📧 Find emails" button** now filters to `email IS NULL AND
  status = 'New'` only — never touches previously-tried-and-failed companies.
- **New "♻️ Retry companies with no email found" section** — filters to
  `email IS NULL AND status = 'Enriched'`, its own button, same underlying
  `enrich.enrich_prospects()` call (no backend change needed, just a
  different id selection) for an explicit, on-purpose retry — worth doing
  since a firm's website or filing may have changed since the last attempt.
Verified the two filters are cleanly non-overlapping against real data
before shipping.

### Session 16 (2026-07-11) — Generic/garbage contact names, third real bug found
User flagged generic names in the contact list and asked to clean them up or
fill in real ones, plus noted "Find this person" links were missing even
when a name was shown. Investigated both with real data before fixing.

**Two genuinely distinct bugs found in `iapd.py`, on top of Session 12's
case-sensitivity fix:**
1. **PDF line-wrap newlines embedded in names** — "Anne\nCampbell",
   "Aryeh\nB. Bourkoff", etc. `_NAME`'s `\s` (needed to span a line-wrapped
   name across two PDF lines) was capturing the literal newline character
   into the stored name. Fixed by collapsing whitespace runs in the captured
   name/title to a single space.
2. **Pattern 3 ("Title, Name") had no requirement that the title actually
   referred to the firm's own person** — reproduced live against the actual
   source text for two different real bugs: (a) "...accounts are reviewed by
   the Managing Director, **Chief Compliance** Officer, Portfolio Manager
   or..." — a list of role titles, not a named person; the pattern grabbed
   the first two words of the *second* title as if it were a name. (b) far
   worse: "...sanctions...including Russian Federation President **Vladimir
   Putin**..." — a real name, but from an unrelated sanctions/risk-disclosure
   section, not the firm's leadership at all. Confirmed 2 more of this same
   shape via firm-context checking: "Donald Trump" at Stabilis Capital
   Management (implausible) and "Ray Dalio" at Omen Advisory (implausible,
   Dalio actually founded the unrelated Bridgewater). **Not everything
   flagged this way was wrong, though** — checked "Anthony Scaramucci" at
   Skybridge Capital II (he really did found SkyBridge — correct) and "David
   Einhorn" at DME Advisors (a real Einhorn-affiliated entity — correct), so
   this was a firm-by-firm plausibility check, not a blanket purge of famous
   names. Fixed pattern 3 by requiring "our"/"the firm's" immediately before
   the title (matching the safety already built into pattern 2) — verified
   against 7 regression cases (both new false-positive texts now correctly
   rejected, all previously-good extractions and the newline fix still work,
   and a legitimate "our President, Name" construction still matches).
3. **Added a deterministic jargon blocklist** (`_looks_like_real_name()`) —
   catches company names ("Deutsche Bank"), departments ("Investor
   Relations"), and bare titles ("Managing Director") that structurally look
   like two-capitalized-word names but obviously aren't. Wired inline so
   `extract_senior_person()` skips to the next pattern instead of trusting a
   blocklisted match — prevents this class of garbage going forward, not
   just a one-time cleanup.

**Cleanup applied to existing data**: normalized whitespace on names with
embedded newlines (12 fixed in place, not cleared — they were real names),
cleared 148 jargon/garbage names via the blocklist, manually cleared the 3
confirmed false-positive famous names (Trump, Putin, Dalio) after checking
firm context. Net: 618 -> 283 named contacts — a real drop, but every
remaining one has actually been checked against a concrete failure mode
rather than assumed correct.

**Also closed the "Find this person" link gap** (49 records had a name but
no LinkedIn person-search URL — stale from before that field existed on
their code path) via one final full regenerate of both `linkedin_search_url`
and `linkedin_profile_url` across all 16,869 prospects, now that names are
cleaned up. Gap confirmed at 0 afterward.
