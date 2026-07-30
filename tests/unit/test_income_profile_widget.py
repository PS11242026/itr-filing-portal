"""The grouped income-profile widget must keep the flag contract stable:
same 16 keys the checklist and computation-tab highlighting rely on."""

from itrapp.widgets.income_profile import ALL_FLAGS, PROFILE_GROUPS

EXPECTED = {
    "has_salary", "has_let_out_property", "has_home_loan",
    "has_business_44ad", "has_profession_44ada", "has_goods_carriage_44ae",
    "has_business_books", "has_property_sale", "has_securities_cg",
    "has_interest_income", "has_dividend_income", "has_gifts",
    "wants_old_regime", "is_director", "has_unlisted_shares",
    "has_foreign_assets"}


def test_flag_set_unchanged_and_unique():
    assert set(ALL_FLAGS) == EXPECTED
    assert len(ALL_FLAGS) == len(EXPECTED)          # no duplicates


def test_business_overhead_clubs_its_subheads():
    biz = next(flags for title, flags in PROFILE_GROUPS
               if "business or profession" in title.casefold())
    assert [f for f, _ in biz] == [
        "has_business_44ad", "has_profession_44ada",
        "has_goods_carriage_44ae", "has_business_books"]


def test_every_group_is_an_income_head_or_filing_flags():
    titles = [t for t, _ in PROFILE_GROUPS]
    assert len(titles) == 6                          # 5 heads + flags
    assert any("house property" in t.casefold() for t in titles)
    assert any("capital gains" in t.casefold() for t in titles)
    assert any("other sources" in t.casefold() for t in titles)
