"""Seed a demo environment: partner + staff + one client with a year of
bank transactions, Form 16, 26AS and AIS — so every page has data.

Run:  .venv\\Scripts\\python scripts\\seed_demo.py
(SAFE: refuses to run if any user already exists.)
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from itrcore.authz import mint_scope
from itrcore.db import migrate
from itrcore.db.repos import (bank_accounts, importers,
                              statements, taxpayers, users)
from itrcore.ingest.base import RawTxn

AY = "2026-27"


def main() -> None:
    migrate.upgrade()
    if users.user_count():
        print("Refusing to seed: users already exist in this database.")
        return

    users.create_user(None, "partner", "partner123", "Partner")
    partner = users.authenticate("partner", "partner123").principal
    users.create_user(partner, "staff1", "staff1234", "Staff")

    tid = taxpayers.create_taxpayer(
        partner, full_name="Ramesh Kumar", pan="AAAPL1234C",
        dob="1985-06-15", email="ramesh@example.com", mobile="9820012345",
        address="14 Marine Drive, Mumbai",
        profile={"has_salary": True, "has_let_out_property": True,
                 "wants_old_regime": True})
    users.create_user(partner, "client_ramesh", "client123", "Client",
                      taxpayer_id=tid)
    scope = mint_scope(partner, tid)

    acct = bank_accounts.add_account(scope, bank_name="HDFC Bank",
                                     account_no="50100123456789",
                                     ifsc="HDFC0001234")
    txns: list[RawTxn] = []
    row = 1
    for m in range(12):                       # salary Apr 2025 – Mar 2026
        d = dt.date(2025, 4, 1) + dt.timedelta(days=int(m * 30.44))
        txns.append(RawTxn(txn_date=d, amount_paise=12500000,
                           narration=f"NEFT-ACME LTD-SALARY {d:%b %y}",
                           row_no=row))
        row += 1
    for m in range(8):                        # rent Apr–Nov, tenant leaves
        d = dt.date(2025, 4, 5) + dt.timedelta(days=int(m * 30.44))
        txns.append(RawTxn(txn_date=d, amount_paise=2500000,
                           narration="UPI-SUNIL VERMA-sunil@oksbi-RENT",
                           row_no=row))
        row += 1
    txns += [
        RawTxn(txn_date=dt.date(2025, 8, 14), amount_paise=30000000,
               narration="IMPS-ANITA SHARMA-P2A-508812345", row_no=row),
        RawTxn(txn_date=dt.date(2025, 10, 2), amount_paise=125050,
               narration="INT.PD:SB INTEREST", row_no=row + 1),
        RawTxn(txn_date=dt.date(2025, 12, 15), amount_paise=-59000,
               narration="CONSOLIDATED CHARGES GST", row_no=row + 2),
        RawTxn(txn_date=dt.date(2026, 1, 10), amount_paise=-4500000,
               narration="ZERODHA BROKING LTD-NEFT", row_no=row + 3),
    ]
    stmt = statements.create_staged(scope, account_id=acct, ay=AY,
                                    doc_id=None, adapter_id="seed",
                                    txns=txns, issues=[])
    statements.commit(scope, stmt)

    importers.save_form16(scope, AY, employer_name="Acme Ltd",
                          employer_tan="MUMA12345B",
                          gross_salary_paise=150000000,
                          exempt_allowances_paise=0, prof_tax_paise=240000,
                          tds_paise=11000000)
    importers.import_26as(scope, AY, [
        {"tan": "MUMA12345B", "deductor_name": "ACME LTD",
         "section": "192", "amount_paid_paise": 150000000,
         "tds_paise": 11000000}])
    importers.import_ais(scope, AY, [
        {"category": "interest_savings",
         "description": "Interest from savings bank",
         "amount_paise": 125050},
        {"category": "rent", "description": "Rent received",
         "amount_paise": 20000000}], "seed_ais.json")

    print("Seeded. Logins: partner/partner123, staff1/staff1234, "
          "client_ramesh/client123")
    print("Next: run analysis on Review transactions, answer the gift "
          "query as the client, confirm classifications, compute, "
          "reconcile, finalise, generate ITR.")


if __name__ == "__main__":
    main()
