"""Reconciliation orchestration: items + resolutions + blockers."""

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection
from itrcore.db.repos import computations as comp_repo
from itrcore.recon import ais_recon, tds as tds_recon


def resolutions(scope: TaxpayerScope, ay: str) -> dict[str, dict]:
    return {r["item_key"]: dict(r) for r in connection.query(
        "SELECT * FROM recon_resolutions WHERE taxpayer_id=? AND ay=?",
        (scope.taxpayer_id, ay))}


def resolve(scope: TaxpayerScope, ay: str, *, kind: str, item_key: str,
            resolution: str, remark: str) -> None:
    require_staff(scope.principal, "Staff", "Resolving reconciliation items")
    if resolution == "justified" and not (remark or "").strip():
        raise ValueError("a remark is required to justify a difference")
    connection.execute(
        "INSERT INTO recon_resolutions (taxpayer_id, ay, kind, item_key,"
        " resolution, remark, resolved_by, resolved_at)"
        " VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT (taxpayer_id, ay, kind, item_key) DO UPDATE SET"
        " resolution=excluded.resolution, remark=excluded.remark,"
        " resolved_by=excluded.resolved_by, resolved_at=excluded.resolved_at",
        (scope.taxpayer_id, ay, kind, item_key, resolution, remark,
         scope.principal.username, connection.now()))
    auditlog.log(scope.principal.username, "RECON_RESOLVE",
                 entity="recon", entity_id=item_key,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "resolution": resolution,
                          "remark": remark})


def clear_resolution(scope: TaxpayerScope, ay: str, kind: str,
                     item_key: str) -> None:
    require_staff(scope.principal, "Staff", "Reopening reconciliation items")
    connection.execute(
        "DELETE FROM recon_resolutions WHERE taxpayer_id=? AND ay=?"
        " AND kind=? AND item_key=?",
        (scope.taxpayer_id, ay, kind, item_key))


def status(scope: TaxpayerScope, ay: str) -> dict:
    """All recon items with their resolution state."""
    inputs = comp_repo.load_inputs(scope, ay)
    res = resolutions(scope, ay)
    tds_items = tds_recon.compare(scope, ay, inputs or {})
    ais_items = ais_recon.compare(scope, ay, inputs or {})
    return {"inputs_present": inputs is not None,
            "tds": [(i, res.get(i.key)) for i in tds_items],
            "ais": [(i, res.get(i.key)) for i in ais_items]}


def blockers(scope: TaxpayerScope, ay: str) -> list[str]:
    """Unresolved mismatches. claim_exceeds_26as cannot be waived by a
    mere remark being absent — it still needs an explicit resolution."""
    state = status(scope, ay)
    out: list[str] = []
    for item, resolution in state["tds"]:
        if item.status == "matched" or resolution:
            continue
        if item.status == "claim_exceeds_26as":
            out.append(f"TDS claimed for TAN {item.tan} "
                       f"(₹{item.claimed_inr:,}) exceeds Form 26AS "
                       f"(₹{item.as26_inr:,})")
        else:
            out.append(f"Form 26AS shows TDS ₹{item.as26_inr:,} from "
                       f"{item.deductor or item.tan} not claimed in the "
                       "return — unreported income?")
    for item, resolution in state["ais"]:
        if item.status == "matched" or resolution:
            continue
        out.append(f"AIS {item.label}: AIS ₹{item.ais_inr:,} vs books "
                   f"₹{item.books_inr:,}")
    return out
