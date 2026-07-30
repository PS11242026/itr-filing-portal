-- 0003: Taxpayer Information Summary (TIS) — the ITD's category-level
-- summary that pairs with AIS. Keeps both the reported ("processed") and
-- post-feedback ("derived") figures; autofill/recon prefer the derived value.

CREATE TABLE IF NOT EXISTS tis_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT,
  processed_value_paise INTEGER NOT NULL DEFAULT 0,
  derived_value_paise INTEGER NOT NULL DEFAULT 0,
  details_json TEXT NOT NULL DEFAULT '{}',
  source_file TEXT,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_tis_tp ON tis_entries(taxpayer_id, ay);
