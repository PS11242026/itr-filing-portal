-- 0001_init: full v1 schema.
-- Money columns are INTEGER paise unless the name says otherwise.
-- PII columns are Fernet-encrypted BLOBs with deterministic HMAC blind
-- indexes (_bidx) for exact-match lookup.

CREATE TABLE IF NOT EXISTS taxpayers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name TEXT NOT NULL,
  pan_enc BLOB NOT NULL,
  pan_bidx TEXT NOT NULL UNIQUE,
  pan_masked TEXT NOT NULL,
  aadhaar_enc BLOB,
  aadhaar_bidx TEXT,
  dob TEXT,
  email TEXT,
  mobile TEXT,
  address TEXT,
  resident_status TEXT NOT NULL DEFAULT 'Resident'
    CHECK (resident_status IN ('Resident','RNOR','Non-Resident')),
  profile_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_by TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('Staff','Manager','Partner','Client')),
  taxpayer_id INTEGER REFERENCES taxpayers(id),
  active INTEGER NOT NULL DEFAULT 1,
  must_change_pw INTEGER NOT NULL DEFAULT 0,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL,
  CHECK ((role = 'Client') = (taxpayer_id IS NOT NULL))
) STRICT;

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  orig_name TEXT NOT NULL,
  stored_path TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  note TEXT,
  uploaded_by TEXT,
  uploaded_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','replaced','void'))
) STRICT;
CREATE INDEX IF NOT EXISTS idx_documents_tp ON documents(taxpayer_id, ay);

CREATE TABLE IF NOT EXISTS checklist_overrides (
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  item_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('waived','satisfied_override')),
  note TEXT,
  updated_by TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (taxpayer_id, ay, item_key)
) STRICT;

CREATE TABLE IF NOT EXISTS bank_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  bank_name TEXT NOT NULL,
  account_no_enc BLOB NOT NULL,
  account_no_bidx TEXT NOT NULL,
  last4 TEXT NOT NULL,
  ifsc TEXT,
  account_type TEXT NOT NULL DEFAULT 'Savings',
  ownership_share REAL NOT NULL DEFAULT 1.0,
  is_primary INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  UNIQUE (taxpayer_id, account_no_bidx)
) STRICT;

CREATE TABLE IF NOT EXISTS statements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  account_id INTEGER NOT NULL REFERENCES bank_accounts(id),
  doc_id INTEGER REFERENCES documents(id),
  ay TEXT NOT NULL,
  period_from TEXT,
  period_to TEXT,
  adapter_id TEXT,
  rows_imported INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'staged'
    CHECK (status IN ('staged','committed','void')),
  imported_by TEXT,
  imported_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS statement_rows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  statement_id INTEGER NOT NULL REFERENCES statements(id),
  row_no INTEGER NOT NULL,
  raw_json TEXT NOT NULL,
  parsed_json TEXT,
  issues TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  account_id INTEGER NOT NULL REFERENCES bank_accounts(id),
  statement_id INTEGER REFERENCES statements(id),
  ay TEXT NOT NULL,
  txn_date TEXT NOT NULL,
  value_date TEXT,
  narration TEXT NOT NULL DEFAULT '',
  ref_no TEXT,
  amount_paise INTEGER NOT NULL,           -- signed; credit positive
  balance_paise INTEGER,
  fingerprint TEXT NOT NULL,
  mode TEXT,
  counterparty_key TEXT,
  counterparty_display TEXT,
  auto_category TEXT,
  flag_reason TEXT,
  status TEXT NOT NULL DEFAULT 'unclassified'
    CHECK (status IN ('unclassified','auto','query_raised','client_answered',
                      'staff_confirmed','staff_overridden')),
  terminal_label TEXT,                     -- income:<head> | non_income:<reason> | noise
  notes TEXT,
  updated_by TEXT,
  updated_at TEXT,
  UNIQUE (account_id, fingerprint)
) STRICT;
CREATE INDEX IF NOT EXISTS idx_txns_tp ON transactions(taxpayer_id, ay);
CREATE INDEX IF NOT EXISTS idx_txns_status ON transactions(taxpayer_id, ay, status);

