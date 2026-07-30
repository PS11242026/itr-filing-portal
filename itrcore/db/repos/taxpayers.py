"""Taxpayer records: PII encrypted at rest with blind-index lookup."""

import json

from itrcore.authz import Principal, TaxpayerScope, require_staff
from itrcore.db import auditlog, connection
from itrcore.db.crypto import get_cipher
from itrcore.models import Taxpayer, TaxpayerSummary
from itrcore.validation import validate_aadhaar, validate_pan


def _mask_pan(pan: str) -> str:
    return "XXXXX" + pan[5:]


def create_taxpayer(principal: Principal, *, full_name: str, pan: str,
                    aadhaar: str | None = None, dob: str | None = None,
                    email: str | None = None, mobile: str | None = None,
                    address: str | None = None,
                    resident_status: str = "Resident",
                    profile: dict | None = None) -> int:
    require_staff(principal, "Staff", "Creating taxpayers")
    full_name = (full_name or "").strip()
    if not full_name:
        raise ValueError("full name is required")
    pan = validate_pan(pan)
    cipher = get_cipher()
    pan_bidx = cipher.blind_index(pan)
    if connection.query_one("SELECT id FROM taxpayers WHERE pan_bidx=?",
                            (pan_bidx,)):
        raise ValueError("a taxpayer with this PAN already exists")
    aadhaar_enc = aadhaar_bidx = None
    if aadhaar:
        aadhaar = validate_aadhaar(aadhaar)
        aadhaar_enc = cipher.enc_str(aadhaar)
        aadhaar_bidx = cipher.blind_index(aadhaar)
    tid = connection.execute(
        "INSERT INTO taxpayers (full_name, pan_enc, pan_bidx, pan_masked,"
        " aadhaar_enc, aadhaar_bidx, dob, email, mobile, address,"
        " resident_status, profile_json, created_by, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (full_name, cipher.enc_str(pan), pan_bidx, _mask_pan(pan),
         aadhaar_enc, aadhaar_bidx, dob, email, mobile, address,
         resident_status, json.dumps(profile or {}), principal.username,
         connection.now()))
    auditlog.log(principal.username, "TAXPAYER_CREATE", entity="taxpayer",
                 entity_id=tid, taxpayer_id=tid,
                 details={"name": full_name})
    return tid


def get_taxpayer(scope: TaxpayerScope) -> Taxpayer:
    row = connection.query_one("SELECT * FROM taxpayers WHERE id=?",
                               (scope.taxpayer_id,))
    if row is None:
        raise ValueError("taxpayer not found")
    cipher = get_cipher()
    return Taxpayer(
        id=row["id"], full_name=row["full_name"],
        pan=cipher.dec_str(row["pan_enc"]), pan_masked=row["pan_masked"],
        dob=row["dob"], email=row["email"], mobile=row["mobile"],
        address=row["address"], resident_status=row["resident_status"],
        profile=json.loads(row["profile_json"] or "{}"),
        status=row["status"])


def get_aadhaar(scope: TaxpayerScope) -> str | None:
    """Separate accessor so casual reads never decrypt Aadhaar."""
    row = connection.query_one("SELECT aadhaar_enc FROM taxpayers WHERE id=?",
                               (scope.taxpayer_id,))
    if row is None or row["aadhaar_enc"] is None:
        return None
    auditlog.log(scope.principal.username, "AADHAAR_REVEAL",
                 entity="taxpayer", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id)
    return get_cipher().dec_str(row["aadhaar_enc"])


def update_profile(scope: TaxpayerScope, profile: dict) -> None:
    require_staff(scope.principal, "Staff", "Editing taxpayer profile")
    connection.execute("UPDATE taxpayers SET profile_json=? WHERE id=?",
                       (json.dumps(profile), scope.taxpayer_id))
    auditlog.log(scope.principal.username, "TAXPAYER_PROFILE_UPDATE",
                 entity="taxpayer", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details={"profile": profile})


