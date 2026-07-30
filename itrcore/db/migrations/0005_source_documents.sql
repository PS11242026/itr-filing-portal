-- 0005: Parsed AIS and Form 26AS source documents.
-- The flat feeds (ais_entries / tis_entries / tds_entries) drive the tax
-- computation; these tables keep the FULL parsed document (all AIS parts with
-- L1+L2 detail, or every 26AS deductor + transaction) as JSON so the
-- Genius-style display and the Excel/PDF re-exports can be rebuilt any time.
-- One current document per taxpayer + AY per kind; a re-import replaces it.

CREATE TABLE IF NOT EXISTS ais_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  source_file TEXT,
  doc_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (taxpayer_id, ay)
) STRICT;

CREATE TABLE IF NOT EXISTS form26as_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  source_file TEXT,
  doc_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (taxpayer_id, ay)
) STRICT;
