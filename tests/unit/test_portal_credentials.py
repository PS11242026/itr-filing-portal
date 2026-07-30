"""Encrypted vault for clients' ITD portal passwords."""

import pytest

from itrcore.authz import mint_scope
from itrcore.db import connection
from itrcore.db.repos import portal_credentials as creds


def test_save_get_roundtrip(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    assert creds.has_credentials(scope) is False
    creds.save_credentials(scope, password="Secr3t!pw")
    assert creds.has_credentials(scope) is True
    assert creds.get_password(scope) == "Secr3t!pw"


def test_password_encrypted_at_rest(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    creds.save_credentials(scope, password="PlainTextPw123")
    row = connection.query_one(
        "SELECT password_enc FROM portal_credentials WHERE taxpayer_id=?",
        (taxpayer_id,))
    assert b"PlainTextPw123" not in bytes(row["password_enc"])


def test_update_and_delete(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    creds.save_credentials(scope, password="one")
    creds.save_credentials(scope, password="two")
    assert creds.get_password(scope) == "two"
    creds.delete_credentials(scope)
    assert creds.has_credentials(scope) is False
    assert creds.get_password(scope) is None


def test_blank_password_rejected(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    with pytest.raises(ValueError):
        creds.save_credentials(scope, password="   ")


def test_credential_use_is_audit_logged(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    creds.save_credentials(scope, password="pw")
    creds.get_password(scope)
    rows = connection.query(
        "SELECT action FROM audit_log WHERE taxpayer_id=?", (taxpayer_id,))
    actions = {r["action"] for r in rows}
    assert "PORTAL_CRED_SAVE" in actions
    assert "PORTAL_CRED_USE" in actions
