"""Append-only, hash-chained audit log.

Every row's row_hash covers the previous row's hash plus its own canonical
fields, so any in-place tampering breaks the chain (verify_chain()).
UPDATE/DELETE are additionally blocked by triggers in the schema.
"""

import hashlib
import json

from itrcore.db import connection

GENESIS = "0" * 64


def _row_hash(prev_hash: str, at: str, username: str, action: str,
              entity: str, entity_id: str, taxpayer_id: str,
              details_json: str) -> str:
    payload = "|".join([prev_hash, at, username, action, entity, entity_id,
                        taxpayer_id, details_json])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def log(username: str | None, action: str, entity: str | None = None,
        entity_id=None, taxpayer_id: int | None = None,
        details: dict | None = None) -> int:
    details_json = (json.dumps(details, ensure_ascii=False, sort_keys=True)
                    if details else "")
    with connection.tx() as conn:
        last = conn.execute(
            "SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev = last["row_hash"] if last else GENESIS
        at = connection.now()
        row_hash = _row_hash(prev, at, username or "", action, entity or "",
                             str(entity_id or ""), str(taxpayer_id or ""),
                             details_json)
        cur = conn.execute(
            "INSERT INTO audit_log (at, username, action, entity, entity_id,"
            " taxpayer_id, details, prev_hash, row_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (at, username, action, entity, str(entity_id) if entity_id
             is not None else None, taxpayer_id, details_json or None,
             prev, row_hash))
        return cur.lastrowid


def verify_chain() -> tuple[bool, int | None]:
    """Recompute the chain; returns (ok, first_bad_row_id)."""
    prev = GENESIS
    with connection.read() as conn:
        for row in conn.execute("SELECT * FROM audit_log ORDER BY id"):
            expected = _row_hash(prev, row["at"], row["username"] or "",
                                 row["action"], row["entity"] or "",
                                 row["entity_id"] or "",
                                 str(row["taxpayer_id"] or ""),
                                 row["details"] or "")
            if row["prev_hash"] != prev or row["row_hash"] != expected:
                return False, row["id"]
            prev = row["row_hash"]
    return True, None


def recent(limit: int = 100, taxpayer_id: int | None = None):
    if taxpayer_id is not None:
        return connection.query(
            "SELECT * FROM audit_log WHERE taxpayer_id=? "
            "ORDER BY id DESC LIMIT ?", (taxpayer_id, limit))
    return connection.query(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
