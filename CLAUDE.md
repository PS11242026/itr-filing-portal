# CLAUDE.md — ITR Filing Portal

Streamlit 1.59 + SQLite portal for individual ITR prep/filing
(AY 2026-27, ITR-1/2). Windows; venv at `.venv` (use
`.venv\Scripts\python`, NOT system python). Tests: `python -m pytest`
(pytest.ini sets pythonpath).

## Hard rules

- `itrcore/` must NEVER import streamlit (test-enforced in
  `tests/unit/test_enforcement.py`). Streamlit code lives in `itrapp/`
  and `app_pages/`.
- Every repo function's first param is `scope: TaxpayerScope` or
  `principal: Principal` — never a raw taxpayer_id (introspection test).
  Scopes come only from `itrcore.authz.mint_scope`.
- Every page in `app_pages/` calls `guard.page(...)` BEFORE importing
  streamlit (regex lint test).
- Money: integer paise in ledgers/ingestion; whole rupees (int) inside
  `itrcore/tax`. Never float.
- Statutory numbers live in `itrcore/config/ay_2026_27.yaml` — no rate
  or limit literals in engine code. New AY = new YAML + golden re-run.
- Schema changes = new numbered file in `itrcore/db/migrations/`
  (never edit 0001), applied by `migrate.upgrade()` on boot.
- ITR JSON key-name fixes for official schema versions go in
  `itrcore/itr/quirks.py`, not in the builders.
- PII (PAN/Aadhaar/account numbers) is Fernet-encrypted with HMAC blind
  indexes; documents encrypted on disk. Keys:
  `%APPDATA%\itr-portal\keys.json` (`ITR_PORTAL_KEY_FILE` in tests).
- Audit log is append-only and hash-chained; add `auditlog.log(...)`
  to every new state-changing repo function.

## Conventions

- Tests use `fresh_db` fixture (`ITR_PORTAL_DATA_DIR` → tmp).
- `st.data_editor` keys must include taxpayer_id + AY.
- Terminal labels for transactions are in
  `itrcore/db/repos/transactions.py::TERMINAL_LABELS`.
- The readiness gate (`itrcore/analysis/gate.py`) is the single
  authority on what blocks filing.
- `itrcore/tax/setoff.py` is the single authority on losses (Sec 70–74):
  no head module may set a loss off against another head, and brought-
  forward losses are applied only there. Head modules return their own
  income in isolation.
- The ₹1.25L in Sec 112A(2) is a RATE concession, not an exclusion from
  total income — apply it in the tax stage, never in `capital_gains.py`.
- `tests/golden/cases/case_*.yaml` calibrate the engine against ten real
  practitioner-software computations (AY 2026-27; the source PDFs live in
  `ITR filing data/changes`, which is gitignored). They must match to the
  rupee; if one breaks, the engine is wrong until proven otherwise. The
  cases are ANONYMISED — identity, DOB, address, employer/bank names and
  challan numbers are neutral stand-ins, and the DOB stand-in preserves
  the senior-citizen age band. Keep them that way: the repo is public.
- Advance tax / self-assessment tax come ONLY from **AIS Part B3**. The
  post-AY-2023-24 TRACES 26AS (Parts I–X) has no tax-paid part at all —
  its Part VII says "refer AIS". B3 is a flat `columnLabel`/`columnData`
  table with no `l2`, so the main row walk must skip it explicitly.
- A challan is reported in the AIS of the year it was PAID but discharges
  the year its own `Financial Year` column names. Match on that, never on
  the deposit date, and scan every stored AY — self-assessment tax for
  AY X paid in July appears only in the AY X+1 AIS.
