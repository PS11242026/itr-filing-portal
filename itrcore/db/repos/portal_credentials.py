"""Encrypted vault for clients' government-portal passwords.

Stored so the firm can pull AIS/TIS/26AS on a consenting client's behalf.
The password is Fernet-encrypted at rest (same cipher as PAN/Aadhaar); it is
never written to the audit log, and every decrypt (a credential *use*) is
audit-logged so there is a trail of who accessed a client's login and when.
"""

import json

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection
from itrcore.db.crypto import get_cipher

VALID_PORTALS = ("itd_efiling", "traces")


def save_credentials(scope: TaxpayerScope, *, password: str,
                     portal: str = "itd_efiling",
                     extra: dict | None = None) -> None:
    require_staff(scope.principal, "Staff", "Saving portal credentials")
    if portal not in VALID_PORTALS:
        raise ValueError(f"unknown portal {portal!r}")
    if not (password or "").strip():
        raise ValueError("password is required")
    cipher = get_cipher()
    connection.execute(
        "INSERT INTO portal_credentials (taxpayer_id, portal, password_enc,"
        " extra_json, updated_by, updated_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT (taxpayer_id, portal) DO UPDATE SET"
        " password_enc=excluded.password_enc, extra_json=excluded.extra_json,"
        " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (scope.taxpayer_id, portal, cipher.enc_str(password),
         json.dumps(extra or {}), scope.principal.username, connection.now()))
    auditlog.log(scope.principal.username, "PORTAL_CRED_SAVE",
                 entity="portal_credentials", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details={"portal": portal})


def has_credentials(scope: TaxpayerScope,
                    portal: str = "itd_efiling") -> bool:
    return connection.query_one(
        "SELECT 1 AS x FROM portal_credentials WHERE taxpayer_id=?"
        " AND portal=?", (scope.taxpayer_id, portal)) is not None


def get_password(scope: TaxpayerScope,
                 portal: str = "itd_efiling") -> str | None:
    """Decrypt and return the stored password. Audit-logged as a use."""
    require_staff(scope.principal, "Staff", "Using portal credentials")
    row = connection.query_one(
        "SELECT password_enc FROM portal_credentials WHERE taxpayer_id=?"
        " AND portal=?", (scope.taxpayer_id, portal))
    if row is None:
        return None
    auditlog.log(scope.principal.username, "PORTAL_CRED_USE",
                 entity="portal_credentials", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details={"portal": portal})
    return get_cipher().dec_str(row["password_enc"])


def delete_credentials(scope: TaxpayerScope,
                       portal: str = "itd_efiling") -> None:
    require_staff(scope.principal, "Staff", "Deleting portal credentials")
    connection.execute(
        "DELETE FROM portal_credentials WHERE taxpayer_id=? AND portal=?",
        (scope.taxpayer_id, portal))
    auditlog.log(scope.principal.username, "PORTAL_CRED_DELETE",
                 entity="portal_credentials", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details={"portal": portal})
