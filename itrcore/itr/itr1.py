"""ITR-1 (Sahaj) JSON builder: computation snapshot → e-filing JSON.

Explicit mapping code by design; schema-version key renames belong in
quirks.py, not here.
"""

from datetime import date

from itrcore.itr import quirks
from itrcore.itr.schema_registry import SchemaBundle
from itrcore.tax.rounding import pct

_VIA_FIELD = {
    "80C": "Section80C", "80CCC": "Section80CCC",
    "80CCD(1)": "Section80CCDEmployeeOrSE",
    "80CCD(1B)": "Section80CCD1B", "80CCD(2)": "Section80CCDEmployer",
    "80CCH": "Section80CCH", "80D": "Section80D", "80DD": "Section80DD",
    "80DDB": "Section80DDB", "80E": "Section80E", "80EE": "Section80EE",
    "80EEA": "Section80EEA", "80G": "Section80G", "80GG": "Section80GG",
    "80TTA": "Section80TTA", "80TTB": "Section80TTB", "80U": "Section80U",
}


def _name_parts(full_name: str) -> dict:
    parts = (full_name or "").strip().split()
    if not parts:
        return {"FirstName": "", "MiddleName": "", "SurNameOrOrgName": ""}
    if len(parts) == 1:
        return {"FirstName": "", "MiddleName": "",
                "SurNameOrOrgName": parts[0]}
    return {"FirstName": parts[0], "MiddleName": " ".join(parts[1:-1]),
            "SurNameOrOrgName": parts[-1]}


def _line_amount(lines: list[dict], section: str,
                 label_prefix: str = "") -> int:
    for ln in lines:
        if ln["section"] == section and ln["label"].startswith(label_prefix):
            return abs(ln["amount"])
    return 0


