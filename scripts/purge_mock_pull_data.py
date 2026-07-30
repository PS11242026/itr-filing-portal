"""One-off cleanup: remove MOCK ITD-pull leftovers from real client records.

Before 2026-07-21 the "Pull from ITD" button used MockITDPortalClient and
wrote demo rows (TAN SBIN12345A / DELC98765F, sample AIS amounts) into real
taxpayers. Real re-imports replace their own tables, but stale mock rows
linger where no real import has happened yet — and mock TDS rows that
auto-fill copied into saved computation inputs stay there forever.

Dry-run by default; pass --apply to write. A timestamped DB backup is taken
first, and the purge is audit-logged.

Usage:  .venv\\Scripts\\python scripts\\purge_mock_pull_data.py [--apply]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from itrcore.db import auditlog, connection

MOCK_TANS = ("SBIN12345A", "DELC98765F")
MOCK_AIS_PAISE = {1850000, 6200000, 940000, 35000000}


def main(apply: bool) -> None:
    tds = connection.query(
        "SELECT id, taxpayer_id, tan, deductor_name FROM tds_entries"
        f" WHERE tan IN ({','.join('?' * len(MOCK_TANS))})", MOCK_TANS)
    ais = [r for r in connection.query(
        "SELECT id, taxpayer_id, amount_paise, description FROM ais_entries")
        if r["amount_paise"] in MOCK_AIS_PAISE]
    tis = [r for r in connection.query(
        "SELECT id, taxpayer_id, processed_value_paise, description"
        " FROM tis_entries")
        if r["processed_value_paise"] in MOCK_AIS_PAISE]
    inputs_rows = []
    for r in connection.query(
            "SELECT rowid, taxpayer_id, ay, inputs_json"
            " FROM computation_inputs"):
        d = json.loads(r["inputs_json"])
        kept = [t for t in d.get("tds", [])
                if t.get("tan") not in MOCK_TANS]
        if len(kept) != len(d.get("tds", [])):
            inputs_rows.append((r, d, kept))

    print(f"mock TDS rows:      {len(tds)}")
    for r in tds:
        print(f"   taxpayer {r['taxpayer_id']}  {r['tan']}  "
              f"{r['deductor_name']}")
    print(f"mock AIS rows:      {len(ais)}")
    for r in ais:
        print(f"   taxpayer {r['taxpayer_id']}  {r['description']}  "
              f"{r['amount_paise'] / 100:,.0f}")
    print(f"mock TIS rows:      {len(tis)}")
    print(f"saved inputs with mock TDS: {len(inputs_rows)}")
    for r, _d, kept in inputs_rows:
        print(f"   taxpayer {r['taxpayer_id']} AY {r['ay']} -> "
              f"{len(kept)} TDS rows kept")

    if not (tds or ais or tis or inputs_rows):
        print("nothing to purge — clean.")
        return
    if not apply:
        print("\nDRY RUN — nothing changed. Re-run with --apply to purge.")
        return

    dest = connection.backup()
    print(f"\nbackup written: {dest}")
    for r in tds:
        connection.execute("DELETE FROM tds_entries WHERE id=?", (r["id"],))
    for r in ais:
        connection.execute("DELETE FROM ais_entries WHERE id=?", (r["id"],))
    for r in tis:
        connection.execute("DELETE FROM tis_entries WHERE id=?", (r["id"],))
    for r, d, kept in inputs_rows:
        d["tds"] = kept
        connection.execute(
            "UPDATE computation_inputs SET inputs_json=? WHERE rowid=?",
            (json.dumps(d), r["rowid"]))
    auditlog.log("system-cleanup", "MOCK_DATA_PURGE", details={
        "tds_deleted": len(tds), "ais_deleted": len(ais),
        "tis_deleted": len(tis),
        "inputs_scrubbed": [r["taxpayer_id"] for r, _, _ in inputs_rows],
        "backup": str(dest)})
    print("purged and audit-logged.")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