CREATE TABLE IF NOT EXISTS explanation_queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  transaction_id INTEGER REFERENCES transactions(id),
  topic TEXT NOT NULL DEFAULT 'transaction',
  question TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','answered','resolved','withdrawn')),
  client_response TEXT,
  response_doc_id INTEGER REFERENCES documents(id),
  answered_at TEXT,
  staff_disposition TEXT,
  resolved_by TEXT,
  resolved_at TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_queries_tp ON explanation_queries(taxpayer_id, ay, status);

CREATE TABLE IF NOT EXISTS property_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('sale','purchase')),
  address TEXT NOT NULL,
  transfer_date TEXT,
  acquisition_date TEXT,
  consideration_paise INTEGER,
  stamp_duty_value_paise INTEGER,
  cost_acquisition_paise INTEGER,
  cost_improvement_json TEXT NOT NULL DEFAULT '[]',
  transfer_expenses_paise INTEGER NOT NULL DEFAULT 0,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS rental_agreements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  property_address TEXT NOT NULL,
  tenant_name TEXT,
  tenant_pan TEXT,
  monthly_rent_paise INTEGER NOT NULL,
  from_date TEXT,
  to_date TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS deduction_proofs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  section TEXT NOT NULL,
  description TEXT,
  amount_paise INTEGER NOT NULL,
  doc_id INTEGER REFERENCES documents(id),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS form16_data (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  employer_name TEXT NOT NULL,
  employer_tan TEXT,
  gross_salary_paise INTEGER NOT NULL DEFAULT 0,
  exempt_allowances_paise INTEGER NOT NULL DEFAULT 0,
  prof_tax_paise INTEGER NOT NULL DEFAULT 0,
  tds_paise INTEGER NOT NULL DEFAULT 0,
  period_from TEXT,
  period_to TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  source_doc_id INTEGER REFERENCES documents(id),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS tds_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '26as'
    CHECK (source IN ('26as','form16','ais','manual')),
  tan TEXT,
  deductor_name TEXT,
  section TEXT,
  quarter TEXT,
  amount_paid_paise INTEGER,
  tds_paise INTEGER NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_tds_tp ON tds_entries(taxpayer_id, ay);

CREATE TABLE IF NOT EXISTS challans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('advance','self_assessment')),
  bsr_code TEXT,
  challan_no TEXT,
  paid_on TEXT NOT NULL,
  amount_paise INTEGER NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS ais_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT,
  amount_paise INTEGER NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  source_file TEXT,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_ais_tp ON ais_entries(taxpayer_id, ay);

CREATE TABLE IF NOT EXISTS recon_resolutions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('tds','ais')),
  item_key TEXT NOT NULL,
  resolution TEXT NOT NULL CHECK (resolution IN ('accepted','justified','edited')),
  remark TEXT,
  resolved_by TEXT,
  resolved_at TEXT NOT NULL,
  UNIQUE (taxpayer_id, ay, kind, item_key)
) STRICT;

CREATE TABLE IF NOT EXISTS computations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  inputs_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  inputs_digest TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','final','superseded')),
  created_by TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (taxpayer_id, ay, version_no)
) STRICT;

CREATE TABLE IF NOT EXISTS filings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  form TEXT NOT NULL CHECK (form IN ('ITR-1','ITR-2')),
  filing_type TEXT NOT NULL DEFAULT 'original'
    CHECK (filing_type IN ('original','revised','belated')),
  computation_id INTEGER NOT NULL REFERENCES computations(id),
  schema_version TEXT NOT NULL,
  json_sha256 TEXT NOT NULL,
  json_path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'generated'
    CHECK (status IN ('generated','uploaded','everified','processed','void')),
  ack_no TEXT,
  filed_on TEXT,
  everified_on TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS column_maps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  header_signature TEXT NOT NULL UNIQUE,
  bank_hint TEXT,
  map_json TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL,
  last_used_at TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS server_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  username TEXT,
  action TEXT NOT NULL,
  entity TEXT,
  entity_id TEXT,
  taxpayer_id INTEGER,
  details TEXT,
  prev_hash TEXT NOT NULL,
  row_hash TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_audit_tp ON audit_log(taxpayer_id);

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;
