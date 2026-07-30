"""Integration: account → parse → stage → commit → dedup → coverage."""

from itrcore.authz import mint_scope
from itrcore.ingest import probe
from itrcore.ingest.banks import best_parse

CSV = (
    "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,"
    "Deposit Amt.,Closing Balance\n"
    "01/04/25,SALARY-ACME-APR,N1,01/04/25,,\"1,50,000.00\","
    "\"3,00,000.00\"\n"
    "01/05/25,SALARY-ACME-MAY,N2,01/05/25,,\"1,50,000.00\","
    "\"4,50,000.00\"\n"
    "15/05/25,UPI-GROCERY,U1,15/05/25,\"5,000.00\",,\"4,45,000.00\"\n"
)


def _setup(partner, taxpayer_id):
    from itrcore.db.repos import bank_accounts, statements
    scope = mint_scope(partner, taxpayer_id)
    acct_id = bank_accounts.add_account(
        scope, bank_name="HDFC Bank", account_no="50100123456789",
        ifsc="HDFC0001234")
    result = best_parse(probe.make_probe("stmt.csv", CSV.encode()))
    stmt_id = statements.create_staged(
        scope, account_id=acct_id, ay="2026-27", doc_id=None,
        adapter_id=result.adapter_id, txns=list(result.txns), issues=[])
    return scope, acct_id, stmt_id, result


def test_stage_commit_and_reupload_dedup(partner, taxpayer_id):
    from itrcore.db import connection
    from itrcore.db.repos import statements
    scope, acct_id, stmt_id, result = _setup(partner, taxpayer_id)

    stats = statements.commit(scope, stmt_id)
    assert stats == {"inserted": 3, "duplicates_skipped": 0}

    # same file staged again → everything deduplicates
    stmt2 = statements.create_staged(
        scope, account_id=acct_id, ay="2026-27", doc_id=None,
        adapter_id=result.adapter_id, txns=list(result.txns), issues=[])
    stats2 = statements.commit(scope, stmt2)
    assert stats2 == {"inserted": 0, "duplicates_skipped": 3}

    rows = connection.query(
        "SELECT COUNT(*) AS c FROM transactions WHERE taxpayer_id=?",
        (scope.taxpayer_id,))
    assert rows[0]["c"] == 3


def test_coverage_reports_gaps_and_blockers(partner, taxpayer_id):
    from itrcore.db.repos import statements
    scope, _, stmt_id, _ = _setup(partner, taxpayer_id)
    statements.commit(scope, stmt_id)

    cov = statements.coverage(scope, "2026-27")
    assert len(cov) == 1
    assert cov[0]["months_covered"] == 2          # Apr + May
    assert cov[0]["gaps"] == []                   # contiguous span

    blockers = statements.coverage_blockers(scope, "2026-27")
    assert blockers == []                         # no internal gaps


def test_account_duplicate_rejected(partner, taxpayer_id):
    import pytest
    from itrcore.db.repos import bank_accounts
    scope = mint_scope(partner, taxpayer_id)
    bank_accounts.add_account(scope, bank_name="SBI",
                              account_no="1234567890")
    with pytest.raises(ValueError):
        bank_accounts.add_account(scope, bank_name="SBI again",
                                  account_no="1234 5678 90")
