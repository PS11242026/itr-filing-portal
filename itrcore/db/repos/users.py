"""User accounts: scrypt password hashing (pattern lifted from the
MahaTransco audit tool), roles, lockout, first-run bootstrap."""

import hashlib
import hmac
import os
from datetime import datetime, timedelta

from itrcore import demo
from itrcore.authz import ALL_ROLES, Principal, require_staff
from itrcore.db import auditlog, connection
from itrcore.models import AuthUser

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=64)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.scrypt(password.encode("utf-8"),
                        salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(dk.hex(), dk_hex)


def user_count() -> int:
    row = connection.query_one("SELECT COUNT(*) AS c FROM users")
    return int(row["c"]) if row else 0


def create_user(principal: Principal | None, username: str, password: str,
                role: str, taxpayer_id: int | None = None,
                must_change_pw: bool = False) -> int:
    """principal=None is allowed only for the first-run Partner bootstrap."""
    if principal is None:
        if user_count() != 0:
            raise PermissionError("bootstrap only allowed on an empty system")
        if role != "Partner":
            raise ValueError("the first account must be a Partner")
    else:
        require_staff(principal, "Manager", "Creating users")
        if role == "Partner":
            require_staff(principal, "Partner", "Creating a Partner account")

    username = (username or "").strip()
    if not username:
        raise ValueError("username is required")
    if role not in ALL_ROLES:
        raise ValueError(f"role must be one of {ALL_ROLES}")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} "
                         "characters")
    if (role == "Client") != (taxpayer_id is not None):
        raise ValueError("Client accounts (and only Client accounts) must be "
                         "linked to a taxpayer")
    if connection.query_one("SELECT id FROM users WHERE username=?",
                            (username,)):
        raise ValueError("username already exists")

    uid = connection.execute(
        "INSERT INTO users (username, pw_hash, role, taxpayer_id, active,"
        " must_change_pw, created_at) VALUES (?,?,?,?,1,?,?)",
        (username, hash_password(password), role, taxpayer_id,
         1 if must_change_pw else 0, connection.now()))
    auditlog.log(principal.username if principal else username,
                 "USER_CREATE", entity="user", entity_id=uid,
                 taxpayer_id=taxpayer_id, details={"role": role,
                                                   "username": username})
    return uid


def authenticate(username: str, password: str) -> AuthUser | None:
    row = connection.query_one(
        "SELECT * FROM users WHERE username=? AND active=1",
        ((username or "").strip(),))
    if row is None:
        auditlog.log(username, "LOGIN_FAILED", details={"reason": "unknown"})
        return None

    now = datetime.now()
    if row["locked_until"]:
        try:
            locked_until = datetime.fromisoformat(row["locked_until"])
        except ValueError:
            locked_until = now
        if now < locked_until:
            auditlog.log(username, "LOGIN_LOCKED",
                         details={"until": row["locked_until"]})
            return None

    if verify_password(password or "", row["pw_hash"]):
        connection.execute(
            "UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?",
            (row["id"],))
        auditlog.log(username, "LOGIN", taxpayer_id=row["taxpayer_id"])
        principal = Principal(user_id=row["id"], username=row["username"],
                              role=row["role"],
                              taxpayer_id=row["taxpayer_id"])
        return AuthUser(principal=principal,
                        must_change_pw=bool(row["must_change_pw"]))

    failed = row["failed_attempts"] + 1
    locked_until = None
    if failed >= MAX_FAILED_ATTEMPTS:
        locked_until = (now + timedelta(minutes=LOCKOUT_MINUTES)
                        ).isoformat(timespec="seconds")
        failed = 0
    connection.execute(
        "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
        (failed, locked_until, row["id"]))
    auditlog.log(username, "LOGIN_FAILED",
                 details={"locked": bool(locked_until)})
    return None


def change_password(principal: Principal, old_password: str,
                    new_password: str) -> None:
    """Self-service password change (also clears must_change_pw)."""
    demo.forbid_in_demo("Changing passwords")
    row = connection.query_one("SELECT * FROM users WHERE id=? AND active=1",
                               (principal.user_id,))
    if row is None or not verify_password(old_password or "", row["pw_hash"]):
        raise ValueError("current password is incorrect")
    if len(new_password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} "
                         "characters")
    connection.execute(
        "UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?",
        (hash_password(new_password), principal.user_id))
    auditlog.log(principal.username, "PASSWORD_CHANGE", entity="user",
                 entity_id=principal.user_id)


def reset_password(principal: Principal, user_id: int,
                   new_password: str) -> None:
    """Staff reset (target must change it on next login)."""
    require_staff(principal, "Manager", "Resetting passwords")
    demo.forbid_in_demo("Resetting passwords")
    if len(new_password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} "
                         "characters")
    connection.execute(
        "UPDATE users SET pw_hash=?, must_change_pw=1, failed_attempts=0,"
        " locked_until=NULL WHERE id=?",
        (hash_password(new_password), user_id))
    auditlog.log(principal.username, "PASSWORD_RESET", entity="user",
                 entity_id=user_id)


def set_active(principal: Principal, user_id: int, active: bool) -> None:
    require_staff(principal, "Partner", "Activating/deactivating users")
    demo.forbid_in_demo("Activating/deactivating users")
    row = connection.query_one(
        "SELECT id, username, role, active FROM users WHERE id=?",
        (user_id,))
    if row is None:
        raise ValueError(f"no user with id {user_id}")
    if not active:
        if user_id == principal.user_id:
            raise ValueError("you cannot deactivate your own account")
        if row["role"] == "Partner":
            others = connection.query_one(
                "SELECT COUNT(*) AS c FROM users WHERE role='Partner'"
                " AND active=1 AND id != ?", (user_id,))
            if int(others["c"]) == 0:
                raise ValueError("cannot deactivate the last active Partner "
                                 "— the firm would be locked out")
    connection.execute("UPDATE users SET active=? WHERE id=?",
                       (1 if active else 0, user_id))
    auditlog.log(principal.username, "USER_SET_ACTIVE", entity="user",
                 entity_id=user_id,
                 details={"active": active, "username": row["username"]})


def list_users(principal: Principal) -> list:
    require_staff(principal, "Manager", "Listing users")
    return connection.query(
        "SELECT id, username, role, taxpayer_id, active, must_change_pw,"
        " locked_until, created_at FROM users ORDER BY id")


def find_client_login(principal: Principal, taxpayer_id: int):
    """The Client-role login linked to a taxpayer (row or None)."""
    require_staff(principal, "Staff", "Looking up client logins")
    return connection.query_one(
        "SELECT id, username, active, must_change_pw FROM users"
        " WHERE role='Client' AND taxpayer_id=?", (taxpayer_id,))
