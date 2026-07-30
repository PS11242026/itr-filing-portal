"""Filing records: generated JSON → outbox file → status/ack tracking."""

import hashlib
import json

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection

_STATUS_FLOW = {"generated": {"uploaded", "void"},
                "uploaded": {"everified", "void"},
                "everified": {"processed"},
                "processed": set(), "void": set()}


def create_filing(scope: TaxpayerScope, *, ay: str, form: str,
                  filing_type: str, computation_id: int,
                  json_dict: dict, schema_version: str) -> tuple[int, str]:
    require_staff(scope.principal, "Staff", "Generating returns")
    payload = json.dumps(json_dict, ensure_ascii=False, indent=1)
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    outbox = connection.data_dir() / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    with connection.tx() as conn:
        cur = conn.execute(
            "INSERT INTO filings (taxpayer_id, ay, form, filing_type,"
            " computation_id, schema_version, json_sha256, json_path,"
            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (scope.taxpayer_id, ay, form, filing_type, computation_id,
             schema_version, sha, "", scope.principal.username,
             connection.now()))
        fid = cur.lastrowid
        fname = (f"{form.replace('-', '')}_TP{scope.taxpayer_id}_"
                 f"AY{ay.replace('-', '')}_F{fid}.json")
        path = outbox / fname
        path.write_text(payload, encoding="utf-8")
        conn.execute("UPDATE filings SET json_path=? WHERE id=?",
                     (str(path), fid))
    auditlog.log(scope.principal.username, "FILING_GENERATE",
                 entity="filing", entity_id=fid,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "form": form, "sha256": sha,
                          "schema_version": schema_version})
    return fid, str(path)


def list_filings(scope: TaxpayerScope, ay: str | None = None):
    sql = "SELECT * FROM filings WHERE taxpayer_id=?"
    params: list = [scope.taxpayer_id]
    if ay:
        sql += " AND ay=?"
        params.append(ay)
    sql += " ORDER BY id DESC"
    return connection.query(sql, tuple(params))


def get_filing(scope: TaxpayerScope, filing_id: int):
    row = connection.query_one(
        "SELECT * FROM filings WHERE id=? AND taxpayer_id=?",
        (filing_id, scope.taxpayer_id))
    if row is None:
        raise ValueError("filing not found")
    return row


def update_status(scope: TaxpayerScope, filing_id: int, new_status: str,
                  *, ack_no: str | None = None,
                  filed_on: str | None = None,
                  everified_on: str | None = None) -> None:
    require_staff(scope.principal, "Staff", "Updating filing status")
    row = get_filing(scope, filing_id)
    allowed = _STATUS_FLOW.get(row["status"], set())
    if new_status not in allowed:
        raise ValueError(f"cannot move filing from {row['status']} to "
                         f"{new_status}")
    if new_status == "uploaded" and not (ack_no or "").strip():
        raise ValueError("acknowledgement number is required when marking "
                         "uploaded")
    connection.execute(
        "UPDATE filings SET status=?,"
        " ack_no=COALESCE(?, ack_no),"
        " filed_on=COALESCE(?, filed_on),"
        " everified_on=COALESCE(?, everified_on) WHERE id=?",
        (new_status, ack_no, filed_on, everified_on, filing_id))
    auditlog.log(scope.principal.username, "FILING_STATUS",
                 entity="filing", entity_id=filing_id,
                 taxpayer_id=scope.taxpayer_id,
                 details={"status": new_status, "ack_no": ack_no})
