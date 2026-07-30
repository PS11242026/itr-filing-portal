"""Hash-chained audit log: integrity + append-only triggers."""

import sqlite3

import pytest

from itrcore.db import auditlog, connection


def test_chain_verifies(fresh_db):
    auditlog.log("tester", "ACTION_ONE", details={"a": 1})
    auditlog.log("tester", "ACTION_TWO")
    auditlog.log(None, "ACTION_THREE", entity="x", entity_id=5)
    ok, bad = auditlog.verify_chain()
    assert ok and bad is None


def test_update_and_delete_blocked_by_triggers(fresh_db):
    auditlog.log("tester", "SOMETHING")
    with connection.read() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE audit_log SET action='FORGED' WHERE id=1")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM audit_log WHERE id=1")


def test_tampering_breaks_chain(fresh_db):
    auditlog.log("tester", "A")
    auditlog.log("tester", "B")
    with connection.read() as conn:
        conn.execute("DROP TRIGGER audit_no_update")  # simulate an attacker
        conn.execute("UPDATE audit_log SET action='FORGED' WHERE id=1")
    ok, bad = auditlog.verify_chain()
    assert not ok
    assert bad == 1
