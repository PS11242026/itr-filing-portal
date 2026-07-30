"""TaxpayerScope: the isolation boundary."""

import pytest

from itrcore.authz import (AccessDenied, Principal, TaxpayerScope,
                           mint_scope)


def test_scope_cannot_be_forged():
    p = Principal(1, "eve", "Client", taxpayer_id=7)
    with pytest.raises(AccessDenied):
        TaxpayerScope(taxpayer_id=8, principal=p)
    with pytest.raises(AccessDenied):
        TaxpayerScope(taxpayer_id=8, principal=p, proof=object())


def test_staff_can_scope_to_any_taxpayer(partner):
    scope = mint_scope(partner, 42)
    assert scope.taxpayer_id == 42


def test_client_can_scope_to_own_taxpayer(client_principal, taxpayer_id):
    scope = mint_scope(client_principal, taxpayer_id)
    assert scope.taxpayer_id == taxpayer_id


def test_client_cross_taxpayer_denied_and_audited(client_principal,
                                                  taxpayer_id):
    from itrcore.db import connection
    with pytest.raises(AccessDenied):
        mint_scope(client_principal, taxpayer_id + 1)
    row = connection.query_one(
        "SELECT * FROM audit_log WHERE action='SCOPE_DENIED' "
        "ORDER BY id DESC LIMIT 1")
    assert row is not None
    assert row["username"] == "client1"
