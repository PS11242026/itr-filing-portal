"""Exempt income (Schedule EI): reported separately, never in total income;
agricultural income triggers partial integration for rate purposes."""

from datetime import date

from itrcore.itr import eligibility, itr1, itr2, schema_registry
from itrcore.itr import validate as itr_validate
from itrcore.tax import serde
from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, ExemptIncomeEntry,
                               PersonalInfo, SalaryIncome)

FILING_META = {
    "father_name": "Suresh Kumar", "place": "Mumbai", "city": "Mumbai",
    "pincode": "400001", "state_code": "27", "bank_name": "HDFC Bank",
    "ifsc": "HDFC0001234", "account_no": "50100123456789",
    "filing_date": "2026-07-20", "return_sec": 11,
}
TAXPAYER = {"pan": "AAAPL1234C", "full_name": "Ramesh Kumar",
            "dob": "1985-06-15", "email": "r@example.com",
            "mobile": "9820012345", "address": "Andheri West"}


def _compute(*, gross_salary: int, exempt=(), regime_election="auto"):
    inputs = AssessmentInputs(
        ay="2026-27",
        personal=PersonalInfo(dob=date(1985, 6, 15)),
        salaries=(SalaryIncome(employer_name="Acme Ltd",
                               gross_salary=gross_salary),),
        exempt_incomes=tuple(exempt),
        regime_election=regime_election)
    return inputs, compute_assessment(inputs)


def test_exempt_income_never_enters_total_income():
    _, base = _compute(gross_salary=650000)
    _, with_ei = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(11)", description="PPF",
                          amount=40000),
        ExemptIncomeEntry(section="10(2A)", amount=100000)))
    for regime in ("old", "new"):
        r0, r1 = getattr(base, regime), getattr(with_ei, regime)
        assert r1.gti == r0.gti
        assert r1.total_income == r0.total_income
        assert r1.tax_liability == r0.tax_liability   # no agri → no rate hit
        assert r1.exempt_total == 140000
        assert any(ln.section == "Sch EI" for ln in r1.exempt_lines)
    assert base.old.exempt_total == 0 and base.old.exempt_lines == ()


def test_agri_partial_integration_old_regime():
    # Normal TI 6,00,000 (6.5L − 50k std) + agri 2,00,000:
    # slab(8L)=72,500 − slab(2.5L+2L)=10,000 → 62,500 (vs 32,500 plain).
    _, res = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=200000),))
    assert res.old.total_income == 600000
    assert res.old.slab_tax == 62500
    assert any("partial integration" in ln.label for ln in res.old.lines)


def test_agri_partial_integration_new_regime():
    # Normal TI 13,00,000 (13.75L − 75k std) + agri 3,00,000:
    # slab(16L)=1,20,000 − slab(4L+3L)=15,000 → 1,05,000 (vs 75,000).
    _, res = _compute(gross_salary=1375000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=300000),))
    assert res.new.total_income == 1300000
    assert res.new.slab_tax == 105000


def test_agri_at_or_below_threshold_no_integration():
    _, base = _compute(gross_salary=650000)
    _, res = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=5000),))
    assert res.old.slab_tax == base.old.slab_tax
    assert res.new.slab_tax == base.new.slab_tax


def test_agri_no_integration_below_basic_exemption():
    # Non-agri TI under the zero band → no aggregation despite large agri.
    _, res = _compute(gross_salary=280000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=900000),))
    assert res.old.slab_tax == 0
    assert res.new.slab_tax == 0


def test_serde_round_trips_exempt_incomes():
    inputs, _ = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", description="farm",
                          amount=200000),))
    d = serde.inputs_to_dict(inputs)
    back = serde.inputs_from_dict(d)
    assert back.exempt_incomes == inputs.exempt_incomes


def _dicts(inputs, result):
    return serde.inputs_to_dict(inputs), serde.result_to_dict(result)


def test_agri_above_5000_forces_itr2():
    inputs, result = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=6000),))
    inputs_d, result_d = _dicts(inputs, result)
    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d, profile={})
    assert sel.form == "ITR-2"
    assert any("Agricultural" in r for r in sel.reasons)


def test_agri_within_limit_and_other_exempt_stay_itr1():
    inputs, result = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=5000),
        ExemptIncomeEntry(section="10(11)", amount=500000)))
    inputs_d, result_d = _dicts(inputs, result)
    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d, profile={})
    assert sel.form == "ITR-1"


def test_itr1_reports_schedule_ei():
    inputs, result = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=4000),
        ExemptIncomeEntry(section="10(11)", description="PPF",
                          amount=40000)))
    inputs_d, result_d = _dicts(inputs, result)
    bundle = schema_registry.get("ITR-1", "2026-27")
    doc = itr1.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime=result.recommended,
                     filing_meta=FILING_META, bundle=bundle)
    inc = doc["ITR"]["ITR1"]["ITR1_IncomeDeductions"]
    assert inc["ExemptIncAgriOthUs10"] == 44000
    natures = {d["NatureDesc"] for d in inc["ExemptIncAgriOthUs10Dtls"]}
    assert natures == {"AGRI", "10(11)"}
    assert itr_validate.validate_schema(doc, bundle) == []


def test_itr2_reports_schedule_ei():
    inputs, result = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=200000),
        ExemptIncomeEntry(section="10(15)", amount=12000),
        ExemptIncomeEntry(section="10(2A)", amount=90000)))
    inputs_d, result_d = _dicts(inputs, result)
    bundle = schema_registry.get("ITR-2", "2026-27")
    doc = itr2.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime=result.recommended,
                     filing_meta=FILING_META, bundle=bundle)
    ei = doc["ITR"]["ITR2"]["ScheduleEI"]
    assert ei["NetAgriIncOrOthrIncRule7"] == 200000
    assert ei["InterestInc"] == 12000
    assert ei["TotalExemptInc"] == 302000
    assert ei["OthersInc"]["OthersIncDtls"] == [
        {"NatureDesc": "10(2A)", "OthNatOfInc": "", "OthAmount": 90000}]
    assert itr_validate.validate_schema(doc, bundle) == []


def test_slab_lines_reconcile_to_slab_tax():
    # Plain case: band-wise lines must sum exactly to the slab tax.
    _, res = _compute(gross_salary=1375000)
    for regime in (res.old, res.new):
        assert sum(ln.amount for ln in regime.slab_lines) \
            == regime.slab_tax
        assert all("@" in ln.label for ln in regime.slab_lines
                   if ln.amount > 0)


def test_slab_lines_reconcile_with_agri_integration():
    _, res = _compute(gross_salary=650000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=200000),))
    old = res.old
    assert old.slab_tax == 62500
    assert sum(ln.amount for ln in old.slab_lines) == 62500
    assert any(ln.amount < 0 and "agricultural" in ln.label.casefold()
               for ln in old.slab_lines)


def test_html_sheet_shows_tax_by_slab_and_subhead():
    from itrcore.export import computation_html
    from itrcore.tax import serde
    _, res = _compute(gross_salary=1375000, exempt=(
        ExemptIncomeEntry(section="10(1)", amount=200000),))
    html = computation_html.render(
        taxpayer_name="Ramesh Kumar", pan_masked="XXXXX1234C",
        ay="2026-27", result=serde.result_to_dict(res)
        | {"recommended": res.recommended, "saving": res.saving,
           "engine_version": res.engine_version,
           "config_hash": res.config_hash})
    assert "Computation of tax" in html
    assert "Tax at slab rates on normal income" in html
    assert "Tax on ₹" in html
