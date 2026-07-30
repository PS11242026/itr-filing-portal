"""Post-onboarding identity edits: PAN / Aadhaar corrections re-encrypt,
rebuild blind indexes, stay masked in the audit log."""

import pytest

from itrcore.authz import AccessDenied, mint_scope


def _scope(principal, taxpayer_id):
    return mint_scope(principal, taxpayer_id)


def test_update_pan_rebuilds_lookup_and_mask(partner, taxpayer_id):
    from itrcore.db.repos import taxpayers
    scope = _scope(partner, taxpayer_id)
    taxpayers.update_identity(scope, pan="AAAPZ9999K")
    tp = taxpayers.get_taxpayer(scope)
    assert tp.pan == "AAAPZ9999K"
    assert tp.pan_masked == "XXXXX9999K"
    assert taxpayers.find_by_pan(partner, "AAAPZ9999K") == taxpayer_id
    assert taxpayers.find_by_pan(partner, "AAAPL1234C") is None


def test_update_pan_rejects_duplicate(partner, taxpayer_id):
    from itrcore.db.repos import taxpayers
    other = taxpayers.create_taxpayer(partner, full_name="Sita Devi",
                                      pan="AAAPS5678D")
    with pytest.raises(ValueError, match="already has this PAN"):
        taxpayers.update_identity(_scope(partner, other), pan="AAAPL1234C")
    # updating to your own current PAN is a no-op, not a duplicate
    taxpayers.update_identity(_scope(partner, taxpayer_id),
                              pan="AAAPL1234C")


def test_aadhaar_set_replace_and_clear(partner, taxpayer_id):
    from itrcore.db.repos import taxpayers
    scope = _scope(partner, taxpayer_id)
    assert taxpayers.has_aadhaar(scope) is False
    taxpayers.update_identity(scope, aadhaar="234123412346")
    assert taxpayers.has_aadhaar(scope) is True
    assert taxpayers.get_aadhaar(scope) == "234123412346"
    taxpayers.update_identity(scope, clear_aadhaar=True)
    assert taxpayers.has_aadhaar(scope) is False
    assert taxpayers.get_aadhaar(scope) is None


def test_identity_edit_requires_manager(partner, taxpayer_id):
    from itrcore.db.repos import taxpayers, users
    users.create_user(partner, "juniour", "password1", "Staff")
    staffer = users.authenticate("juniour", "password1").principal
    with pytest.raises(AccessDenied):
        taxpayers.update_identity(_scope(staffer, taxpayer_id),
                                  pan="AAAPZ9999K")


def test_identity_audit_never_stores_full_pan(partner, taxpayer_id):
    from itrcore.db import auditlog
    from itrcore.db.repos import taxpayers
    taxpayers.update_identity(_scope(partner, taxpayer_id),
                              pan="AAAPZ9999K", aadhaar="234123412346")
    entries = [dict(e) for e in auditlog.recent(limit=10)
               if e["action"] == "TAXPAYER_IDENTITY_UPDATE"]
    assert entries, "identity update must be audit-logged"
    blob = str(entries)
    assert "AAAPZ9999K" not in blob
    assert "234123412346" not in blob
    assert "XXXXX9999K" in blob
