"""ITR-3 / ITR-4 build → schema validate → business rules → round-trip."""

from datetime import date

from itrcore.itr import (business_rules, eligibility, itr3, itr4,
                         schema_registry)
from itrcore.itr import validate as itr_validate
from itrcore.tax import serde
from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, PersonalInfo,
                               PresumptiveBusiness, RegularBusiness,
                               SalaryIncome, TDSEntry)

TAXPAYER = {"pan": "AAAPL1234C", "full_name": "Ramesh Kumar",
            "dob": "1985-06-15", "email": "r@example.com",
            "mobile": "9820012345", "address": "Andheri West"}
FILING_META = {"father_name": "Suresh Kumar", "place": "Mumbai",
               "city": "Mumbai", "pincode": "400001", "state_code": "27",
               "bank_name": "HDFC Bank", "ifsc": "HDFC0001234",
               "account_no": "50100123456789", "filing_date": "2026-07-20",
               "return_sec": 11}


def _build_and_check(inputs, expected_form, builder):
    inputs_d = serde.inputs_to_dict(inputs)
    result_d = serde.result_to_dict(compute_assessment(inputs))
    sel = eligibility.select_form(inputs_dict=inputs_d, result_dict=result_d,
                                  profile={})
    assert sel.form == expected_form, sel
    bundle = schema_registry.get(expected_form, "2026-27")
    regime = result_d["recommended"]
    doc = builder.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                        result_dict=result_d, regime=regime,
                        filing_meta=FILING_META, bundle=bundle)
    assert itr_validate.validate_schema(doc, bundle) == []
    assert business_rules.check(doc, expected_pan="AAAPL1234C",
                                regime_result=result_d[regime]) == []
    assert business_rules.round_trip_check(doc, result_d[regime]) == []
    return doc


def test_itr4_presumptive_build_roundtrip():
    inputs = AssessmentInputs(
        ay="2026-27", personal=PersonalInfo(dob=date(1985, 6, 15)),
        businesses=(PresumptiveBusiness(section="44ADA",
                                        gross_receipts=2000000),))
    doc = _build_and_check(inputs, "ITR-4", itr4)
    body = doc["ITR"]["ITR4"]
    assert body["ScheduleBP"]["IncChargeableUnderBP"] == 1000000
    assert body["PartB_TI"]["ProfBusinessGain"] == 1000000


def test_itr3_regular_books_build_roundtrip():
    inputs = AssessmentInputs(
        ay="2026-27", personal=PersonalInfo(dob=date(1985, 6, 15)),
        salaries=(SalaryIncome(employer_name="Acme",
                               employer_tan="MUMA12345B",
                               gross_salary=600000),),
        regular_businesses=(RegularBusiness(
            name="Traders", net_profit_before_tax=1200000,
            depreciation_books=100000, depreciation_it=80000),),
        tds=(TDSEntry(tan="MUMA12345B", deductor="Acme", section="192",
                      amount=20000),),
        filing_date=date(2026, 7, 20))
    doc = _build_and_check(inputs, "ITR-3", itr3)
    body = doc["ITR"]["ITR3"]
    # 1,200,000 + 100,000 − 80,000 = 12,20,000
    assert body["ScheduleBP"]["IncChargeableUnderBP"] == 1220000
    assert body["PartB_TI"]["ProfBusinessGain"] == 1220000
