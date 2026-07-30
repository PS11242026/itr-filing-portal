-- 0006: Parked (still-ITD-encrypted) AIS payloads.
-- A portal pull can download the AIS before the client's date of birth is
-- on record — without it the envelope cannot be decrypted. Instead of
-- forcing a re-pull (CAPTCHA + OTP again), the raw payload is parked here
-- and imported automatically by the next page render once the DOB exists.
-- The payload stays exactly as the ITD served it: AES-encrypted, keyed to
-- the client's own PAN + DOB. One parked payload per taxpayer + AY.

CREATE TABLE IF NOT EXISTS pending_ais_payloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  taxpayer_id INTEGER NOT NULL REFERENCES taxpayers(id),
  ay TEXT NOT NULL,
  payload BLOB NOT NULL,
  source_file TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (taxpayer_id, ay)
) STRICT;
