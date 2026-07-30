"""Onboarding income profile (incl. PGBP) drives the document checklist."""

from itrcore.authz import mint_scope
from itrcore.db.repos import checklist, taxpayers


def _profile_items(partner, pan, profile):
    tid = taxpayers.create_taxpayer(partner, full_name="Client", pan=pan,
                                    profile=profile)
    scope = mint_scope(partner, tid)
    prof = taxpayers.get_taxpayer(scope).profile
    return {i.key for i in checklist.applicable_items(prof)}


def test_44ada_profile_requires_receipts_summary(partner):
    items = _profile_items(partner, "AAAPB1234C",
                           {"has_profession_44ada": True})
    assert "profession_44ada_summary" in items
    assert "goods_carriage_register" not in items


def test_44ae_and_books_profiles_require_their_docs(partner):
    items = _profile_items(partner, "AAAPC1234C",
                           {"has_goods_carriage_44ae": True,
                            "has_business_books": True})
    assert "goods_carriage_register" in items
    assert "books_of_accounts" in items


def test_salary_only_profile_has_no_business_items(partner):
    items = _profile_items(partner, "AAAPD1234C", {"has_salary": True})
    assert "form16" in items
    assert not any(k.startswith(("business_", "profession_", "goods_",
                                 "books_")) for k in items)
