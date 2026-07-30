"""Statement staging → committed transactions, with dedup and coverage."""

import json
from datetime import date

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.constants import fy_for_ay
from itrcore.db import auditlog, connection
from itrcore.ingest.base import RawTxn
from itrcore.ingest.dedup import coverage_gaps, month_key, with_fingerprints


def _txn_to_json(txn: RawTxn) -> str:
    return json.dumps({
        "txn_date": txn.txn_date.isoformat(),
        "value_date": txn.value_date.isoformat() if txn.value_date else None,
        "narration": txn.narration, "ref": txn.ref,
        "amount_paise": txn.amount_paise,
        "balance_paise": txn.balance_paise, "row_no": txn.row_no})


def txn_from_json(payload: str) -> RawTxn:
    d = json.loads(payload)
    return RawTxn(
        txn_date=date.fromisoformat(d["txn_date"]),
        value_date=(date.fromisoformat(d["value_date"])
                    if d.get("value_date") else None),
        narration=d.get("narration", ""), ref=d.get("ref"),
        amount_paise=int(d["amount_paise"]),
        balance_paise=(int(d["balance_paise"])
                       if d.get("balance_paise") is not None else None),
        row_no=int(d.get("row_no", 0)))


def create_staged(scope: TaxpayerScope, *, account_id: int, ay: str,
                  doc_id: int | None, adapter_id: str,
                  txns: list[RawTxn], issues: list[str]) -> int:
    require_staff(scope.principal, "Staff", "Importing statements")
    account = connection.query_one(
        "SELECT id FROM bank_accounts WHERE id=? AND taxpayer_id=?",
        (account_id, scope.taxpayer_id))
    if account is None:
        raise ValueError("bank account not found for this client")
    with connection.tx() as conn:
        cur = conn.execute(
            "INSERT INTO statements (taxpayer_id, account_id, doc_id, ay,"
            " adapter_id, imported_by, imported_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (scope.taxpayer_id, account_id, doc_id, ay, adapter_id,
             scope.principal.username, connection.now()))
        stmt_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO statement_rows (statement_id, row_no, raw_json,"
            " parsed_json, issues) VALUES (?,?,?,?,?)",
            [(stmt_id, t.row_no, _txn_to_json(t), _txn_to_json(t), None)
             for t in txns])
        if issues:
            conn.execute(
                "INSERT INTO statement_rows (statement_id, row_no,"
                " raw_json, parsed_json, issues) VALUES (?,?,?,?,?)",
                (stmt_id, 0, "{}", None, json.dumps(issues)))
    auditlog.log(scope.principal.username, "STATEMENT_STAGE",
                 entity="statement", entity_id=stmt_id,
                 taxpayer_id=scope.taxpayer_id,
                 details={"rows": len(txns), "adapter": adapter_id})
    return stmt_id


def staged_txns(scope: TaxpayerScope, statement_id: int) -> list[RawTxn]:
    _own_statement(scope, statement_id)
    rows = connection.query(
        "SELECT parsed_json FROM statement_rows WHERE statement_id=?"
        " AND parsed_json IS NOT NULL ORDER BY row_no", (statement_id,))
    return [txn_from_json(r["parsed_json"]) for r in rows]


def replace_staged(scope: TaxpayerScope, statement_id: int,
                   txns: list[RawTxn]) -> None:
    require_staff(scope.principal, "Staff", "Editing staged rows")
    _own_statement(scope, statement_id)
    with connection.tx() as conn:
        conn.execute("DELETE FROM statement_rows WHERE statement_id=?",
                     (statement_id,))
        conn.executemany(
            "INSERT INTO statement_rows (statement_id, row_no, raw_json,"
            " parsed_json) VALUES (?,?,?,?)",
            [(statement_id, t.row_no or i + 1, _txn_to_json(t),
              _txn_to_json(t)) for i, t in enumerate(txns)])


