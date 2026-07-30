# ITR Filing Portal

Internal + client-facing portal for individual income-tax return
preparation and filing (AY 2026-27, **ITR-1/2/3/4** — salary, house
property, capital gains, presumptive & regular-books business income, other
sources, exempt income/Schedule EI, and clubbing), built on Streamlit +
SQLite with a pure-Python core (`itrcore/`).

## Setup on a fresh machine

```bat
setup_portal.bat      &rem creates .venv + installs requirements.txt
run_portal.bat        &rem launches http://localhost:8511
```

or manually: `python -m venv .venv`, `.venv\Scripts\python -m pip install
-r requirements.txt`, then `.venv\Scripts\python -m streamlit run app.py`.
First launch shows a one-time Partner (admin) bootstrap. Optional demo
data: `.venv\Scripts\python scripts\seed_demo.py` (empty database only).

Tests: `.venv\Scripts\python -m pytest` (332 tests, ~40 s; dev extras in
`requirements-dev.txt`). Lint: `.venv\Scripts\python -m ruff check .`

## Filing-season SOP (per client)

1. **Onboarding** — create the taxpayer (PAN validated, encrypted at
   rest), tick the income profile, hand the client their one-time login.
   Everything remains editable later on the **Clients** page (contact,
   PAN/Aadhaar identity fixes, profile, deactivate/reactivate).
2. **Documents** — client uploads against the checklist (or staff upload
   on their behalf). Waivers are recorded and audited.
3. **Data import** — bank statements (CSV/XLSX/PDF; unknown layouts via
   the manual column mapper, which remembers the bank for next time),
   **AIS/TIS** (the portal's encrypted JSON is decrypted locally and
   classified Genius-style B1–B7 with the Excel workbook export), **Form
   26AS** (TRACES Annual Tax Statement PDF → parsed + full formatted PDF
   re-export), Form 16 quick entry. Or **pull AIS/TIS/26AS straight from
   the ITD portal** (browser-driven, live-calibrated: PAN + stored
   password pre-filled, the human does only CAPTCHA/OTP; e-filing
   password reset opens the Forgot-Password flow the same way).
4. **Review transactions** — Run analysis (salary/rent series,
   self-transfers, reversals, noise, cash deposits, gift-like credits →
   client queries per Sec 56(2)(x)). Client answers on their portal;
   staff resolve and bulk-confirm final categories.
5. **Computation** — **Auto-fill** merges imports around manual entries
   (never discarding them): salary from Form 16, else employer-wise from
   26AS section-192 amount-paid, else the AIS aggregate; TDS from 26AS;
   interest/dividend from AIS/TIS; brought-forward losses from last
   year's final. Capital gains import from the **standardized Excel
   template** (auto-classified ST/LT into 111A/112A/112 buckets). Exempt
   income (Schedule EI) has its own tab; net agricultural income above
   the threshold triggers partial integration in both regimes. Every
   line item is editable/deletable in place and reflects in the compute.
   **Compute** cross-checks the inputs against imported AIS/TIS/26AS and
   pops anything missing before running. Save a snapshot; Manager marks
   it **final**; export the practitioner-format Excel (slab-band tax
   ladder, section-wise TDS), PDF or HTML sheet.
6. **Reconciliation** — TDS vs 26AS (claiming more than 26AS blocks;
   unclaimed 26AS asks "unreported income?"), AIS vs books, and **AIS/TIS
   vs 26AS** cross-check (differences → client queries), with
   accept/justify/edit resolutions. Unresolved items block filing.
7. **ITR generation & filing** — eligibility (ITR-1/2/3/4) with reasons;
   the page itself offers snapshot-saving and finalisation if pending →
   validated JSON (jsonschema + business rules + round-trip) → download
   for manual filing **or 🚀 browser-driven upload** (logs in, navigates
   File-ITR, attaches the JSON, then STOPS — you review, Submit and
   e-verify personally), then record the acknowledgement in the tracker
   (30-day e-verify reminder shown).

## Critical operational notes

- **Back up two things**: the database (Admin → Backups, or
  `data/backups/`) **and the key file**
  `%APPDATA%\itr-portal\keys.json`. Losing the key file makes encrypted
  PAN/Aadhaar/account numbers and all stored documents unrecoverable.
- `data/`, `data_backup_*/` and `ITR filing data/` hold client PII —
  git-ignored; keep them off shared drives.
- **Restarting after code changes**: OneDrive breaks Streamlit
  hot-reload AND Windows lets several servers bind port 8511 at once
  (stale ones keep serving old code). `run_portal.bat` kills all 8511
  listeners before launching — always restart through it.
- **Official ITR schemas**: `schemas/itr/ay2026_27/` currently holds
  portal-derived placeholders. Follow `MANIFEST.md` there to drop in the
  official ITD schemas and record hashes; key-name deltas go in
  `itrcore/itr/quirks.py`.
- **Golden calibration**: verify `itrcore/config/ay_2026_27.yaml` seed
  values against the Finance Act 2025 and re-check
  `tests/golden/cases/*.yaml` against the ITD utility
  (see `tests/golden/README.md`), then set
  `verified_against_utility: true`.
- LAN sharing: change `server.address` in `.streamlit/config.toml` to
  the machine's LAN IP; keep the Windows firewall rule office-only.
- Portal-flow changes (menus/TRACES) are self-diagnosing: failures name
  the exact step and dump screenshots+HTML per tab to `%TEMP%\itd_diag`.

## Architecture (short)

- `itrcore/` — pure Python, zero Streamlit imports (test-enforced):
  db + migrations + field encryption + hash-chained audit log, ingestion
  adapters (incl. AIS decryption and the Selenium ITD connector),
  analysis engine (auto-fill merge, pre-compute recon gaps), dual-regime
  tax engine (config-driven; slab-band + special-rate breakdowns),
  reconciliation, ITR builders, exports.
- `itrapp/` — Streamlit glue: auth (scrypt, lockout, forced + self-service
  password change), role-filtered navigation, guards, PII widgets.
- `app_pages/` — staff and client pages; every page starts with
  `guard.page(...)`; every repo call goes through a `TaxpayerScope`
  minted by `itrcore.authz` (clients cannot cross taxpayers — enforced
  at the data layer, not the UI).
- Money is integer paise in ledgers, whole rupees inside the tax engine.
  Statutory numbers live only in `itrcore/config/ay_2026_27.yaml`.

## Backlog (deliberate deferrals)

- Remember-me cookie + server-side sessions (currently: re-login on F5).
- DPAPI wrap for the key file; key rotation runbook.
- DPDP data-subject export / purge tooling.
- Broker/CAMS/KFintech capital-gains statement parsing (CG is the Excel
  template + manual entry + AIS assist today).
- Block-of-assets depreciation auto-calc (regular-books depreciation is an
  input today); fuller ITR-3 schedules beyond the reduced structure.
- ERI/API e-filing (`itrcore/efile/protocol.py`) — the browser-driven
  upload covers filing until ERI credentials exist.
- AY 2027-28: the e-filing menu already shows "View Form 168, Income Tax
  Act 2025" (26AS's successor under the new Act) — the pull matches on
  "26as" and will need a one-line retarget next season.