def _applied_via(deduction_lines: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in deduction_lines:
        if ln["label"].startswith("Deduction u/s ") and ln["amount"] < 0:
            out[ln["section"]] = out.get(ln["section"], 0) - ln["amount"]
    return out


_EI_NATURE = {"10(1)": "AGRI", "Other": "OTH"}


def _exempt_income_block(inputs_dict: dict) -> tuple[int, list[dict]]:
    """Schedule-EI reporting block shared by the ITR-1/2 builders:
    (total, [{NatureDesc, OthNatOfInc, OthAmount}])."""
    total = 0
    details: list[dict] = []
    for e in inputs_dict.get("exempt_incomes", []):
        amount = int(e.get("amount", 0))
        if amount <= 0:
            continue
        section = e.get("section", "Other")
        details.append({
            "NatureDesc": _EI_NATURE.get(section, section),
            "OthNatOfInc": e.get("description", ""),
            "OthAmount": amount,
        })
        total += amount
    return total, details


def build(*, taxpayer: dict, inputs_dict: dict, result_dict: dict,
          regime: str, filing_meta: dict, bundle: SchemaBundle) -> dict:
    res = result_dict[regime]
    heads = res["heads"]
    sal_lines = heads["salary"].get("lines", [])
    hp_head = heads["house_property"]
    cg = heads["capital_gains"]
    os_head = heads["other_sources"]

    gross_salary = sum(s.get("gross_salary", 0)
                       for s in inputs_dict.get("salaries", []))
    exempt = sum(s.get("exempt_allowances", 0)
                 for s in inputs_dict.get("salaries", []))
    std16ia = _line_amount(sal_lines, "16(ia)")
    prof16iii = _line_amount(sal_lines, "16(iii)")

    hp_inputs = inputs_dict.get("house_properties", [])
    hp = hp_inputs[0] if hp_inputs else None
    if hp:
        gav = max(0, hp.get("annual_rent_received", 0))
        muni = min(max(0, hp.get("municipal_taxes_paid", 0)), gav)
        nav = gav - muni
        hp_block = {
            "TypeOfHP": "S" if hp.get("kind") == "self_occupied" else "L",
            "GrossRentReceived": gav,
            "TaxPaidlocalAuth": muni,
            "AnnualValue": nav,
            "StandardDeduction": pct(nav, 30),
            "InterestPayable": (hp.get("interest_24b", 0)
                                + hp.get("pre_construction_interest_total",
                                         0) // 5),
            "TotalIncomeOfHP": hp_head["amount"],
        }
    else:
        hp_block = {"TypeOfHP": "", "GrossRentReceived": 0,
                    "TaxPaidlocalAuth": 0, "AnnualValue": 0,
                    "StandardDeduction": 0, "InterestPayable": 0,
                    "TotalIncomeOfHP": 0}

    # 112A gain inside the exemption (eligibility guarantees taxable = 0)
    ltcg_112a_reported = _line_amount(cg.get("lines", []), "112A",
                                      "112A exemption")

    via_applied = _applied_via(res.get("deduction_lines", []))
    via_block = {_VIA_FIELD.get(sec, "Section" + sec.replace("(", "")
                                .replace(")", "").replace("80", "80", 1)): amt
                 for sec, amt in via_applied.items()}
    via_block["TotalChapVIADeductions"] = res["deductions_total"]

    tds_entries = inputs_dict.get("tds", [])
    tds_sal = [t for t in tds_entries if t.get("section") == "192"]
    tds_oth = [t for t in tds_entries if t.get("section") != "192"]
    tds_total = sum(t.get("amount", 0) for t in tds_entries)

    challans = inputs_dict.get("challans", [])
    interest_total = (res["interest_234a"] + res["interest_234b"]
                      + res["interest_234c"] + res["fee_234f"])
    payable = res["payable_or_refund"]
    tax_on_ti = res["slab_tax"] + res["special_tax"]
    after_rebate = max(0, tax_on_ti - res["rebate_87a"]) \
        if regime == "old" else max(0, res["slab_tax"]
                                    - res["rebate_87a"]) + res["special_tax"]
    gross_liab = res["tax_liability"]

    filing_date = filing_meta.get("filing_date") or date.today().isoformat()
    exempt_total, exempt_details = _exempt_income_block(inputs_dict)

    doc = {"ITR": {"ITR1": {
        "CreationInfo": {
            "SWVersionNo": "1.0",
            "SWCreatedBy": "SW00000000",
            "JSONCreationDate": date.today().isoformat(),
            "JSONCreatedBy": "SW00000000",
            "IntermediaryCity": "",
            "Digest": "-",
        },
        "Form_ITR1": {
            "FormName": "ITR-1",
            "Description": "For Indl having income from salary, one house "
                           "property, other sources",
            "AssessmentYear": "2026",
            "SchemaVer": bundle.version,
            "FormVer": bundle.version,
        },
        "PersonalInfo": {
            "AssesseeName": _name_parts(taxpayer["full_name"]),
            "PAN": taxpayer["pan"],
            "DOB": taxpayer.get("dob") or "",
            "AadhaarCardNo": taxpayer.get("aadhaar") or "",
            "EmployerCategory": "OTH",
            "Address": {
                "ResidenceNo": "",
                "LocalityOrArea": taxpayer.get("address") or "",
                "CityOrTownOrDistrict": filing_meta.get("city", ""),
                "StateCode": filing_meta.get("state_code", ""),
                "CountryCode": "91",
                "PinCode": filing_meta.get("pincode", ""),
                "EmailAddress": taxpayer.get("email") or "",
                "MobileNo": taxpayer.get("mobile") or "",
            },
        },
        "FilingStatus": {
            "ReturnFileSec": int(filing_meta.get("return_sec", 11)),
            "NewTaxRegime": "Y" if regime == "new" else "N",
            "SeventhProvisio139": "N",
        },
        "ITR1_IncomeDeductions": {
            "GrossSalary": gross_salary,
            "ExemptAllwnceUs10": exempt,
            "NetSalary": max(0, gross_salary - exempt),
            "DeductionUs16": std16ia + prof16iii,
            "DeductionUs16ia": std16ia,
            "ProfessionalTaxUs16iii": prof16iii,
            "IncomeFromSal": heads["salary"]["amount"],
            **hp_block,
            "LongTermCapGain112A": ltcg_112a_reported,
            "IncomeOthSrc": os_head["amount"],
            "DeductionUs57iia": _line_amount(os_head.get("lines", []),
                                             "57(iia)"),
            "GrossTotIncome": res["gti"],
            "UsrDeductUndChapVIA": dict(via_block),
            "DeductUndChapVIA": dict(via_block),
            "TotalIncome": res["total_income"],
            "ExemptIncAgriOthUs10": exempt_total,
            "ExemptIncAgriOthUs10Dtls": exempt_details,
        },
        "ITR1_TaxComputation": {
            "TotalTaxPayable": tax_on_ti,
            "Rebate87A": res["rebate_87a"],
            "TaxPayableOnRebate": after_rebate,
            "EducationCess": res["cess"],
            "GrossTaxLiability": gross_liab,
            "Section89": 0,
            "NetTaxLiability": gross_liab,
            "TotalIntrstPay": interest_total,
            "IntrstPay": {
                "IntrstPayUs234A": res["interest_234a"],
                "IntrstPayUs234B": res["interest_234b"],
                "IntrstPayUs234C": res["interest_234c"],
                "LateFilingFee234F": res["fee_234f"],
            },
            "TotTaxPlusIntrstPay": gross_liab + interest_total,
        },
        "TaxPaid": {
            "TaxesPaid": {
                "AdvanceTax": res["advance_tax_paid"],
                "TDS": tds_total,
                "TCS": 0,
                "SelfAssessmentTax": res["self_assessment_paid"],
                "TotalTaxesPaid": (res["advance_tax_paid"] + tds_total
                                   + res["self_assessment_paid"]),
            },
            "BalTaxPayable": max(0, payable),
        },
        "Refund": {
            "RefundDue": max(0, -payable),
            "BankAccountDtls": {
                "AddtnlBankDetails": [{
                    "IFSCCode": filing_meta.get("ifsc", ""),
                    "BankName": filing_meta.get("bank_name", ""),
                    "BankAccountNo": filing_meta.get("account_no", ""),
                    "UseForRefund": "true",
                }],
            },
        },
        "TDSonSalaries": {
            "TotalTDSonSalaries": sum(t.get("amount", 0) for t in tds_sal),
            "TDSonSalary": [{
                "EmployerOrDeductorOrCollectDetl": {
                    "TAN": t.get("tan", ""),
                    "EmployerOrDeductorOrCollecterName":
                        t.get("deductor", ""),
                },
                "IncChrgSal": heads["salary"]["amount"],
                "TotalTDSSal": t.get("amount", 0),
            } for t in tds_sal],
        },
        "TDSonOthThanSals": {
            "TotalTDSonOthThanSals": sum(t.get("amount", 0)
                                         for t in tds_oth),
            "TDSonOthThanSal": [{
                "EmployerOrDeductorOrCollectDetl": {
                    "TAN": t.get("tan", ""),
                    "EmployerOrDeductorOrCollecterName":
                        t.get("deductor", ""),
                },
                "AmtForTaxDeduct": 0,
                "ClaimOutOfTotTDSOnAmtPaid": t.get("amount", 0),
            } for t in tds_oth],
        },
        "ScheduleIT": {
            "TotalTaxPayments": sum(c.get("amount", 0) for c in challans),
            "TaxPayment": [{
                "BSRCode": c.get("bsr_code", ""),
                "DateDep": c.get("paid_on") or "",
                "SrlNoOfChaln": c.get("challan_no", ""),
                "Amt": c.get("amount", 0),
            } for c in challans],
        },
        "Verification": {
            "Declaration": {
                "AssesseeVerName": taxpayer["full_name"],
                "FatherName": filing_meta.get("father_name", ""),
                "AssesseeVerPAN": taxpayer["pan"],
            },
            "Capacity": "A",
            "Place": filing_meta.get("place", ""),
            "Date": filing_date,
        },
    }}}
    return quirks.apply(doc, "ITR-1", bundle.version)
