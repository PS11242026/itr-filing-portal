"""Layers 2 & 3: cross-field business checks + round-trip re-extraction.

Form-aware: ITR-2/3/4 share the PartB body shape; ITR-1 is separate.
"""

from itrcore.validation import validate_ifsc

_PARTB_FORMS = ("ITR2", "ITR3", "ITR4")


def check(json_dict: dict, *, expected_pan: str,
          regime_result: dict) -> list[str]:
    itr = json_dict.get("ITR", {})
    for key in _PARTB_FORMS:
        if key in itr:
            return _check_partb(json_dict, key, expected_pan=expected_pan,
                                regime_result=regime_result)
    errors: list[str] = []
    itr1 = json_dict["ITR"]["ITR1"]
    inc = itr1["ITR1_IncomeDeductions"]
    tax = itr1["ITR1_TaxComputation"]
    paid = itr1["TaxPaid"]["TaxesPaid"]

    if itr1["PersonalInfo"]["PAN"] != expected_pan:
        errors.append("PersonalInfo.PAN does not match the taxpayer's PAN")
    if itr1["Verification"]["Declaration"]["AssesseeVerPAN"] != expected_pan:
        errors.append("Verification PAN does not match the taxpayer's PAN")

    if inc["GrossTotIncome"] != regime_result["gti"]:
        errors.append(f"GrossTotIncome {inc['GrossTotIncome']:,} ≠ "
                      f"computed GTI {regime_result['gti']:,}")
    if inc["TotalIncome"] != regime_result["total_income"]:
        errors.append(f"TotalIncome {inc['TotalIncome']:,} ≠ computed "
                      f"{regime_result['total_income']:,}")
    via = inc["UsrDeductUndChapVIA"]["TotalChapVIADeductions"]
    if via != regime_result["deductions_total"]:
        errors.append("Chapter VI-A total mismatch")

    tds_sched = (itr1.get("TDSonSalaries", {})
                 .get("TotalTDSonSalaries", 0)
                 + itr1.get("TDSonOthThanSals", {})
                 .get("TotalTDSonOthThanSals", 0))
    if tds_sched != paid["TDS"]:
        errors.append(f"TDS schedules total {tds_sched:,} ≠ TDS credit "
                      f"claimed {paid['TDS']:,}")

    expected_total_paid = (paid["AdvanceTax"] + paid["TDS"] + paid["TCS"]
                           + paid["SelfAssessmentTax"])
    if paid["TotalTaxesPaid"] != expected_total_paid:
        errors.append("TotalTaxesPaid is not the sum of its components")

    if (tax["NetTaxLiability"] + tax["TotalIntrstPay"]
            != tax["TotTaxPlusIntrstPay"]):
        errors.append("TotTaxPlusIntrstPay ≠ NetTaxLiability + interest")

    bal = itr1["TaxPaid"]["BalTaxPayable"]
    refund = itr1["Refund"]["RefundDue"]
    if bal and refund:
        errors.append("Both BalTaxPayable and RefundDue are non-zero")
    if bal - refund != regime_result["payable_or_refund"]:
        errors.append("Payable/refund does not match the computation")

    if refund > 0:
        bank = itr1["Refund"]["BankAccountDtls"]["AddtnlBankDetails"]
        if not bank or not bank[0].get("BankAccountNo"):
            errors.append("Refund due but no bank account provided")
        else:
            try:
                validate_ifsc(bank[0].get("IFSCCode", ""))
            except ValueError:
                errors.append("Refund bank IFSC is invalid")
    return errors


