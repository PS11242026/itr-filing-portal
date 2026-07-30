"""User accounts: bootstrap, roles, lockout, password flows."""

import pytest

from itrcore.authz import AccessDenied


def test_bootstrap_only_once_and_only_partner(fresh_db):
    from itrcore.db.repos import users
    with pytest.raises(ValueError):
        users.create_user(None, "x", "password1", "Staff")
    users.create_user(None, "partner1", "secret123", "Partner")
    with pytest.raises(PermissionError):
        users.create_user(None, "again", "secret123", "Partner")


def test_role_hierarchy_for_user_creation(partner):
    from itrcore.db.repos import users
    users.create_user(partner, "mgr", "password1", "Manager")
    mgr = users.authenticate("mgr", "password1").principal
    users.create_user(mgr, "staffer", "password1", "Staff")
    with pytest.raises(AccessDenied):
        users.create_user(mgr, "p2", "password1", "Partner")
    staffer = users.authenticate("staffer", "password1").principal
    with pytest.raises(AccessDenied):
        users.create_user(staffer, "s2", "password1", "Staff")


def test_client_account_requires_taxpayer_link(partner, taxpayer_id):
    from itrcore.db.repos import users
    with pytest.raises(ValueError):
        users.create_user(partner, "c_bad", "password1", "Client")
    with pytest.raises(ValueError):
        users.create_user(partner, "s_bad", "password1", "Staff",
                          taxpayer_id=taxpayer_id)
    uid = users.create_user(partner, "c_ok", "password1", "Client",
                            taxpayer_id=taxpayer_id)
    assert uid > 0


def test_lockout_after_failed_attempts(partner):
    from itrcore.db.repos import users
    users.create_user(partner, "victim", "correct-pw1", "Staff")
    for _ in range(users.MAX_FAILED_ATTEMPTS):
        assert users.authenticate("victim", "wrong") is None
    # correct password now rejected because the account is locked
    assert users.authenticate("victim", "correct-pw1") is None


def test_forced_password_change_flow(partner, taxpayer_id):
    from itrcore.db.repos import users
    users.create_user(partner, "newbie", "one-time-pw", "Client",
                      taxpayer_id=taxpayer_id, must_change_pw=True)
    result = users.authenticate("newbie", "one-time-pw")
    assert result.must_change_pw is True
    users.change_password(result.principal, "one-time-pw", "my-own-pw-9")
    result2 = users.authenticate("newbie", "my-own-pw-9")
    assert result2 is not None and result2.must_change_pw is False


def test_reset_password_marks_must_change(partner):
    from itrcore.db.repos import users
    uid = users.create_user(partner, "forgetful", "password1", "Staff")
    users.reset_password(partner, uid, "temp-pw-123")
    result = users.authenticate("forgetful", "temp-pw-123")
    assert result.must_change_pw is True


def test_set_active_deactivates_and_blocks_login(partner):
    from itrcore.db.repos import users
    uid = users.create_user(partner, "leaver", "password1", "Staff")
    users.set_active(partner, uid, False)
    assert users.authenticate("leaver", "password1") is None
    row = next(r for r in users.list_users(partner) if r["id"] == uid)
    assert row["active"] == 0
    users.set_active(partner, uid, True)
    assert users.authenticate("leaver", "password1") is not None


def test_set_active_rejects_unknown_id(partner):
    from itrcore.db.repos import users
    with pytest.raises(ValueError, match="no user with id"):
        users.set_active(partner, 9999, False)


def test_set_active_protects_self(partner):
    from itrcore.db.repos import users
    with pytest.raises(ValueError, match="own account"):
        users.set_active(partner, partner.user_id, False)
    # a second partner CAN be deactivated while the first stays active
    p2 = users.create_user(partner, "partner2", "password1", "Partner")
    users.set_active(partner, p2, False)


def test_last_active_partner_cannot_be_deactivated(partner):
    # partner1's stale session (already deactivated by partner2) must not be
    # able to take down the sole remaining active Partner.
    from itrcore.db.repos import users
    p2 = users.create_user(partner, "partner2", "password1", "Partner")
    partner2 = users.authenticate("partner2", "password1").principal
    users.set_active(partner2, partner.user_id, False)
    with pytest.raises(ValueError, match="last active Partner"):
        users.set_active(partner, p2, False)


def test_find_client_login(partner, taxpayer_id):
    from itrcore.db.repos import users
    assert users.find_client_login(partner, taxpayer_id) is None
    users.create_user(partner, "clientlogin", "password1", "Client",
                      taxpayer_id=taxpayer_id)
    row = users.find_client_login(partner, taxpayer_id)
    assert row is not None and row["username"] == "clientlogin"
    assert row["active"] == 1
