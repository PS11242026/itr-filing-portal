"""Full-engine scenarios with hand-computed expected values (to be
re-verified against the ITD utility via tests/golden)."""

from datetime import date

from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, CGEntry, Challan,
                               HouseProperty, PersonalInfo,
                               SalaryIncome, TDSEntry)


def _inputs(**kw) -> AssessmentInputs:
    base = dict(ay="2026-27", personal=PersonalInfo(dob=date(1990, 5, 10)))
    base.update(kw)
    return AssessmentInputs(**base)


def test_salary_12l_new_regime_rebate_zeroes_tax():
    result = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=1200000),)))
    new = result.new
    assert new.total_income == 1125000
    assert new.slab_tax == 52500
    assert new.rebate_87a == 52500
    assert new.tax_liability == 0
    # old regime: TI 11.5L → 12,500 + 1,00,000 + 45,000 = 1,57,500 + 4% cess
    assert result.old.tax_liability == 163800
    assert result.recommended == "new"
    assert result.saving == 163800


def test_87a_marginal_relief_just_above_12l():
    result = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=1280000),)))
    new = result.new
    assert new.total_income == 1205000
    assert new.slab_tax == 60750
    assert new.rebate_87a == 55750           # net slab tax = excess (5,000)
    assert new.tax_liability == 5200         # 5,000 + 4% cess


def test_property_sale_50c_with_basic_exemption_adjustment():
    result = compute_assessment(_inputs(
        capital_gains=(CGEntry(
            asset_class="property", transfer_date=date(2025, 9, 15),
            acquisition_date=date(2015, 6, 1),
            full_value_consideration=6000000, stamp_duty_value=7000000,
            cost_of_acquisition=3000000),)))
    new = result.new
    assert new.total_income == 4000000
    # basic exemption 4L absorbed → 36L @ 12.5% = 4,50,000 + cess = 4,68,000
    assert new.special_tax == 450000
    assert new.tax_liability == 468000
    # old regime: 2.5L zero band → 37.5L @ 12.5% = 4,68,750 + cess
    assert result.old.special_tax == 468750
    assert result.old.tax_liability == 487500
    assert result.recommended == "new"


def test_112a_with_salary_slab_only_rebate():
    result = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=1000000),),
        capital_gains=(CGEntry(
            asset_class="listed_equity_stt",
            transfer_date=date(2025, 8, 1),
            acquisition_date=date(2020, 1, 1),
            full_value_consideration=800000, cost_of_acquisition=500000),)))
    new = result.new
    # TI = 9,25,000 + (3L − 1.25L) = 11,00,000 ≤ 12L
    assert new.total_income == 1100000
    assert new.slab_tax == 32500
    assert new.rebate_87a == 32500           # slab portion only
    assert new.special_tax == 21875          # 1.75L @ 12.5%
    assert new.tax_liability == 21875 + 875  # + 4% cess


def test_old_regime_with_deductions_and_tds_refund():
    result = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=800000),),
        deductions=(("80C", 200000),),
        tds=(TDSEntry(tan="MUMA12345B", deductor="Acme",
                      section="192", amount=50000),),
        regime_election="old"))
    old = result.old
    assert old.total_income == 600000        # 7.5L − 1.5L
    assert old.tax_liability == 33800        # 32,500 + cess
    assert old.payable_or_refund == 33800 - 50000 + 0  # refund 16,200
    assert result.recommended == "old"       # election honoured


def test_hp_loss_setoff_old_vs_lapse_new():
    result = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=2000000),),
        house_properties=(HouseProperty(kind="self_occupied",
                                        interest_24b=250000),)))
    # old: salary 19.5L − 2L HP loss = 17.5L GTI
    assert result.old.gti == 1750000
    # new: loss lapses → GTI = 19.25L
    assert result.new.gti == 1925000


def test_interest_234_applied_when_filing_late_with_balance():
    result = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=3000000),),
        filing_date=date(2026, 9, 10)))      # due 31-07-2026
    new = result.new
    assert new.tax_liability > 0
    assert new.interest_234b > 0             # nothing prepaid
    assert new.interest_234c > 0
    assert new.interest_234a > 0             # filed after due date
    assert new.fee_234f == 5000


def test_senior_citizen_no_234bc_without_business():
    result = compute_assessment(_inputs(
        personal=PersonalInfo(dob=date(1950, 1, 1)),
        salaries=(SalaryIncome(employer_name="Pension",
                               gross_salary=3000000),),
        filing_date=date(2026, 7, 20)))
    assert result.new.interest_234b == 0
    assert result.new.interest_234c == 0


def test_advance_tax_challan_reduces_234b():
    with_advance = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=3000000),),
        challans=(Challan(kind="advance", paid_on=date(2025, 6, 10),
                          amount=500000),),
        filing_date=date(2026, 7, 20)))
    without = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=3000000),),
        filing_date=date(2026, 7, 20)))
    assert with_advance.new.interest_234b < without.new.interest_234b
    assert with_advance.new.interest_234c < without.new.interest_234c


def test_surcharge_with_marginal_relief_at_50l():
    result = compute_assessment(_inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=5150000),),
        regime_election="new"))
    new = result.new
    assert new.total_income == 5075000
    assert new.surcharge > 0
    assert new.marginal_relief > 0           # just over 50L threshold
    # ceiling: tax at 50L + (TI − 50L)
    slab_at_50l = 20000 + 40000 + 60000 + 80000 + 100000 + 780000
    assert (new.slab_tax + new.surcharge - new.marginal_relief
            == slab_at_50l + 75000)


def test_serde_roundtrip():
    from itrcore.tax import serde
    inputs = _inputs(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=1200000),),
        capital_gains=(CGEntry(asset_class="property",
                               transfer_date=date(2025, 9, 15),
                               acquisition_date=date(2015, 6, 1),
                               full_value_consideration=6000000,
                               cost_of_acquisition=3000000),),
        challans=(Challan(kind="advance", paid_on=date(2025, 6, 10),
                          amount=10000),),
        filing_date=date(2026, 7, 20))
    d = serde.inputs_to_dict(inputs)
    back = serde.inputs_from_dict(d)
    assert back == inputs
    assert (compute_assessment(back).new.tax_liability
            == compute_assessment(inputs).new.tax_liability)
