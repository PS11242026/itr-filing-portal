"""Bank accounts: encrypted account numbers, blind-index dedupe."""

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection
from itrcore.db.crypto import get_cipher
from itrcore.validation import validate_ifsc


def add_account(scope: TaxpayerScope, *, bank_name: str, account_no: str,
                ifsc: str | None = None, account_type: str = "Savings",
                ownership_share: float = 1.0) -> int:
    bank_name = (bank_name or "").strip()
    account_no = (account_no or "").strip().replace(" ", "")
    if not bank_name or not account_no:
        raise ValueError("bank name and account number are required")
    if ifsc:
        ifsc = validate_ifsc(ifsc)
    cipher = get_cipher()
    bidx = cipher.blind_index(account_no)
    existing = connection.query_one(
        "SELECT id FROM bank_accounts WHERE taxpayer_id=? AND "
        "account_no_bidx=?", (scope.taxpayer_id, bidx))
    if existing:
        raise ValueError("this account already exists for the client")
    aid = connection.execute(
        "INSERT INTO bank_accounts (taxpayer_id, bank_name, account_no_enc,"
        " account_no_bidx, last4, ifsc, account_type, ownership_share,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (scope.taxpayer_id, bank_name, cipher.enc_str(account_no), bidx,
         account_no[-4:], ifsc, account_type, float(ownership_share),
         connection.now()))
    auditlog.log(scope.principal.username, "ACCOUNT_ADD",
                 entity="bank_account", entity_id=aid,
                 taxpayer_id=scope.taxpayer_id,
                 details={"bank": bank_name, "last4": account_no[-4:]})
    return aid


def list_accounts(scope: TaxpayerScope, include_inactive: bool = False):
    sql = ("SELECT id, bank_name, last4, ifsc, account_type,"
           " ownership_share, active FROM bank_accounts WHERE taxpayer_id=?")
    if not include_inactive:
        sql += " AND active=1"
    return connection.query(sql + " ORDER BY id", (scope.taxpayer_id,))


def deactivate(scope: TaxpayerScope, account_id: int) -> None:
    require_staff(scope.principal, "Staff", "Deactivating accounts")
    connection.execute(
        "UPDATE bank_accounts SET active=0 WHERE id=? AND taxpayer_id=?",
        (account_id, scope.taxpayer_id))
    auditlog.log(scope.principal.username, "ACCOUNT_DEACTIVATE",
                 entity="bank_account", entity_id=account_id,
                 taxpayer_id=scope.taxpayer_id)
