"""Chapter VI — set off and carry forward of losses (Sec 70–74)."""

from datetime import date

import pytest

from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, BFLossEntry,
                               BroughtForwardLosses, CGEntry, HouseProperty,
                               OtherSources, RegularBusiness, SalaryIncome)


def _run(**kw):
    return compute_assessment(AssessmentInputs(
        ay="2026-27", regime_election="new", filing_date=date(2026, 7, 20),
        **kw)).new


def _cf(result, kind):
    return sum(r.carried_forward for r in result.loss_table if r.kind == kind)


def test_business_loss_never_touches_salary():
    """Sec 71(2A). The whole point of the F&O case: a trading loss may eat
    other sources and capital gains, but salary is untouchable."""
    r = _run(salaries=(SalaryIncome(employer_name="Acme",
                                    gross_salary=2000000),),
             regular_businesses=(RegularBusiness(net_profit_before_tax=
                                                 -500000),),
             other_sources=OtherSources(fd_interest=100000))
    assert r.gti == 1925000              # salary only, no interest left
    assert _cf(r, "business") == 400000   # 5L − 1L absorbed by interest


def test_speculation_loss_is_ring_fenced():
    """Sec 73(1): a speculation loss finds no home outside speculation."""
    r = _run(other_sources=OtherSources(fd_interest=300000),
             regular_businesses=(
                 RegularBusiness(name="Intraday", net_profit_before_tax=-50000,
                                 is_speculative=True),))
    assert r.gti == 300000               # interest survives intact
    assert _cf(r, "speculation") == 50000


def test_ordinary_loss_may_be_absorbed_by_speculation_profit():
    """The reverse direction IS allowed — both are the same head (Sec 70)."""
    r = _run(regular_businesses=(
        RegularBusiness(name="Trading", net_profit_before_tax=-40000),
        RegularBusiness(name="Intraday", net_profit_before_tax=60000,
                        is_speculative=True)))
    assert r.gti == 20000
    assert r.speculation_income == 20000
    assert _cf(r, "business") == 0


def test_house_property_loss_lapses_under_115bac():
    """Let-out, so 24(b) interest IS allowed under 115BAC and a loss of
    ₹4,30,000 arises (70,000 NAV − 5,00,000 interest). But 115BAC(2)(i)(B)
    bars it from any other head and 115BAC(2)(ii) deems it already given
    full effect — so it neither reduces salary nor survives the year."""
    r = _run(salaries=(SalaryIncome(employer_name="Acme",
                                    gross_salary=1500000),),
             house_properties=(HouseProperty(kind="let_out",
                                             annual_rent_received=100000,
                                             interest_24b=500000),))
    assert r.gti == 1425000              # salary untouched
    assert _cf(r, "house_property") == 0  # lapsed, not carried forward
    assert any("115BAC" in ln.section
               for ln in r.heads["house_property"].lines)


def test_house_property_setoff_capped_at_2l_in_old_regime():
    result = compute_assessment(AssessmentInputs(
        ay="2026-27", regime_election="old", filing_date=date(2026, 7, 20),
        salaries=(SalaryIncome(employer_name="Acme", gross_salary=1500000),),
        house_properties=(HouseProperty(kind="let_out",
                                        annual_rent_received=100000,
                                        interest_24b=500000),)))
    old = result.old
    # loss = 70,000 AV − 5,00,000 interest = −4,30,000; only 2L may leave
    # (hp_loss_setoff is signed: negative = the loss actually absorbed)
    assert old.hp_loss_setoff == -200000
    assert old.gti == 1450000 - 200000
    assert _cf(old, "house_property") == 230000


def test_ltcl_is_carried_forward_not_set_off_against_stcg():
    """Sec 74: a long-term loss only ever meets a long-term gain."""
    r = _run(capital_gains=(
        CGEntry(asset_class="listed_equity_stt",
                transfer_date=date(2025, 12, 1),
                acquisition_date=date(2023, 1, 1),
                full_value_consideration=200000,
                cost_of_acquisition=300000),          # LTCL 1,00,000
        CGEntry(asset_class="listed_equity_stt",
                transfer_date=date(2025, 12, 1),
                acquisition_date=date(2025, 6, 1),
                full_value_consideration=300000,
                cost_of_acquisition=220000)))         # STCG 80,000
    assert r.heads["capital_gains"].stcg_111a == 80000
    assert _cf(r, "ltcl") == 100000


def test_brought_forward_business_loss_meets_business_income_only():
    r = _run(salaries=(SalaryIncome(employer_name="Acme",
                                    gross_salary=1000000),),
             regular_businesses=(RegularBusiness(net_profit_before_tax=
                                                 300000),),
             brought_forward=BroughtForwardLosses(entries=(
                 BFLossEntry(ay="2023-24", kind="business", amount=500000),)))
    assert r.gti == 925000               # business income wiped, salary intact
    assert _cf(r, "business") == 200000


def test_brought_forward_loss_lapses_when_its_window_closes():
    """A speculation loss survives four assessment years; AY 2020-21 is out
    of time by AY 2026-27 while AY 2023-24 is still live."""
    r = _run(regular_businesses=(
                 RegularBusiness(name="Intraday", net_profit_before_tax=100000,
                                 is_speculative=True),),
             brought_forward=BroughtForwardLosses(entries=(
                 BFLossEntry(ay="2020-21", kind="speculation", amount=70000),
                 BFLossEntry(ay="2023-24", kind="speculation", amount=30000))))
    lapsed = {r_.ay: r_.lapsed for r_ in r.loss_table}
    assert lapsed["2020-21"] == 70000
    assert r.gti == 70000                # only the live 30,000 was absorbed


def test_brought_forward_capital_loss_applied_after_current_year():
    r = _run(capital_gains=(
                 CGEntry(asset_class="listed_equity_stt",
                         transfer_date=date(2025, 8, 1),
                         acquisition_date=date(2020, 1, 1),
                         full_value_consideration=600000,
                         cost_of_acquisition=200000),),   # 112A pool 4L
             brought_forward=BroughtForwardLosses(entries=(
                 BFLossEntry(ay="2024-25", kind="ltcl", amount=150000),)))
    assert r.heads["capital_gains"].ltcg_112a == 250000
    assert _cf(r, "ltcl") == 0


@pytest.mark.parametrize("kind", ["stcl", "ltcl", "business", "speculation",
                                  "house_property"])
def test_every_loss_kind_round_trips_through_the_table(kind):
    r = _run(brought_forward=BroughtForwardLosses(entries=(
        BFLossEntry(ay="2025-26", kind=kind, amount=12345),)))
    row = next(x for x in r.loss_table if x.kind == kind)
    assert row.brought_forward == 12345
    assert row.carried_forward == 12345      # nothing to absorb it
    assert row.label
