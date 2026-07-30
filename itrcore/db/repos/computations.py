"""Computation persistence: draft inputs + versioned result snapshots."""

import json

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection


def save_inputs(scope: TaxpayerScope, ay: str, inputs_dict: dict) -> None:
    require_staff(scope.principal, "Staff", "Editing computation inputs")
    connection.execute(
        "INSERT INTO computation_inputs (taxpayer_id, ay, inputs_json,"
        " updated_by, updated_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT (taxpayer_id, ay) DO UPDATE SET"
        " inputs_json=excluded.inputs_json, updated_by=excluded.updated_by,"
        " updated_at=excluded.updated_at",
        (scope.taxpayer_id, ay, json.dumps(inputs_dict),
         scope.principal.username, connection.now()))
    auditlog.log(scope.principal.username, "COMP_INPUTS_SAVE",
                 entity="computation_inputs", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details={"ay": ay})


def load_inputs(scope: TaxpayerScope, ay: str) -> dict | None:
    row = connection.query_one(
        "SELECT inputs_json FROM computation_inputs"
        " WHERE taxpayer_id=? AND ay=?", (scope.taxpayer_id, ay))
    return json.loads(row["inputs_json"]) if row else None


def save_snapshot(scope: TaxpayerScope, ay: str, inputs_dict: dict,
                  result_dict: dict, *, engine_version: str,
                  config_hash: str, inputs_digest: str) -> int:
    require_staff(scope.principal, "Staff", "Saving computations")
    with connection.tx() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) AS v FROM computations"
            " WHERE taxpayer_id=? AND ay=?",
            (scope.taxpayer_id, ay)).fetchone()
        version = int(row["v"]) + 1
        conn.execute(
            "INSERT INTO computations (taxpayer_id, ay, version_no,"
            " inputs_json, result_json, engine_version, config_hash,"
            " inputs_digest, status, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,'draft',?,?)",
            (scope.taxpayer_id, ay, version, json.dumps(inputs_dict),
             json.dumps(result_dict), engine_version, config_hash,
             inputs_digest, scope.principal.username, connection.now()))
    auditlog.log(scope.principal.username, "COMP_SNAPSHOT",
                 entity="computation", entity_id=version,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "version": version,
                          "inputs_digest": inputs_digest})
    return version


def list_snapshots(scope: TaxpayerScope, ay: str):
    return connection.query(
        "SELECT id, version_no, status, engine_version, config_hash,"
        " inputs_digest, created_by, created_at FROM computations"
        " WHERE taxpayer_id=? AND ay=? ORDER BY version_no DESC",
        (scope.taxpayer_id, ay))


def get_snapshot(scope: TaxpayerScope, ay: str, version_no: int):
    row = connection.query_one(
        "SELECT * FROM computations WHERE taxpayer_id=? AND ay=?"
        " AND version_no=?", (scope.taxpayer_id, ay, version_no))
    if row is None:
        raise ValueError("computation version not found")
    return row


def finalize(scope: TaxpayerScope, ay: str, version_no: int) -> None:
    require_staff(scope.principal, "Manager", "Finalising computations")
    get_snapshot(scope, ay, version_no)
    with connection.tx() as conn:
        conn.execute(
            "UPDATE computations SET status='superseded'"
            " WHERE taxpayer_id=? AND ay=? AND status='final'",
            (scope.taxpayer_id, ay))
        conn.execute(
            "UPDATE computations SET status='final'"
            " WHERE taxpayer_id=? AND ay=? AND version_no=?",
            (scope.taxpayer_id, ay, version_no))
    auditlog.log(scope.principal.username, "COMP_FINALIZE",
                 entity="computation", entity_id=version_no,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "version": version_no})
