-- 0002: working (draft) assessment inputs per taxpayer/AY, edited on the
-- staff computation page; snapshots go to `computations` on compute+save.

CREATE TABLE IF NOT EXISTS computation_inputs (
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  updated_by TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (taxpayer_id, ay)
) STRICT;