def _check_partb(json_dict: dict, form_key: str, *, expected_pan: str,
                 regime_result: dict) -> list[str]:
    errors: list[str] = []
    itr2 = json_dict["ITR"][form_key]
    ti_block = itr2["PartB_TI"]
    tti = itr2["PartB_TTI"]
    comp = tti["ComputationOfTaxLiability"]
    paid = tti["TaxPaid"]["TaxesPaid"]

    if itr2["PartA_GEN1"]["PersonalInfo"]["PAN"] != expected_pan:
        errors.append("PersonalInfo.PAN does not match the taxpayer's PAN")
    if itr2["Verification"]["Declaration"]["AssesseeVerPAN"] != expected_pan:
        errors.append("Verification PAN does not match the taxpayer's PAN")

    if ti_block["GrossTotalIncome"] != regime_result["gti"]:
        errors.append(f"GrossTotalIncome {ti_block['GrossTotalIncome']:,} "
                      f"≠ computed GTI {regime_result['gti']:,}")
    if ti_block["TotalIncome"] != regime_result["total_income"]:
        errors.append(f"TotalIncome {ti_block['TotalIncome']:,} ≠ computed "
                      f"{regime_result['total_income']:,}")
    if (ti_block["DeductionsUnderScheduleVIA"]
            != regime_result["deductions_total"]):
        errors.append("Chapter VI-A total mismatch")

    heads = regime_result["heads"]
    if ti_block["Salaries"] != heads["salary"]["amount"]:
        errors.append("PartB-TI Salaries ≠ salary head")
    if ti_block["IncomeFromHP"] != heads["house_property"]["amount"]:
        errors.append("PartB-TI HP ≠ house-property head")
    if (ti_block["CapGain"]["CapGainTotal"]
            != heads["capital_gains"].get("head_total", 0)):
        errors.append("PartB-TI capital gains ≠ CG head")
    if ti_block["IncFromOS"] != heads["other_sources"]["amount"]:
        errors.append("PartB-TI other sources ≠ OS head")
    if ("ProfBusinessGain" in ti_block
            and ti_block["ProfBusinessGain"] != heads["business"]["amount"]):
        errors.append("PartB-TI business income ≠ business head")

    tds_sched = itr2.get("ScheduleTDS", {}).get("TotalTDS", 0)
    if tds_sched != paid["TDS"]:
        errors.append(f"Schedule TDS total {tds_sched:,} ≠ TDS credit "
                      f"claimed {paid['TDS']:,}")
    expected_total_paid = (paid["AdvanceTax"] + paid["TDS"] + paid["TCS"]
                           + paid["SelfAssessmentTax"])
    if paid["TotalTaxesPaid"] != expected_total_paid:
        errors.append("TotalTaxesPaid is not the sum of its components")

    if comp["GrossTaxLiability"] != regime_result["tax_liability"]:
        errors.append("GrossTaxLiability ≠ computed tax liability")

    bal = tti["TaxPaid"]["BalTaxPayable"]
    refund = tti["Refund"]["RefundDue"]
    if bal and refund:
        errors.append("Both BalTaxPayable and RefundDue are non-zero")
    if bal - refund != regime_result["payable_or_refund"]:
        errors.append("Payable/refund does not match the computation")
    if refund > 0:
        bank = tti["Refund"]["BankAccountDtls"].get("AddtnlBankDetails",
                                                    [])
        if not bank or not bank[0].get("BankAccountNo"):
            errors.append("Refund due but no bank account provided")
        else:
            try:
                validate_ifsc(bank[0].get("IFSCCode", ""))
            except ValueError:
                errors.append("Refund bank IFSC is invalid")
    return errors


def extract_key_figures(json_dict: dict) -> dict:
    itr = json_dict.get("ITR", {})
    for key in _PARTB_FORMS:
        if key in itr:
            body = itr[key]
            return {
                "gti": body["PartB_TI"]["GrossTotalIncome"],
                "total_income": body["PartB_TI"]["TotalIncome"],
                "tax_liability": body["PartB_TTI"]
                                 ["ComputationOfTaxLiability"]
                                 ["NetTaxLiability"],
                "tds": body["PartB_TTI"]["TaxPaid"]["TaxesPaid"]["TDS"],
                "payable": body["PartB_TTI"]["TaxPaid"]["BalTaxPayable"],
                "refund": body["PartB_TTI"]["Refund"]["RefundDue"],
            }
    itr1 = json_dict["ITR"]["ITR1"]
    return {
        "gti": itr1["ITR1_IncomeDeductions"]["GrossTotIncome"],
        "total_income": itr1["ITR1_IncomeDeductions"]["TotalIncome"],
        "tax_liability": itr1["ITR1_TaxComputation"]["NetTaxLiability"],
        "tds": itr1["TaxPaid"]["TaxesPaid"]["TDS"],
        "payable": itr1["TaxPaid"]["BalTaxPayable"],
        "refund": itr1["Refund"]["RefundDue"],
    }


def round_trip_check(json_dict: dict, regime_result: dict) -> list[str]:
    fig = extract_key_figures(json_dict)
    errors = []
    pairs = [
        ("gti", regime_result["gti"]),
        ("total_income", regime_result["total_income"]),
        ("tax_liability", regime_result["tax_liability"]),
        ("tds", regime_result["tds_total"]),
    ]
    for name, expected in pairs:
        if fig[name] != expected:
            errors.append(f"round-trip {name}: JSON has {fig[name]:,}, "
                          f"computation says {expected:,}")
    net = fig["payable"] - fig["refund"]
    if net != regime_result["payable_or_refund"]:
        errors.append(f"round-trip payable/refund: JSON nets {net:,}, "
                      f"computation says "
                      f"{regime_result['payable_or_refund']:,}")
    return errors