def _own_statement(scope: TaxpayerScope, statement_id: int):
    row = connection.query_one(
        "SELECT * FROM statements WHERE id=? AND taxpayer_id=?",
        (statement_id, scope.taxpayer_id))
    if row is None:
        raise ValueError("statement not found")
    return row


def commit(scope: TaxpayerScope, statement_id: int) -> dict:
    """Staged rows → transactions with fingerprint dedup."""
    require_staff(scope.principal, "Staff", "Committing statements")
    stmt = _own_statement(scope, statement_id)
    if stmt["status"] != "staged":
        raise ValueError("statement is not in staged state")
    txns = staged_txns(scope, statement_id)
    if not txns:
        raise ValueError("no parsed rows to commit")

    pairs = with_fingerprints(stmt["account_id"], txns)
    inserted = skipped = 0
    with connection.tx() as conn:
        existing = {r["fingerprint"] for r in conn.execute(
            "SELECT fingerprint FROM transactions WHERE account_id=?",
            (stmt["account_id"],)).fetchall()}
        for txn, fp in pairs:
            if fp in existing:
                skipped += 1
                continue
            existing.add(fp)
            conn.execute(
                "INSERT INTO transactions (taxpayer_id, account_id,"
                " statement_id, ay, txn_date, value_date, narration,"
                " ref_no, amount_paise, balance_paise, fingerprint)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scope.taxpayer_id, stmt["account_id"], statement_id,
                 stmt["ay"], txn.txn_date.isoformat(),
                 txn.value_date.isoformat() if txn.value_date else None,
                 txn.narration, txn.ref, txn.amount_paise,
                 txn.balance_paise, fp))
            inserted += 1
        dates = [t.txn_date for t in txns]
        conn.execute(
            "UPDATE statements SET status='committed', rows_imported=?,"
            " period_from=?, period_to=? WHERE id=?",
            (inserted, min(dates).isoformat(), max(dates).isoformat(),
             statement_id))
    auditlog.log(scope.principal.username, "STATEMENT_COMMIT",
                 entity="statement", entity_id=statement_id,
                 taxpayer_id=scope.taxpayer_id,
                 details={"inserted": inserted, "duplicates": skipped})
    return {"inserted": inserted, "duplicates_skipped": skipped}


def list_statements(scope: TaxpayerScope, ay: str):
    return connection.query(
        "SELECT s.*, b.bank_name, b.last4 FROM statements s"
        " JOIN bank_accounts b ON b.id = s.account_id"
        " WHERE s.taxpayer_id=? AND s.ay=? ORDER BY s.id DESC",
        (scope.taxpayer_id, ay))


def coverage(scope: TaxpayerScope, ay: str) -> list[dict]:
    """Per-account covered months + internal gaps for the FY."""
    fy_start_s, fy_end_s = fy_for_ay(ay)
    fy_start, fy_end = (date.fromisoformat(fy_start_s),
                        date.fromisoformat(fy_end_s))
    out = []
    accounts = connection.query(
        "SELECT id, bank_name, last4 FROM bank_accounts"
        " WHERE taxpayer_id=? AND active=1", (scope.taxpayer_id,))
    for acct in accounts:
        rows = connection.query(
            "SELECT txn_date FROM transactions WHERE account_id=? AND ay=?",
            (acct["id"], ay))
        covered = {month_key(date.fromisoformat(r["txn_date"]))
                   for r in rows}
        gaps = coverage_gaps(covered, fy_start, fy_end)
        out.append({"account_id": acct["id"],
                    "label": f"{acct['bank_name']} ··{acct['last4']}",
                    "months_covered": len(covered),
                    "gaps": gaps,
                    "has_data": bool(covered)})
    return out


def coverage_blockers(scope: TaxpayerScope, ay: str) -> list[str]:
    blockers = []
    for c in coverage(scope, ay):
        if not c["has_data"]:
            blockers.append(f"{c['label']}: no transactions imported")
        elif c["gaps"]:
            blockers.append(f"{c['label']}: missing months "
                            + ", ".join(c["gaps"]))
    return blockers