def update_contact(scope: TaxpayerScope, *, full_name: str | None = None,
                   dob: str | None = None, email: str | None = None,
                   mobile: str | None = None, address: str | None = None,
                   resident_status: str | None = None) -> None:
    require_staff(scope.principal, "Staff", "Editing taxpayer details")
    current = connection.query_one("SELECT * FROM taxpayers WHERE id=?",
                                   (scope.taxpayer_id,))
    if current is None:
        raise ValueError("taxpayer not found")
    connection.execute(
        "UPDATE taxpayers SET full_name=?, dob=?, email=?, mobile=?,"
        " address=?, resident_status=? WHERE id=?",
        (full_name if full_name is not None else current["full_name"],
         dob if dob is not None else current["dob"],
         email if email is not None else current["email"],
         mobile if mobile is not None else current["mobile"],
         address if address is not None else current["address"],
         resident_status if resident_status is not None
         else current["resident_status"],
         scope.taxpayer_id))
    auditlog.log(scope.principal.username, "TAXPAYER_UPDATE",
                 entity="taxpayer", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id)


def update_identity(scope: TaxpayerScope, *, pan: str | None = None,
                    aadhaar: str | None = None,
                    clear_aadhaar: bool = False) -> None:
    """Correct PAN / Aadhaar after onboarding (typo fixes). Re-encrypts and
    rebuilds the blind indexes; only masked values reach the audit log."""
    require_staff(scope.principal, "Manager", "Editing taxpayer identity")
    current = connection.query_one("SELECT id FROM taxpayers WHERE id=?",
                                   (scope.taxpayer_id,))
    if current is None:
        raise ValueError("taxpayer not found")
    cipher = get_cipher()
    sets: list[str] = []
    params: list = []
    details: dict = {}
    if pan is not None:
        pan = validate_pan(pan)
        pan_bidx = cipher.blind_index(pan)
        dup = connection.query_one(
            "SELECT id FROM taxpayers WHERE pan_bidx=? AND id != ?",
            (pan_bidx, scope.taxpayer_id))
        if dup:
            raise ValueError("another taxpayer already has this PAN")
        sets += ["pan_enc=?", "pan_bidx=?", "pan_masked=?"]
        params += [cipher.enc_str(pan), pan_bidx, _mask_pan(pan)]
        details["pan_masked"] = _mask_pan(pan)
    if clear_aadhaar:
        sets += ["aadhaar_enc=NULL", "aadhaar_bidx=NULL"]
        details["aadhaar"] = "cleared"
    elif aadhaar is not None:
        aadhaar = validate_aadhaar(aadhaar)
        sets += ["aadhaar_enc=?", "aadhaar_bidx=?"]
        params += [cipher.enc_str(aadhaar), cipher.blind_index(aadhaar)]
        details["aadhaar"] = "updated"
    if not sets:
        return
    connection.execute(
        f"UPDATE taxpayers SET {', '.join(sets)} WHERE id=?",
        (*params, scope.taxpayer_id))
    auditlog.log(scope.principal.username, "TAXPAYER_IDENTITY_UPDATE",
                 entity="taxpayer", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details=details)


def has_aadhaar(scope: TaxpayerScope) -> bool:
    """Whether Aadhaar is on file — no decrypt, no reveal audit entry."""
    row = connection.query_one(
        "SELECT aadhaar_enc IS NOT NULL AS present FROM taxpayers"
        " WHERE id=?", (scope.taxpayer_id,))
    return bool(row and row["present"])


def find_by_pan(principal: Principal, pan: str) -> int | None:
    require_staff(principal, "Staff", "PAN lookup")
    pan = validate_pan(pan)
    row = connection.query_one("SELECT id FROM taxpayers WHERE pan_bidx=?",
                               (get_cipher().blind_index(pan),))
    return row["id"] if row else None


def list_taxpayers(principal: Principal,
                   include_archived: bool = False) -> list[TaxpayerSummary]:
    require_staff(principal, "Staff", "Listing taxpayers")
    sql = ("SELECT id, full_name, pan_masked, status FROM taxpayers "
           + ("" if include_archived else "WHERE status='active' ")
           + "ORDER BY full_name")
    return [TaxpayerSummary(r["id"], r["full_name"], r["pan_masked"],
                            r["status"])
            for r in connection.query(sql)]


def set_status(scope: TaxpayerScope, status: str) -> None:
    require_staff(scope.principal, "Manager", "Archiving taxpayers")
    if status not in ("active", "archived"):
        raise ValueError("invalid status")
    connection.execute("UPDATE taxpayers SET status=? WHERE id=?",
                       (status, scope.taxpayer_id))
    auditlog.log(scope.principal.username, "TAXPAYER_SET_STATUS",
                 entity="taxpayer", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details={"status": status})
