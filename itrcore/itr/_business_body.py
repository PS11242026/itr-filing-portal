"""Shared reduced-but-faithful body for the business returns (ITR-3 / ITR-4).

Same structure as the ITR-2 builder plus Schedule BP and the business head in
PartB-TI; only the form key ('ITR3' / 'ITR4') differs. The per-form modules
(itr3.py / itr4.py) wrap this and run quirks.apply. Official-schema key deltas
belong in quirks.py, not here.
"""

from datetime import date

from itrcore.itr.itr1 import _applied_via, _line_amount, _name_parts
from itrcore.tax.rounding import pct


def _schedule_bp(inputs_dict: dict, biz_amount: int) -> dict:
    presumptive = inputs_dict.get("businesses", [])
    regular = inputs_dict.get("regular_businesses", [])
    return {
        "Nature44AD": [{
            "TradeName": b.get("name", ""),
            "GrsTrnOverBnk": b.get("turnover_digital", 0),
            "GrsTrnOverCash": b.get("turnover_cash", 0),
        } for b in presumptive if b.get("section") == "44AD"],
        "Nature44ADA": [{
            "TradeName": b.get("name", ""),
            "GrossReceipts": b.get("gross_receipts", 0),
        } for b in presumptive if b.get("section") == "44ADA"],
        "Nature44AE": [{"TradeName": b.get("name", "")}
                       for b in presumptive if b.get("section") == "44AE"],
        "RegularBooks": [{
            "TradeName": rb.get("name", ""),
            "NetProfitBeforeTax": rb.get("net_profit_before_tax", 0),
        } for rb in regular],
        "IncChargeableUnderBP": biz_amount,
    }


