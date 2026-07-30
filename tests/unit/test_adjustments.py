"""Manual add/less adjustment lines: engine math, serde round-trip."""

import pytest

from itrcore.tax import serde
from itrcore.tax.ayconfig import load as load_cfg
from itrcore.tax.engine import compute_assessment

AY = "2026-27"


def _compute(d: dict):
    return compute_assessment(serde.inputs_from_dict({"ay": AY, **d}))


def test_os_subhead_adjustments_add_and_less():
    r = _compute({
        "other_sources": {"fd_interest": 455497, "savings_interest": 9463},
        "adjustments": [
            {"target": "other_sources.other", "label": "PENSION FROM LIC",
             "amount": 100624},
            {"target": "other_sources.fd_interest",
             "label": "INTT. ON NSC (accrued)", "amount": 10616},
            {"target": "other_sources.savings_interest",
             "label": "Exempt u/s 10(15)", "amount": -3500},
        ]})
    os_head = r.new.heads["other_sources"]
    assert os_head.amount == (455497 + 10616) + (9463 - 3500) + 100624
    labels = [ln.label for ln in os_head.lines]
    assert "Adjustment: PENSION FROM LIC" in labels
    assert "Adjustment: INTT. ON NSC (accrued)" in labels
    assert "Adjustment: Exempt u/s 10(15)" in labels


def test_family_pension_adjustment_gets_third_deduction():
    r = _compute({
        "adjustments": [{"target": "other_sources.family_pension",
                         "label": "Family pension (AIS)",
                         "amount": 90000}]})
    cap = load_cfg(AY).regime("new")["family_pension_deduction_cap"]
    assert r.new.heads["other_sources"].amount == 90000 - min(30000, cap)


def test_adjusted_savings_interest_feeds_80tta():
    base = {
        "personal": {"resident_status": "Resident"},
        "salaries": [{"employer_name": "E", "gross_salary": 800000}],
        "other_sources": {"savings_interest": 4000},
        "deductions": [["80TTA", 10000]],
    }
    with_adj = {**base, "adjustments": [
        {"target": "other_sources.savings_interest", "label": "2nd bank",
         "amount": 8000}]}
    old_no = _compute(base).old
    old_adj = _compute(with_adj).old
    # Base alone: 80TTA capped at ₹4,000 actual savings interest. With the
    # +₹8,000 adjustment the full ₹10,000 cap is available.
    assert any(ln.section == "80TTA" and ln.amount == -4000
               for ln in old_no.deduction_lines)
    assert any(ln.section == "80TTA" and ln.amount == -10000
               for ln in old_adj.deduction_lines)


def test_salary_head_adjustment_and_floor():
    base = {"salaries": [{"employer_name": "E", "gross_salary": 500000}]}
    r = _compute({**base, "adjustments": [
        {"target": "salary", "label": "Arrears", "amount": -50000}]})
    # 5,00,000 − 75,000 std deduction − 50,000 adjustment.
    assert r.new.heads["salary"].amount == 375000
    floored = _compute({**base, "adjustments": [
        {"target": "salary", "label": "kill", "amount": -1000000}]})
    assert floored.new.heads["salary"].amount == 0


def test_tds_and_tcs_credit_adjustments_change_payable():
    base = {
        "salaries": [{"employer_name": "E", "gross_salary": 1500000}],
        "tds": [{"tan": "T", "deductor": "D", "section": "192",
                 "amount": 100000}],
    }
    plain = _compute(base).new
    adj = _compute({**base, "adjustments": [
        {"target": "tcs", "label": "TCS on car 206C(1F)", "amount": 25000},
        {"target": "tds", "label": "wrong credit dropped",
         "amount": -10000}]}).new
    assert adj.tds_total == plain.tds_total + 25000 - 10000
    assert any("TCS credit" in ln.label for ln in adj.lines)


def test_os_head_floored_at_zero_on_over_subtraction():
    r = _compute({
        "other_sources": {"dividend": 2000},
        "adjustments": [{"target": "other_sources.dividend",
                         "label": "reversal", "amount": -9000}]})
    os_head = r.new.heads["other_sources"]
    assert os_head.amount == 0
    assert any("floored at zero" in ln.label for ln in os_head.lines)


def test_unknown_adjustment_target_rejected():
    with pytest.raises(ValueError, match="unknown adjustment target"):
        _compute({"adjustments": [{"target": "capital_gains",
                                   "label": "x", "amount": 1}]})


def test_serde_roundtrip_keeps_adjustments():
    d = {"ay": AY, "adjustments": [
        {"target": "other_sources.other", "label": "NSC", "amount": 9341},
        {"target": "tds", "label": "less", "amount": -100}]}
    inputs = serde.inputs_from_dict(d)
    assert [a.amount for a in inputs.adjustments] == [9341, -100]
    back = serde.inputs_to_dict(inputs)
    assert back["adjustments"] == d["adjustments"]
