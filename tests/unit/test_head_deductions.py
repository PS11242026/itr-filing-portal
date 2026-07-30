"""Head-wise deductions: salary 16(ii), HP 25A arrears, OS 57 expenses."""

from itrcore.tax import house_property, other_sources, salary
from itrcore.tax.ayconfig import load as load_cfg
from itrcore.tax.types import HouseProperty, OtherSources, SalaryIncome

_CFG = load_cfg("2026-27")
OLD = _CFG.regime("old")
NEW = _CFG.regime("new")
HP_CFG = _CFG.raw["house_property"]


def test_entertainment_allowance_old_regime_least_of_rule():
    # net salary ₹1,00,000 → 1/5 = 20,000; cap 5,000; actual 8,000 → 5,000
    h = salary.compute((SalaryIncome(employer_name="Gov",
                                     gross_salary=100000,
                                     entertainment_allowance=8000),), OLD)
    by_sec = {ln.section: ln.amount for ln in h.lines}
    assert by_sec.get("16(ii)") == -5000


def test_entertainment_allowance_not_allowed_in_new_regime():
    h = salary.compute((SalaryIncome(employer_name="Gov",
                                     gross_salary=100000,
                                     entertainment_allowance=8000),), NEW)
    assert all(ln.section != "16(ii)" for ln in h.lines)


def test_hp_arrears_25a_gets_30pct_deduction():
    h = house_property.compute(
        (HouseProperty(kind="let_out", arrears_rent_received=100000),),
        HP_CFG, new_regime=False)
    assert h.amount == 70000  # 100000 − 30%


def test_os_57_expenses_reduce_income():
    h = other_sources.compute(
        OtherSources(fd_interest=50000, deductions_57=8000), NEW)
    assert h.amount == 42000


def test_os_57_capped_at_income():
    h = other_sources.compute(
        OtherSources(fd_interest=5000, deductions_57=9000), NEW)
    assert h.amount == 0  # deduction capped at the income
