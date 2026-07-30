"""Presumptive business/professional income (Sec 44AD / 44ADA / 44AE)."""

from itrcore.itr import eligibility
from itrcore.tax import business, serde
from itrcore.tax.ayconfig import load as load_cfg
from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, PresumptiveBusiness,
                               RegularBusiness)

CFG = load_cfg("2026-27").raw["presumptive"]


def _head(*businesses):
    return business.compute(businesses, (), CFG)


def test_44ad_digital_and_cash_rates():
    # 6% of ₹10,00,000 digital + 8% of ₹5,00,000 cash = 60,000 + 40,000.
    h = _head(PresumptiveBusiness(section="44AD", turnover_digital=1000000,
                                  turnover_cash=500000))
    assert h.amount == 100000


def test_44ada_half_of_receipts():
    h = _head(PresumptiveBusiness(section="44ADA", gross_receipts=2000000))
    assert h.amount == 1000000


def test_44ae_per_ton_and_per_vehicle_month():
    # 100 ton-months × ₹1,000 + 12 vehicle-months × ₹7,500.
    h = _head(PresumptiveBusiness(section="44AE", heavy_ton_months=100,
                                  other_vehicle_months=12))
    assert h.amount == 100000 + 90000


def test_declared_income_overrides_when_higher():
    h = _head(PresumptiveBusiness(section="44AD", turnover_digital=1000000,
                                  declared_income=80000))
    assert h.amount == 80000  # max(60,000 presumptive, 80,000 declared)


def test_over_turnover_limit_is_flagged():
    # ₹2.5 cr all cash → above the ₹2 cr limit (₹3 cr needs ≤5% cash).
    h = _head(PresumptiveBusiness(section="44AD", turnover_cash=25000000))
    assert any("exceed" in ln.label.lower() for ln in h.lines)


def test_business_income_flows_into_total_income():
    inputs = AssessmentInputs(
        ay="2026-27",
        businesses=(PresumptiveBusiness(section="44ADA",
                                        gross_receipts=2000000),))
    result = compute_assessment(inputs)
    assert result.new.heads["business"].amount == 1000000
    assert result.new.total_income == 1000000
    assert result.old.heads["business"].amount == 1000000


def _select(inputs_dict):
    result_dict = serde.result_to_dict(
        compute_assessment(serde.inputs_from_dict(inputs_dict)))
    return eligibility.select_form(inputs_dict=inputs_dict,
                                   result_dict=result_dict, profile={})


def test_presumptive_only_routes_to_itr4():
    sel = _select({"ay": "2026-27",
                   "personal": {"resident_status": "Resident"},
                   "businesses": [{"section": "44ADA",
                                   "gross_receipts": 2000000}]})
    assert sel.form == "ITR-4"


def test_business_with_disqualifier_routes_to_itr3():
    sel = _select({"ay": "2026-27",
                   "personal": {"resident_status": "NRI"},
                   "businesses": [{"section": "44AD",
                                   "turnover_digital": 1000000}]})
    assert sel.form == "ITR-3"


def test_regular_books_net_profit_adjustments():
    inputs = AssessmentInputs(
        ay="2026-27",
        regular_businesses=(RegularBusiness(
            name="Traders", net_profit_before_tax=800000,
            depreciation_books=100000, depreciation_it=120000,
            disallowances=30000),))
    result = compute_assessment(inputs)
    # 800000 + 100000 - 120000 + 30000 = 810000
    assert result.new.heads["business"].amount == 810000


def test_presumptive_and_regular_books_combine():
    inputs = AssessmentInputs(
        ay="2026-27",
        businesses=(PresumptiveBusiness(section="44ADA",
                                        gross_receipts=2000000),),
        regular_businesses=(RegularBusiness(net_profit_before_tax=500000),))
    result = compute_assessment(inputs)
    assert result.new.heads["business"].amount == 1000000 + 500000
