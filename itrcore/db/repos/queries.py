"""ExplanationQuery lifecycle: staff raise → client answers → staff resolve.

The analysis engine (M5) also raises queries automatically through
raise_query(); the workflow is identical.
"""

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection


def raise_query(scope: TaxpayerScope, *, ay: str, question: str,
                transaction_id: int | None = None,
                topic: str = "transaction") -> int:
    require_staff(scope.principal, "Staff", "Raising queries")
    if not (question or "").strip():
        raise ValueError("question text is required")
    qid = connection.execute(
        "INSERT INTO explanation_queries (taxpayer_id, ay, transaction_id,"
        " topic, question, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
        (scope.taxpayer_id, ay, transaction_id, topic, question.strip(),
         scope.principal.username, connection.now()))
    auditlog.log(scope.principal.username, "QUERY_RAISE", entity="query",
                 entity_id=qid, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "topic": topic})
    return qid


def list_queries(scope: TaxpayerScope, ay: str | None = None,
                 status: str | None = None):
    sql = "SELECT * FROM explanation_queries WHERE taxpayer_id=?"
    params: list = [scope.taxpayer_id]
    if ay:
        sql += " AND ay=?"
        params.append(ay)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY created_at DESC"
    return connection.query(sql, tuple(params))


def open_count(scope: TaxpayerScope, ay: str | None = None) -> int:
    sql = ("SELECT COUNT(*) AS c FROM explanation_queries"
           " WHERE taxpayer_id=? AND status IN ('open','answered')")
    params: list = [scope.taxpayer_id]
    if ay:
        sql += " AND ay=?"
        params.append(ay)
    row = connection.query_one(sql, tuple(params))
    return int(row["c"]) if row else 0


def answer(scope: TaxpayerScope, query_id: int, response_text: str,
           response_doc_id: int | None = None) -> None:
    """Client (or staff on the client's behalf) answers an open query."""
    if not (response_text or "").strip():
        raise ValueError("a response is required")
    row = connection.query_one(
        "SELECT * FROM explanation_queries WHERE id=? AND taxpayer_id=?",
        (query_id, scope.taxpayer_id))
    if row is None:
        raise ValueError("query not found")
    if row["status"] not in ("open", "answered"):
        raise ValueError("this query is already resolved")
    connection.execute(
        "UPDATE explanation_queries SET client_response=?, response_doc_id=?,"
        " answered_at=?, status='answered' WHERE id=?",
        (response_text.strip(), response_doc_id, connection.now(), query_id))
    auditlog.log(scope.principal.username, "QUERY_ANSWER", entity="query",
                 entity_id=query_id, taxpayer_id=scope.taxpayer_id)


def resolve(scope: TaxpayerScope, query_id: int, disposition: str,
            withdraw: bool = False) -> None:
    require_staff(scope.principal, "Staff", "Resolving queries")
    row = connection.query_one(
        "SELECT * FROM explanation_queries WHERE id=? AND taxpayer_id=?",
        (query_id, scope.taxpayer_id))
    if row is None:
        raise ValueError("query not found")
    status = "withdrawn" if withdraw else "resolved"
    connection.execute(
        "UPDATE explanation_queries SET staff_disposition=?, resolved_by=?,"
        " resolved_at=?, status=? WHERE id=?",
        (disposition, scope.principal.username, connection.now(), status,
         query_id))
    auditlog.log(scope.principal.username, "QUERY_RESOLVE", entity="query",
                 entity_id=query_id, taxpayer_id=scope.taxpayer_id,
                 details={"disposition": disposition, "status": status})
