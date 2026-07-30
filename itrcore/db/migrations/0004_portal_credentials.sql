-- 0004: encrypted store for clients' government-portal credentials so the
-- firm can pull AIS/TIS/26AS on their behalf (with consent). The password is
-- Fernet-encrypted (password_enc), same as PAN/Aadhaar; only staff write it,
-- and every read is audit-logged at the repo layer. The login id is the
-- taxpayer's PAN, already on the taxpayers row — not duplicated here.

CREATE TABLE IF NOT EXISTS portal_credentials (
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  portal TEXT NOT NULL DEFAULT 'itd_efiling'
    CHECK (portal IN ('itd_efiling','traces')),
  password_enc BLOB NOT NULL,
  extra_json TEXT NOT NULL DEFAULT '{}',
  updated_by TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (taxpayer_id, portal)
) STRICT;