def build_body(form_key: str, form_name: str, *, taxpayer: dict,
               inputs_dict: dict, result_dict: dict, regime: str,
               filing_meta: dict, bundle) -> dict:
    res = result_dict[regime]
    heads = res["heads"]
    sal_lines = heads["salary"].get("lines", [])
    cg = heads["capital_gains"]
    os_head = heads["other_sources"]
    os_inputs = inputs_dict.get("other_sources", {})

    gross_salary = sum(s.get("gross_salary", 0)
                       for s in inputs_dict.get("salaries", []))
    exempt = sum(s.get("exempt_allowances", 0)
                 for s in inputs_dict.get("salaries", []))
    std16ia = _line_amount(sal_lines, "16(ia)")
    prof16iii = _line_amount(sal_lines, "16(iii)")

    hp_details = []
    for hp in inputs_dict.get("house_properties", []):
        gav = max(0, hp.get("annual_rent_received", 0))
        muni = min(max(0, hp.get("municipal_taxes_paid", 0)), gav)
        nav = gav - muni
        interest = (hp.get("interest_24b", 0)
                    + hp.get("pre_construction_interest_total", 0) // 5)
        hp_details.append({
            "IfLetOut": "N" if hp.get("kind") == "self_occupied" else "Y",
            "AddressDetail": hp.get("address", ""),
            "GrossRentReceived": gav,
            "TaxPaidlocalAuth": muni,
            "AnnualValue": nav,
            "StandardDeduction": pct(nav, 30),
            "InterestPayable": interest,
        })

    stcg_total = cg.get("stcg_slab", 0) + cg.get("stcg_111a", 0)
    ltcg_total = (cg.get("ltcg_112a_taxable", 0) + cg.get("ltcg_125", 0)
                  + cg.get("ltcg_20_indexed", 0))
    si_rows = []
    for sec_code, income, rate in (("111A", cg.get("stcg_111a", 0), 20),
                                   ("112A", cg.get("ltcg_112a_taxable", 0),
                                    12.5),
                                   ("112", cg.get("ltcg_125", 0), 12.5),
                                   ("112", cg.get("ltcg_20_indexed", 0), 20)):
        if income > 0:
            si_rows.append({"SecCode": sec_code, "SplRateInc": income,
                            "SplRateIncTax": pct(income, rate)})

    via_applied = _applied_via(res.get("deduction_lines", []))
    via_block = {**{k.replace("(", "").replace(")", ""): v
                    for k, v in via_applied.items()},
                 "TotalChapVIADeductions": res["deductions_total"]}

    tds_entries = inputs_dict.get("tds", [])
    tds_total = sum(t.get("amount", 0) for t in tds_entries)
    challans = inputs_dict.get("challans", [])
    interest_total = (res["interest_234a"] + res["interest_234b"]
                      + res["interest_234c"] + res["fee_234f"])
    payable = res["payable_or_refund"]
    filing_date = filing_meta.get("filing_date") or date.today().isoformat()

    body = {
        "CreationInfo": {
            "SWVersionNo": "1.0", "SWCreatedBy": "SW00000000",
            "JSONCreationDate": date.today().isoformat(),
            "JSONCreatedBy": "SW00000000", "Digest": "-",
        },
        f"Form_{form_key}": {
            "FormName": form_name, "AssessmentYear": "2026",
            "SchemaVer": bundle.version, "FormVer": bundle.version,
        },
        "PartA_GEN1": {
            "PersonalInfo": {
                "AssesseeName": _name_parts(taxpayer["full_name"]),
                "PAN": taxpayer["pan"],
                "DOB": taxpayer.get("dob") or "",
                "AadhaarCardNo": taxpayer.get("aadhaar") or "",
                "Address": {
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
            },
        },
        "ScheduleS": {
            "TotalGrossSalary": gross_salary,
            "AllwncExemptUs10": exempt,
            "NetSalary": max(0, gross_salary - exempt),
            "DeductionUS16": std16ia + prof16iii,
            "TotIncUnderHeadSalaries": heads["salary"]["amount"],
        },
        "ScheduleHP": {
            "PropertyDetails": hp_details,
            "TotalIncomeChargeableUnHP": heads["house_property"]["amount"],
        },
        "ScheduleBP": _schedule_bp(inputs_dict, heads["business"]["amount"]),
        "ScheduleCG": {
            "ShortTermCapGain": {
                "STCG111A": cg.get("stcg_111a", 0),
                "STCGOthers": cg.get("stcg_slab", 0),
                "TotalSTCG": stcg_total,
            },
            "LongTermCapGain": {
                "LTCG112A": cg.get("ltcg_112a_taxable", 0),
                "LTCG112Others": cg.get("ltcg_125", 0),
                "LTCG20PercentIndexed": cg.get("ltcg_20_indexed", 0),
                "TotalLTCG": ltcg_total,
            },
            "TotScheduleCG": cg.get("head_total", 0),
        },
        "ScheduleOS": {
            "InterestGross": (os_inputs.get("savings_interest", 0)
                              + os_inputs.get("fd_interest", 0)),
            "DividendGross": os_inputs.get("dividend", 0),
            "FamilyPension": os_inputs.get("family_pension", 0),
            "GiftsUs562x": os_inputs.get("taxable_gifts", 0),
            "OthersGross": os_inputs.get("other", 0),
            "TotalIncomeFromOS": os_head["amount"],
        },
        "ScheduleVIA": {
            "UsrDeductUndChapVIA": dict(via_block),
            "DeductUndChapVIA": dict(via_block),
        },
        "ScheduleSI": {"SplCodeRateTax": si_rows},
        "ScheduleTDS": {
            "TotalTDS": tds_total,
            "TDSDetails": [{
                "TAN": t.get("tan", ""),
                "DeductorName": t.get("deductor", ""),
                "Section": t.get("section", ""),
                "TDSAmount": t.get("amount", 0),
            } for t in tds_entries],
        },
        "ScheduleIT": {
            "TotalTaxPayments": sum(c.get("amount", 0) for c in challans),
            "TaxPayment": [{
                "DateDep": c.get("paid_on") or "",
                "Amt": c.get("amount", 0),
                "Kind": c.get("kind", "advance"),
            } for c in challans],
        },
        "PartB_TI": {
            "Salaries": heads["salary"]["amount"],
            "IncomeFromHP": heads["house_property"]["amount"],
            "ProfBusinessGain": heads["business"]["amount"],
            "CapGain": {
                "ShortTermCapGainTotal": stcg_total,
                "LongTermCapGainTotal": ltcg_total,
                "CapGainTotal": cg.get("head_total", 0),
            },
            "IncFromOS": os_head["amount"],
            "GrossTotalIncome": res["gti"],
            "DeductionsUnderScheduleVIA": res["deductions_total"],
            "TotalIncome": res["total_income"],
            "IncChargeableTaxSplRates": sum(r["SplRateInc"] for r in si_rows),
        },
        "PartB_TTI": {
            "ComputationOfTaxLiability": {
                "TaxAtNormalRatesOnAggrInc": res["slab_tax"],
                "TaxAtSpecialRates": res["special_tax"],
                "Rebate87A": res["rebate_87a"],
                "TaxPayableAfterRebate": max(
                    0, res["slab_tax"] + res["special_tax"]
                    - res["rebate_87a"]),
                "Surcharge": res["surcharge"],
                "MarginalRelief": res["marginal_relief"],
                "EducationCess": res["cess"],
                "GrossTaxLiability": res["tax_liability"],
                "NetTaxLiability": res["tax_liability"],
                "IntrstPay": {
                    "IntrstPayUs234A": res["interest_234a"],
                    "IntrstPayUs234B": res["interest_234b"],
                    "IntrstPayUs234C": res["interest_234c"],
                    "LateFilingFee234F": res["fee_234f"],
                },
                "AggregateTaxInterestLiability": (res["tax_liability"]
                                                  + interest_total),
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
    }
    return {"ITR": {form_key: body}}
