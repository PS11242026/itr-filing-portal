"""SRS acceptance scenarios end-to-end through the service layer:
statements → analysis → queries → client answers → staff resolution →
readiness gate → computation.
"""

import datetime as dt

from itrcore.analysis import classify, gate
from itrcore.authz import mint_scope
from itrcore.constants import fy_for_ay
from itrcore.ingest.base import RawTxn


def _import_txns(scope, acct_id, txns):
    from itrcore.db.repos import statements
    stmt = statements.create_staged(scope, account_id=acct_id, ay="2026-27",
                                    doc_id=None, adapter_id="test",
                                    txns=txns, issues=[])
    statements.commit(scope, stmt)
    return stmt


def _run_analysis(scope, employers=()):
    from itrcore.db.repos import transactions as txn_repo
    rows = txn_repo.rows_for_analysis(scope, "2026-27")
    fy_end = dt.date.fromisoformat(fy_for_ay("2026-27")[1])
    outcome = classify.analyze(rows, employer_names=list(employers),
                               fy_end=fy_end)
    return txn_repo.apply_analysis(scope, "2026-27", outcome)


def _account(scope, no="50100999888777"):
    from itrcore.db.repos import bank_accounts
    return bank_accounts.add_account(scope, bank_name="HDFC",
                                     account_no=no, ifsc="HDFC0001234")


def test_srs1_salary_reconciliation(partner, taxpayer_id):
    """12 identical salary credits auto-detect and total to Form 16 gross."""
    from itrcore.db.repos import importers, transactions as txn_repo
    scope = mint_scope(partner, taxpayer_id)
    acct = _account(scope)
    txns = [RawTxn(txn_date=dt.date(2025, 4, 1)
                   + dt.timedelta(days=30 * i),
                   narration=f"NEFT-ACME LTD-SALARY M{i}",
                   amount_paise=12500000, row_no=i + 1)
            for i in range(12)]
    _import_txns(scope, acct, txns)
    importers.save_form16(scope, "2026-27", employer_name="Acme Ltd",
                          employer_tan="MUMA12345B",
                          gross_salary_paise=150000000,
                          exempt_allowances_paise=0, prof_tax_paise=0,
                          tds_paise=10000000)
    stats = _run_analysis(scope, employers=["Acme Ltd"])
    assert stats["auto_categorized"] >= 12

    summary = {s["label"]: s for s in
               txn_repo.summary_by_label(scope, "2026-27")}
    assert summary["income:salary"]["n"] == 12
    assert summary["income:salary"]["total"] == 150000000  # matches Form 16

    txn_repo.confirm_auto(scope, "2026-27")
    assert txn_repo.open_counts(scope, "2026-27")["non_terminal"] == 0


def test_srs2_prorated_rent_tenant_left(partner, taxpayer_id):
    """8 rent credits → series query; tenant-vacated query on early end."""
    from itrcore.db.repos import queries as queries_repo
    from itrcore.db.repos import transactions as txn_repo
    scope = mint_scope(partner, taxpayer_id)
    acct = _account(scope)
    txns = [RawTxn(txn_date=dt.date(2025, 4, 5)
                   + dt.timedelta(days=30 * i),
                   narration="UPI-SUNIL TENANT-sunil@oksbi-RENT",
                   amount_paise=2500000, row_no=i + 1)
            for i in range(8)]                      # Apr–Nov, then stops
    _import_txns(scope, acct, txns)
    _run_analysis(scope)

    open_qs = queries_repo.list_queries(scope, "2026-27", status="open")
    questions = " || ".join(q["question"] for q in open_qs)
    assert "vacate" in questions or "arrangement end" in questions

    # client answers; staff resolves and confirms classification
    client_scope = scope                            # staff acting on behalf
    for q in open_qs:
        queries_repo.answer(client_scope, q["id"],
                            "Tenant vacated on 30-11-2025; rent was "
                            "₹25,000/month for 8 months.")
    for q in queries_repo.list_queries(scope, "2026-27", status="answered"):
        queries_repo.resolve(scope, q["id"],
                             "Rent confirmed ₹25,000 × 8 months; HP "
                             "income prorated.")
    txn_repo.confirm_auto(scope, "2026-27")

    report = gate.readiness(scope, "2026-27")
    assert not any("quer" in b.lower() for b in report.blockers)
    summary = {s["label"]: s for s in
               txn_repo.summary_by_label(scope, "2026-27")}
    assert summary["income:house_property"]["total"] == 20000000  # 8 × 25k


def test_srs4_gift_from_relative_exempt_vs_friend_taxable(partner,
                                                          taxpayer_id):
    """Large one-off credits get identity queries; relative → exempt,
    friend ₹60k → taxable other-sources income."""
    from itrcore.db.repos import queries as queries_repo
    from itrcore.db.repos import transactions as txn_repo
    scope = mint_scope(partner, taxpayer_id)
    acct = _account(scope)
    txns = [
        RawTxn(txn_date=dt.date(2025, 8, 14),
               narration="IMPS-ANITA AUNT-P2A-GIFT",
               amount_paise=30000000, row_no=1),     # ₹3L from aunt
        RawTxn(txn_date=dt.date(2025, 12, 1),
               narration="IMPS-ROHIT FRIEND-P2A",
               amount_paise=25000000, row_no=2),     # ₹2.5L from friend
    ]
    _import_txns(scope, acct, txns)
    _run_analysis(scope)

    open_qs = queries_repo.list_queries(scope, "2026-27", status="open")
    assert len(open_qs) == 2
    assert all("56(2)(x)" in q["question"] for q in open_qs)

    rows = txn_repo.list_txns(scope, "2026-27")
    aunt_id = next(r["id"] for r in rows if "AUNT" in r["narration"])
    friend_id = next(r["id"] for r in rows if "FRIEND" in r["narration"])

    for q in open_qs:
        if q["transaction_id"] == aunt_id:
            queries_repo.answer(scope, q["id"],
                                "Gift from my aunt (father's sister).")
        else:
            queries_repo.answer(scope, q["id"], "Gift from a friend.")
    for q in queries_repo.list_queries(scope, "2026-27",
                                       status="answered"):
        if q["transaction_id"] == aunt_id:
            queries_repo.resolve(scope, q["id"],
                                 "Relative u/s 56(2)(x) — exempt.")
        else:
            queries_repo.resolve(scope, q["id"],
                                 "Non-relative gift > ₹50,000 — taxable.")

    txn_repo.set_terminal(scope, [aunt_id], "non_income:exempt_gift")
    txn_repo.set_terminal(scope, [friend_id], "income:other_sources:gift")

    assert txn_repo.open_counts(scope, "2026-27")["non_terminal"] == 0
    summary = {s["label"]: s for s in
               txn_repo.summary_by_label(scope, "2026-27")}
    assert summary["income:other_sources:gift"]["total"] == 25000000
    assert summary["non_income:exempt_gift"]["total"] == 30000000


def test_gate_blocks_until_everything_terminal(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    acct = _account(scope)
    _import_txns(scope, acct, [
        RawTxn(txn_date=dt.date(2025, 8, 14),
               narration="IMPS-SOMEONE-P2A", amount_paise=30000000,
               row_no=1)])
    _run_analysis(scope)
    report = gate.readiness(scope, "2026-27")
    assert any("not yet classified" in b for b in report.blockers)
    assert any("quer" in b.lower() for b in report.blockers)
