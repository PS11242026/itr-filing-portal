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
