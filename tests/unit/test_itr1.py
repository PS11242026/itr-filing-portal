"""ITR-1: eligibility, builder, 3-layer validation, filings, mock ERI."""

from datetime import date

import pytest

from itrcore.authz import mint_scope
from itrcore.itr import business_rules, eligibility, itr1, schema_registry
from itrcore.itr import validate as itr_validate
from itrcore.tax import serde
from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, CGEntry, HouseProperty,
                               PersonalInfo, SalaryIncome, TDSEntry)

FILING_META = {
    "father_name": "Suresh Kumar", "place": "Mumbai", "city": "Mumbai",
    "pincode": "400001", "state_code": "27", "bank_name": "HDFC Bank",
    "ifsc": "HDFC0001234", "account_no": "50100123456789",
    "filing_date": "2026-07-20", "return_sec": 11,
}
TAXPAYER = {"pan": "AAAPL1234C", "full_name": "Ramesh Kumar",
            "dob": "1985-06-15", "email": "r@example.com",
            "mobile": "9820012345", "address": "Andheri West"}


def _computed(**kw):
    base = dict(ay="2026-27", personal=PersonalInfo(dob=date(1985, 6, 15)))
    base.update(kw)
    inputs = AssessmentInputs(**base)
    result = compute_assessment(inputs)
    return serde.inputs_to_dict(inputs), serde.result_to_dict(result)


def _salary_case():
    return _computed(
        salaries=(SalaryIncome(employer_name="Acme Ltd",
                               employer_tan="MUMA12345B",
                               gross_salary=1500000),),
        house_properties=(HouseProperty(kind="let_out",
                                        annual_rent_received=240000,
                                        municipal_taxes_paid=8000),),
        tds=(TDSEntry(tan="MUMA12345B", deductor="Acme Ltd",
                      section="192", amount=150000),),
        filing_date=date(2026, 7, 20))


# ------------------------------------------------------------ eligibility --

def test_eligible_simple_salary_case():
    inputs_d, result_d = _salary_case()
    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d, profile={})
    assert sel.form == "ITR-1" and not sel.reasons


def test_property_sale_forces_itr2():
    inputs_d, result_d = _computed(
        capital_gains=(CGEntry(asset_class="property",
                               transfer_date=date(2025, 9, 15),
                               acquisition_date=date(2015, 6, 1),
                               full_value_consideration=6000000,
                               cost_of_acquisition=3000000),))
    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d, profile={})
    assert sel.form == "ITR-2"
    assert any("other than listed equity" in r for r in sel.reasons)


def test_112a_within_exemption_stays_itr1():
    inputs_d, result_d = _computed(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=800000),),
        capital_gains=(CGEntry(asset_class="listed_equity_stt",
                               transfer_date=date(2025, 8, 1),
                               acquisition_date=date(2020, 1, 1),
                               full_value_consideration=300000,
                               cost_of_acquisition=200000),))
    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d, profile={})
    assert sel.form == "ITR-1"          # 1L gain sits inside the 1.25L slab


def test_director_flag_forces_itr2():
    inputs_d, result_d = _salary_case()
    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d,
                                  profile={"is_director": True})
    assert sel.form == "ITR-2"


def test_ti_above_50l_forces_itr2():
    inputs_d, result_d = _computed(
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=6000000),))
    sel = eligibility.select_form(inputs_dict=inputs_d,
                                  result_dict=result_d, profile={})
    assert sel.form == "ITR-2"


# ---------------------------------------------------------------- builder --

def test_build_validate_and_round_trip():
    inputs_d, result_d = _salary_case()
    bundle = schema_registry.get("ITR-1", "2026-27")
    doc = itr1.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime="new",
                     filing_meta=FILING_META, bundle=bundle)

    assert itr_validate.validate_schema(doc, bundle) == []
    assert business_rules.check(doc, expected_pan="AAAPL1234C",
                                regime_result=result_d["new"]) == []
    assert business_rules.round_trip_check(doc, result_d["new"]) == []

    itr1_body = doc["ITR"]["ITR1"]
    assert itr1_body["FilingStatus"]["NewTaxRegime"] == "Y"
    assert itr1_body["PersonalInfo"]["PAN"] == "AAAPL1234C"
    assert (itr1_body["ITR1_IncomeDeductions"]["GrossTotIncome"]
            == result_d["new"]["gti"])
    assert (itr1_body["TDSonSalaries"]["TotalTDSonSalaries"] == 150000)


def test_business_rules_catch_pan_mismatch_and_tds_break():
    inputs_d, result_d = _salary_case()
    bundle = schema_registry.get("ITR-1", "2026-27")
    doc = itr1.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime="new",
                     filing_meta=FILING_META, bundle=bundle)
    doc["ITR"]["ITR1"]["PersonalInfo"]["PAN"] = "ZZZPZ9999Z"
    doc["ITR"]["ITR1"]["TaxPaid"]["TaxesPaid"]["TDS"] = 999
    errors = business_rules.check(doc, expected_pan="AAAPL1234C",
                                  regime_result=result_d["new"])
    assert any("PAN" in e for e in errors)
    assert any("TDS" in e for e in errors)


def test_refund_requires_bank_details():
    inputs_d, result_d = _salary_case()   # TDS 1.1L > liability → refund
    assert result_d["new"]["payable_or_refund"] < 0
    bundle = schema_registry.get("ITR-1", "2026-27")
    meta = dict(FILING_META, ifsc="", account_no="", bank_name="")
    doc = itr1.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime="new",
                     filing_meta=meta, bundle=bundle)
    errors = business_rules.check(doc, expected_pan="AAAPL1234C",
                                  regime_result=result_d["new"])
    assert any("bank" in e.lower() for e in errors)


# ---------------------------------------------------------------- filings --

def test_filing_lifecycle(partner, taxpayer_id):
    from itrcore.db.repos import computations as comp_repo
    from itrcore.db.repos import filings as filings_repo
    scope = mint_scope(partner, taxpayer_id)
    inputs_d, result_d = _salary_case()
    v = comp_repo.save_snapshot(scope, "2026-27", inputs_d, result_d,
                                engine_version="t", config_hash="t",
                                inputs_digest="t")
    snap = comp_repo.get_snapshot(scope, "2026-27", v)

    bundle = schema_registry.get("ITR-1", "2026-27")
    doc = itr1.build(taxpayer=TAXPAYER, inputs_dict=inputs_d,
                     result_dict=result_d, regime="new",
                     filing_meta=FILING_META, bundle=bundle)
    fid, path = filings_repo.create_filing(
        scope, ay="2026-27", form="ITR-1", filing_type="original",
        computation_id=snap["id"], json_dict=doc,
        schema_version=bundle.version)
    assert path.endswith(".json")

    with pytest.raises(ValueError):       # ack number mandatory
        filings_repo.update_status(scope, fid, "uploaded")
    filings_repo.update_status(scope, fid, "uploaded",
                               ack_no="123456789012345",
                               filed_on="2026-07-20")
    with pytest.raises(ValueError):       # can't skip to processed
        filings_repo.update_status(scope, fid, "processed")
    filings_repo.update_status(scope, fid, "everified",
                               everified_on="2026-07-21")
    row = filings_repo.get_filing(scope, fid)
    assert row["status"] == "everified"
    assert row["ack_no"] == "123456789012345"


def test_mock_eri_is_clearly_mock(fresh_db):
    from itrcore.efile.mock_eri import MockERIClient
    client = MockERIClient()
    res = client.submit_return({"ITR": {}}, "ITR-1", "2026-27")
    assert res.success and res.ack_no.startswith("MOCK")
    assert any("mock" in m for m in res.messages)
