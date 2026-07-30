"""Transaction classification persistence + the readiness gate's counts."""

from datetime import date

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection

TERMINAL_LABELS = (
    "income:salary", "income:house_property", "income:capital_gains",
    "income:other_sources:interest", "income:other_sources:dividend",
    "income:other_sources:gift", "income:other_sources",
    "non_income:self_transfer", "non_income:investment",
    "non_income:personal", "non_income:loan_receipt",
    "non_income:exempt_gift", "noise")


def list_txns(scope: TaxpayerScope, ay: str, *, status: str | None = None,
              only_open: bool = False, limit: int = 5000):
    sql = ("SELECT t.*, b.bank_name, b.last4 FROM transactions t"
           " JOIN bank_accounts b ON b.id = t.account_id"
           " WHERE t.taxpayer_id=? AND t.ay=?")
    params: list = [scope.taxpayer_id, ay]
    if status:
        sql += " AND t.status=?"
        params.append(status)
    if only_open:
        sql += " AND t.terminal_label IS NULL"
    sql += " ORDER BY t.txn_date, t.id LIMIT ?"
    params.append(limit)
    return connection.query(sql, tuple(params))


def rows_for_analysis(scope: TaxpayerScope, ay: str) -> list[dict]:
    """Only rows staff have not already decided (unclassified/auto)."""
    rows = connection.query(
        "SELECT id, account_id, txn_date, narration, amount_paise"
        " FROM transactions WHERE taxpayer_id=? AND ay=?"
        " AND status IN ('unclassified','auto')",
        (scope.taxpayer_id, ay))
    return [{"id": r["id"], "account_id": r["account_id"],
             "txn_date": date.fromisoformat(r["txn_date"]),
             "narration": r["narration"],
             "amount_paise": r["amount_paise"]} for r in rows]


def apply_analysis(scope: TaxpayerScope, ay: str, outcome) -> dict:
    """Write AnalysisOutcome updates; raise queries (deduped by topic)."""
    require_staff(scope.principal, "Staff", "Running analysis")
    with connection.tx() as conn:
        for upd in outcome.updates:
            conn.execute(
                "UPDATE transactions SET mode=?, counterparty_key=?,"
                " counterparty_display=?, auto_category=?, flag_reason=?,"
                " status=CASE WHEN status='unclassified' THEN 'auto'"
                " ELSE status END, updated_by=?, updated_at=?"
                " WHERE id=? AND taxpayer_id=?"
                " AND status IN ('unclassified','auto')",
                (upd.mode, upd.counterparty_key, upd.counterparty_display,
                 upd.auto_category, upd.flag_reason,
                 scope.principal.username, connection.now(),
                 upd.txn_id, scope.taxpayer_id))

        existing_topics = {r["topic"] for r in conn.execute(
            "SELECT topic FROM explanation_queries WHERE taxpayer_id=?"
            " AND ay=? AND status IN ('open','answered')",
            (scope.taxpayer_id, ay)).fetchall()}
        raised = 0
        for q in outcome.queries:
            if q.topic in existing_topics:
                continue
            conn.execute(
                "INSERT INTO explanation_queries (taxpayer_id, ay,"
                " transaction_id, topic, question, created_by, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (scope.taxpayer_id, ay, q.transaction_id, q.topic,
                 q.question, scope.principal.username, connection.now()))
            existing_topics.add(q.topic)
            raised += 1
            if q.transaction_id is not None:
                conn.execute(
                    "UPDATE transactions SET status='query_raised'"
                    " WHERE id=? AND taxpayer_id=?"
                    " AND status IN ('unclassified','auto')",
                    (q.transaction_id, scope.taxpayer_id))
    auditlog.log(scope.principal.username, "ANALYSIS_RUN",
                 entity="transactions", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id,
                 details={**outcome.stats, "queries_raised": raised})
    return {**outcome.stats, "queries_raised": raised}


def set_terminal(scope: TaxpayerScope, txn_ids: list[int],
                 label: str) -> int:
    require_staff(scope.principal, "Staff", "Classifying transactions")
    if label not in TERMINAL_LABELS:
        raise ValueError(f"unknown label {label}")
    count = 0
    with connection.tx() as conn:
        for tid in txn_ids:
            cur = conn.execute(
                "UPDATE transactions SET terminal_label=?,"
                " status='staff_confirmed', updated_by=?, updated_at=?"
                " WHERE id=? AND taxpayer_id=?",
                (label, scope.principal.username, connection.now(),
                 tid, scope.taxpayer_id))
            count += cur.rowcount
    auditlog.log(scope.principal.username, "TXN_CLASSIFY",
                 entity="transactions", entity_id=len(txn_ids),
                 taxpayer_id=scope.taxpayer_id,
                 details={"label": label, "count": count})
    return count


def confirm_auto(scope: TaxpayerScope, ay: str,
                 category: str | None = None) -> int:
    """Bulk-accept auto suggestions (optionally one category only)."""
    require_staff(scope.principal, "Staff", "Confirming classifications")
    sql = ("UPDATE transactions SET terminal_label=auto_category,"
           " status='staff_confirmed', updated_by=?, updated_at=?"
           " WHERE taxpayer_id=? AND ay=? AND status='auto'"
           " AND auto_category IS NOT NULL AND terminal_label IS NULL")
    params: list = [scope.principal.username, connection.now(),
                    scope.taxpayer_id, ay]
    if category:
        sql += " AND auto_category=?"
        params.append(category)
    with connection.tx() as conn:
        cur = conn.execute(sql, tuple(params))
        count = cur.rowcount
    auditlog.log(scope.principal.username, "TXN_BULK_CONFIRM",
                 entity="transactions", entity_id=count,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "category": category, "count": count})
    return count


def summary_by_label(scope: TaxpayerScope, ay: str) -> list[dict]:
    rows = connection.query(
        "SELECT COALESCE(terminal_label, COALESCE(auto_category,"
        " 'unclassified')) AS label, status IS NOT NULL AS _s,"
        " COUNT(*) AS n, SUM(amount_paise) AS total,"
        " SUM(CASE WHEN terminal_label IS NULL THEN 1 ELSE 0 END)"
        " AS pending FROM transactions WHERE taxpayer_id=? AND ay=?"
        " GROUP BY label ORDER BY label", (scope.taxpayer_id, ay))
    return [dict(r) for r in rows]


def open_counts(scope: TaxpayerScope, ay: str) -> dict:
    row = connection.query_one(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN terminal_label IS NULL THEN 1 ELSE 0 END)"
        " AS non_terminal FROM transactions"
        " WHERE taxpayer_id=? AND ay=?", (scope.taxpayer_id, ay))
    return {"total": row["total"] or 0,
            "non_terminal": row["non_terminal"] or 0}
