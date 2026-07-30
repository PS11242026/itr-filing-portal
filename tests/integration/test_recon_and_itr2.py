"""M6: reconciliation (SRS scenarios 5 & 6) + ITR-2 build/validate."""

from datetime import date

from itrcore.authz import mint_scope
from itrcore.itr import business_rules, eligibility, itr2, schema_registry
from itrcore.itr import validate as itr_validate
from itrcore.recon import service as recon_service
from itrcore.tax import serde
from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, CGEntry, PersonalInfo,
                               SalaryIncome, TDSEntry)

FILING_META = {
    "father_name": "Suresh Kumar", "place": "Mumbai", "city": "Mumbai",
    "pincode": "400001", "state_code": "27", "bank_name": "HDFC Bank",
    "ifsc": "HDFC0001234", "account_no": "50100123456789",
    "filing_date": "2026-07-20", "return_sec": 11,
}
TAXPAYER = {"pan": "AAAPL1234C", "full_name": "Ramesh Kumar",
            "dob": "1985-06-15", "email": "r@example.com",
            "mobile": "9820012345", "address": "Andheri West"}


def _save_inputs(scope, inputs):
    from itrcore.db.repos import computations as comp_repo
    comp_repo.save_inputs(scope, "2026-27",
                          serde.inputs_to_dict(inputs))


def test_srs5_ais_interest_underreport_caught_and_accepted(partner,
                                                           taxpayer_id):
    """Books declare ₹10,000 savings interest; AIS shows ₹15,000 → recon
    flags it; accepting AIS updates the inputs; blocker clears."""
    from itrcore.db.repos import computations as comp_repo
    from itrcore.db.repos import importers
    scope = mint_scope(partner, taxpayer_id)
    inputs = AssessmentInputs(
        ay="2026-27", personal=PersonalInfo(dob=date(1985, 6, 15)),
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=1000000),))
    d = serde.inputs_to_dict(inputs)
    d["other_sources"]["savings_interest"] = 10000
    comp_repo.save_inputs(scope, "2026-27", d)
    importers.import_ais(scope, "2026-27", [
        {"category": "interest_savings",
         "description": "Interest from savings bank",
         "amount_paise": 1500000}], "ais.json")

    blockers = recon_service.blockers(scope, "2026-27")
    assert any("Savings interest" in b and "15,000" in b for b in blockers)

    # staff accepts the AIS figure (mirrors the UI action)
    d2 = comp_repo.load_inputs(scope, "2026-27")
    d2["other_sources"]["savings_interest"] = 15000
    comp_repo.save_inputs(scope, "2026-27", d2)
    recon_service.resolve(scope, "2026-27", kind="ais",
                          item_key="ais:interest_savings",
                          resolution="accepted", remark="books → AIS 15k")
    assert recon_service.blockers(scope, "2026-27") == []


def test_srs6_26as_bonus_tds_mismatch(partner, taxpayer_id):
    """26AS carries TDS the return doesn't claim (forgotten bonus) → recon
    blocks; claiming more than 26AS also blocks."""
    from itrcore.db.repos import importers
    scope = mint_scope(partner, taxpayer_id)
    inputs = AssessmentInputs(
        ay="2026-27", personal=PersonalInfo(dob=date(1985, 6, 15)),
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=1500000),),
        tds=(TDSEntry(tan="MUMA12345B", deductor="Acme", section="192",
                      amount=100000),))
    _save_inputs(scope, inputs)
    importers.import_26as(scope, "2026-27", [
        {"tan": "MUMA12345B", "deductor_name": "ACME LTD",
         "section": "192", "amount_paid_paise": 150000000,
         "tds_paise": 10000000},
        {"tan": "DELB55555D", "deductor_name": "BONUS PAYER",
         "section": "194", "amount_paid_paise": 2000000,
         "tds_paise": 2000000}])                    # ₹20,000 TDS unclaimed

    blockers = recon_service.blockers(scope, "2026-27")
    assert any("not claimed" in b and "20,000" in b for b in blockers)

    recon_service.resolve(scope, "2026-27", kind="tds",
                          item_key="tds:DELB55555D",
                          resolution="edited",
                          remark="bonus added to salary; TDS claimed")
    assert recon_service.blockers(scope, "2026-27") == []


def test_recon_justified_requires_remark(partner, taxpayer_id):
    import pytest
    scope = mint_scope(partner, taxpayer_id)
    with pytest.raises(ValueError):
        recon_service.resolve(scope, "2026-27", kind="ais",
                              item_key="ais:dividend",
                              resolution="justified", remark="  ")


def test_itr2_build_validate_roundtrip():
    inputs = AssessmentInputs(
        ay="2026-27", personal=PersonalInfo(dob=date(1985, 6, 15)),
        salaries=(SalaryIncome(employer_name="Acme Ltd",
                               employer_tan="MUMA12345B",
                               gross_salary=2000000),),
        capital_gains=(CGEntry(
            asset_class="property", transfer_date=date(2025, 9, 15),
            acquisition_date=date(2015, 6, 1),
            full_value_consideration=6000000, stamp_duty_value=7000000,
            cost_of_acquisition=3000000),),
        tds=(TDSEntry(tan="MUMA12345B", deductor="Acme", section="192",
                      amount=300000),),
        filing_date=date(2026, 7, 20))
    inputs_d = serde.inputs_to_dict(inputs)
    result_d = serde.result_to_dict(compute_assessment(inputs))

    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d, profile={})
    assert sel.form == "ITR-2"

    bundle = schema_registry.get("ITR-2", "2026-27")
    doc = itr2.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime="new",
                     filing_meta=FILING_META, bundle=bundle)

    assert itr_validate.validate_schema(doc, bundle) == []
    assert business_rules.check(doc, expected_pan="AAAPL1234C",
                                regime_result=result_d["new"]) == []
    assert business_rules.round_trip_check(doc, result_d["new"]) == []

    body = doc["ITR"]["ITR2"]
    assert body["ScheduleCG"]["LongTermCapGain"]["LTCG112Others"] == 4000000
    si = {r["SecCode"]: r for r in body["ScheduleSI"]["SplCodeRateTax"]}
    assert si["112"]["SplRateInc"] == 4000000
    assert body["PartB_TI"]["CapGain"]["CapGainTotal"] == 4000000


def test_itr2_business_rules_catch_head_mismatch():
    inputs = AssessmentInputs(
        ay="2026-27", personal=PersonalInfo(dob=date(1985, 6, 15)),
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=2000000),))
    inputs_d = serde.inputs_to_dict(inputs)
    result_d = serde.result_to_dict(compute_assessment(inputs))
    bundle = schema_registry.get("ITR-2", "2026-27")
    doc = itr2.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime="new",
                     filing_meta=FILING_META, bundle=bundle)
    doc["ITR"]["ITR2"]["PartB_TI"]["Salaries"] = 1
    errors = business_rules.check(doc, expected_pan="AAAPL1234C",
                                  regime_result=result_d["new"])
    assert any("Salaries" in e for e in errors)
