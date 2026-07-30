"""Head computations: salary, house property, other sources, deductions."""

from itrcore.tax import deductions, house_property, other_sources, salary
from itrcore.tax.ayconfig import load
from itrcore.tax.types import HouseProperty, OtherSources, SalaryIncome

CFG = load("2026-27")


def test_salary_new_regime_standard_deduction():
    result = salary.compute(
        (SalaryIncome(employer_name="Acme", gross_salary=1200000),),
        CFG.regime("new"))
    assert result.amount == 1200000 - 75000


def test_salary_old_regime_with_exempt_and_prof_tax():
    result = salary.compute(
        (SalaryIncome(employer_name="Acme", gross_salary=1000000,
                      exempt_allowances=100000, prof_tax=2400),),
        CFG.regime("old"))
    assert result.amount == 900000 - 50000 - 2400


def test_let_out_property_statutory_order():
    # GAV 2,00,000 (8 months rent) − municipal 10,000 → NAV 1,90,000;
    # 30% of NAV = 57,000 → income 1,33,000
    result = house_property.compute(
        (HouseProperty(kind="let_out", annual_rent_received=200000,
                       municipal_taxes_paid=10000),),
        CFG.raw["house_property"], new_regime=False)
    assert result.amount == 133000


def test_sop_interest_old_capped_new_disallowed():
    props = (HouseProperty(kind="self_occupied", interest_24b=250000),)
    old = house_property.compute(props, CFG.raw["house_property"],
                                 new_regime=False)
    assert old.amount == -200000          # capped at 2L
    new = house_property.compute(props, CFG.raw["house_property"],
                                 new_regime=True)
    assert new.amount == 0                # not deductible


def test_hp_loss_lapses_in_new_regime():
    props = (HouseProperty(kind="let_out", annual_rent_received=120000,
                           interest_24b=300000),)
    new = house_property.compute(props, CFG.raw["house_property"],
                                 new_regime=True)
    assert new.amount == 0
    assert any("lapses" in ln.label for ln in new.lines)


def test_family_pension_deduction():
    result = other_sources.compute(OtherSources(family_pension=90000),
                                   CFG.regime("new"))
    # 1/3 = 30,000 but capped at 25,000 (new regime)
    assert result.amount == 90000 - 25000


def test_taxable_gift_full_amount_not_excess():
    result = other_sources.compute(OtherSources(taxable_gifts=60000),
                                   CFG.regime("old"))
    assert result.amount == 60000


def test_deductions_80c_group_cap_and_new_regime_filter():
    claims = (("80C", 200000), ("80CCD(1B)", 60000), ("80D", 30000))
    total_old, _ = deductions.compute(
        claims, CFG.raw, regime="old", senior=False, cap_base=10**9,
        savings_interest=0, total_interest=0)
    assert total_old == 150000 + 50000 + 30000

    total_new, lines_new = deductions.compute(
        claims, CFG.raw, regime="new", senior=False, cap_base=10**9,
        savings_interest=0, total_interest=0)
    assert total_new == 0
    assert any("not available" in ln.label for ln in lines_new)


def test_80tta_limited_to_savings_interest_and_cap():
    claims = (("80TTA", 15000),)
    total, _ = deductions.compute(claims, CFG.raw, regime="old",
                                  senior=False, cap_base=10**9,
                                  savings_interest=7000, total_interest=7000)
    assert total == 7000
    total2, _ = deductions.compute(claims, CFG.raw, regime="old",
                                   senior=False, cap_base=10**9,
                                   savings_interest=50000,
                                   total_interest=50000)
    assert total2 == 10000
