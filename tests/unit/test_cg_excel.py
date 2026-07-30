"""Capital-gains Excel template: Genius Share Detail format build → fill →
parse → classify, plus the legacy standardized-template fallback."""

import io
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from itrcore.ingest import cg_excel

_GENIUS_XLS = (Path(__file__).resolve().parents[2] / "ITR filing data"
               / "Capital Gain Template Genius.xls")


def _genius_workbook(rows: list[list]) -> bytes:
    """A workbook using the Genius template's own 19 headers + ``rows``."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(cg_excel._G_HEADERS))
    for row in rows:
        ws.append(row + [None] * (19 - len(row)))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _legacy_workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Capital Gains"
    ws.append([title for _, title in cg_excel._HEADERS])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------- template --
def test_template_is_genius_layout_and_parses_empty():
    tpl = cg_excel.build_template()
    assert tpl[:2] == b"PK"                    # xlsx container
    wb = openpyxl.load_workbook(io.BytesIO(tpl))
    ws = wb[wb.sheetnames[0]]
    headers = [c.value for c in ws[1]]
    assert headers == list(cg_excel._G_HEADERS)
    # The four dropdowns, formatted as in the Genius file.
    dvs = {tuple(sorted(str(r) for r in dv.sqref.ranges)):
           dv.formula1 for dv in ws.data_validations.dataValidation}
    joined = " | ".join(dvs.values())
    assert '"Listed,Unlisted"' in joined
    assert '"Long-Term,Short-Term"' in joined
    assert "Equity Oriented Mutual Fund Units" in joined
    assert "Government Securities/ Other Mutual Fund" in joined
    assert sum('"Yes,No"' in f for f in dvs.values()) == 3   # F, M, N
    parsed = cg_excel.parse_cg_excel(tpl)
    assert parsed["rows"] == []


def test_parse_genius_rows_and_classification():
    data = _genius_workbook([
        # Listed equity, STT paid, held > 12 months → LTCG 112A.
        ["Listed", "Equity Shares", "Long-Term", "ITC LTD", 133650, "", "",
         "16/12/2025", 62138.08, "", "12/02/2021", 478.11, "Yes"],
        # Listed equity, STT paid, held < 12 months → STCG 111A.
        ["Listed", "Equity Shares", "Short-Term", "ENGINEERS INDIA LTD",
         16851, "", "", "15/04/2025", 25836.16, "", "17/05/2024", 156.97,
         "Yes"],
        # Listed debenture → listed_other.
        ["Listed", "Debentures/Bonds", "Long-Term", "NCD Series IV",
         200000, "", "", "01/10/2025", 150000, "", "01/01/2020", 0, "No",
         "Yes"],
    ])
    parsed = cg_excel.parse_cg_excel(data, ay="2026-27")
    assert parsed["errors"] == []
    r1, r2, r3 = parsed["rows"]
    assert r1["asset_class"] == "listed_equity_stt"
    assert r1["description"] == "ITC LTD"
    assert r1["transfer_date"] == "2025-12-16"
    assert r1["acquisition_date"] == "2021-02-12"
    assert r1["full_value_consideration"] == 133650
    assert r1["cost_of_acquisition"] == 62138
    assert r1["transfer_expenses"] == 478
    assert r2["asset_class"] == "listed_equity_stt"
    assert r3["asset_class"] == "listed_other"

    classified = cg_excel.classify_rows(parsed["rows"], "2026-27")
    assert classified[0]["term"] == "Long-term"
    assert "112A" in classified[0]["bucket"]
    assert classified[1]["term"] == "Short-term"
    assert "111A" in classified[1]["bucket"]
    assert classified[2]["term"] == "Long-term"
    assert "112" in classified[2]["bucket"]


def test_genius_category_mapping():
    data = _genius_workbook([
        ["Unlisted", "Equity Shares", "", "Pvt Ltd shares", 400000, "No",
         "", "01/09/2025", 100000, "", "01/01/2020", 0, "No"],
        ["Listed", "Equity Oriented Mutual Fund Units", "", "HDFC MF",
         500000, "", "", "01/09/2025", 200000, "", "01/01/2024", 0, "Yes"],
        ["Unlisted", "Debt Oriented Mutual Fund Units", "", "Debt fund",
         300000, "", "", "01/09/2025", 250000, "", "01/01/2020", 0, ""],
        ["Listed", "Zero Coupon Bonds", "", "ZCB", 100000, "", "",
         "01/09/2025", 60000, "", "01/01/2024", 0, ""],
        ["Unlisted", "Preference Shares", "", "Pref", 100000, "No", "",
         "01/09/2025", 60000, "", "01/01/2020", 0, ""],
    ])
    rows = cg_excel.parse_cg_excel(data)["rows"]
    assert [r["asset_class"] for r in rows] == [
        "unlisted", "equity_mf", "other", "listed_other", "unlisted"]


def test_genius_per_unit_112a_columns_derive_totals():
    # Sale amount and FMV-2018 left blank — derived from the per-unit
    # columns (units × price, units × FMV/unit), as Genius fills them.
    data = _genius_workbook([
        ["Listed", "Equity Shares", "Long-Term", "HDFC AMC", None, "", "",
         "30/06/2025", 355537.71, None, "04/12/2017", 3385.36, "Yes", "",
         "", "INE127D01025", 110, 5203.49, 2100.5],
    ])
    parsed = cg_excel.parse_cg_excel(data)
    row = parsed["rows"][0]
    assert row["full_value_consideration"] == round(110 * 5203.49)
    assert row["fmv_31jan2018"] == round(110 * 2100.5)


def test_genius_50ca_unquoted_fmv_becomes_consideration():
    data = _genius_workbook([
        ["Unlisted", "Equity Shares", "", "Closely-held Co", 400000, "Yes",
         500000, "01/09/2025", 100000, "", "01/01/2020", 0, "No"],
    ])
    parsed = cg_excel.parse_cg_excel(data)
    assert parsed["rows"][0]["full_value_consideration"] == 500000
    assert any("50CA" in e for e in parsed["errors"])


def test_genius_declared_term_cross_checked_against_dates():
    data = _genius_workbook([
        # Held 4+ years but marked Short-Term → warning, dates win.
        ["Listed", "Equity Shares", "Short-Term", "ITC LTD", 133650, "",
         "", "16/12/2025", 62138, "", "12/02/2021", 0, "Yes"],
        # Short-Term claimed with no purchase date → warning.
        ["Listed", "Equity Shares", "Short-Term", "No-date Co", 50000, "",
         "", "16/12/2025", 20000, "", None, 0, "Yes"],
    ])
    parsed = cg_excel.parse_cg_excel(data, ay="2026-27")
    assert any("dates imply long-term" in e for e in parsed["errors"])
    assert any("no date of purchase" in e for e in parsed["errors"])
    assert len(parsed["rows"]) == 2


def test_genius_94_7_8_flagged_not_applied():
    data = _genius_workbook([
        ["Listed", "Equity Shares", "Short-Term", "Strip Co", 100000, "",
         "", "01/09/2025", 120000, "", "01/05/2025", 0, "Yes", "", 15000],
    ])
    parsed = cg_excel.parse_cg_excel(data)
    assert parsed["rows"][0]["cost_of_acquisition"] == 120000   # untouched
    assert any("94(7)" in e for e in parsed["errors"])


def test_genius_rows_without_sale_date_skipped():
    data = _genius_workbook([
        ["Listed", "Equity Shares", "", "No sale date", 100000, "", "",
         None, 50000, "", "01/01/2020", 0, "Yes"],
    ])
    parsed = cg_excel.parse_cg_excel(data)
    assert parsed["rows"] == []
    assert any("no date of sale" in e for e in parsed["errors"])


@pytest.mark.skipif(not _GENIUS_XLS.exists(),
                    reason="reference Genius .xls not on this machine")
def test_original_genius_xls_parses():
    parsed = cg_excel.parse_cg_excel(_GENIUS_XLS.read_bytes())
    assert parsed["rows"] == []                # blank template, no errors
    assert parsed["errors"] == []


# ------------------------------------------- prefill from the AIS detail --
_NSDL_LABELS = ["TSN", "Date of Sale/Transfer",
                "Security Name (Security Code)", "Security Class",
                "Debit Type", "Credit Type", "Asset Type", "Quantity",
                "Sale Price Per unit", "Sales Consideration",
                "Cost of Acquisition", "Unit FMV", "Fair Market Value",
                "Indexed Cost of Acquisition", "Status", "Feedback"]
_KFIN_LABELS = ["TSN", "AMC Name (Code)", "Date of Sale/Transfer",
                "Security Class", "Security Name (Security Code)",
                "Debit Type", "Credit Type", "Asset Type", "Quantity",
                "Sale Price Per unit", "Sales Consideration", "STT",
                "Cost of Acquisition", "Unit FMV", "Fair Market Value",
                "Indexed Cost of Acquisition", "Status", "Feedback"]


def _ais_doc_with_sales():
    from itrcore.ingest import ais
    return ais.document_from_dict({"rows": [
        {"part": "B2", "part_title": "Part B2-SFT",
         "category": "Sale of securities and units of mutual fund",
         "category_code": "SS", "info_code": "SFT-17-LES(M)",
         "source": "National Securities Depository Limited", "count": 2,
         "amount_paise": 0, "derived_paise": None, "bucket": "x",
         "detail_labels": _NSDL_LABELS,
         "detail_rows": [
             ["141", "16/12/2025", "ITC LIMITED EQ NEW FV RE",
              "Listed Equity Share", "Market", "Market", "Long term",
              "330.00", "405.00", "1,33,650.00", "71,758.50", "0", "0",
              "0", "Active", "Optional"],
             ["142", "15/04/2025", "ENGINEERS INDIA LTD",
              "Listed Equity Share", "Market", "Market", "Short term",
              "100.00", "168.51", "16,851.00", "25,836.16", "0", "0",
              "0", "Active", "Optional"]]},
        {"part": "B2", "part_title": "Part B2-SFT",
         "category": "Sale of securities and units of mutual fund",
         "category_code": "SS", "info_code": "SFT-18-EMF(M)",
         "source": "KFin Technologies Pvt. Ltd", "count": 1,
         "amount_paise": 0, "derived_paise": None, "bucket": "x",
         "detail_labels": _KFIN_LABELS,
         "detail_rows": [
             ["143", "NIPPON INDIA MUTUAL FUND", "21/07/2025",
              "Unit of Equity Oriented Mutual Fund",
              "NIPPON INDIA GROWTH MID CAP FUND", "AMC (redemption)",
              "AMC (purchase)", "Long term", "6.55", "4,597.36",
              "30,112.68", "0.30", "24,999.55", "0", "0", "0", "Active",
              "Optional"]]},
    ]})


def test_prefill_from_ais_maps_depository_detail():
    rows = cg_excel.prefill_from_ais(_ais_doc_with_sales())
    assert len(rows) == 3
    itc, eil, mf = rows
    assert itc["listed"] == "Listed"
    assert itc["category"] == "Equity Shares"
    assert itc["term"] == "Long-Term"
    assert itc["name"] == "ITC LIMITED EQ NEW FV RE"
    assert itc["sale_amount"] == 133650.0
    assert itc["sale_date"] == "16/12/2025"
    assert itc["purchase_cost"] == 71758.5    # depository-reported cost
    assert itc["stt"] == "Yes"                # on-market debit
    assert itc["units"] == 330.0
    assert eil["term"] == "Short-Term"
    assert mf["category"] == "Equity Oriented Mutual Fund Units"
    assert mf["stt"] == "Yes"                 # STT column carries 0.30
    assert mf["purchase_cost"] == 24999.55


def test_prefilled_template_roundtrips_through_parse():
    rows = cg_excel.prefill_from_ais(_ais_doc_with_sales())
    filled = cg_excel.build_template(prefill=rows)
    parsed = cg_excel.parse_cg_excel(filled, ay="2026-27")
    assert len(parsed["rows"]) == 3
    r_itc, r_eil, r_mf = parsed["rows"]
    assert r_itc["asset_class"] == "listed_equity_stt"
    assert r_itc["full_value_consideration"] == 133650
    assert r_itc["cost_of_acquisition"] == 71758   # round-half-even
    assert r_itc["transfer_date"] == "2025-12-16"
    assert r_mf["asset_class"] == "equity_mf"
    # The AIS carries no purchase date: the declared Short-Term row must
    # prompt staff for one instead of silently going long-term.
    assert any("no date of purchase" in e for e in parsed["errors"])


def test_computation_rows_from_ais_carry_term_override():
    derived = cg_excel.computation_rows_from_ais(_ais_doc_with_sales())
    rows = derived["rows"]
    assert len(rows) == 3
    itc, eil, mf = rows
    assert itc["asset_class"] == "listed_equity_stt"
    assert itc["transfer_date"] == "2025-12-16"
    assert itc["acquisition_date"] is None
    assert itc["term_override"] == "long"
    assert itc["full_value_consideration"] == 133650
    assert itc["cost_of_acquisition"] == 71758
    assert eil["term_override"] == "short"
    assert mf["asset_class"] == "equity_mf"
    assert any("purchase dates" in n for n in derived["notes"])
    # classify follows the AIS's own term even with no purchase date
    classified = cg_excel.classify_rows(rows, "2026-27")
    assert classified[0]["term"] == "Long-term"
    assert classified[1]["term"] == "Short-term"
    assert "111A" in classified[1]["bucket"]


def test_engine_honours_term_override_for_undated_rows():
    from itrcore.tax import serde
    from itrcore.tax.engine import compute_assessment

    inputs = serde.inputs_from_dict({"ay": "2026-27", "capital_gains": [
        {"asset_class": "listed_equity_stt", "description": "AIS sale",
         "transfer_date": "2025-09-01", "acquisition_date": None,
         "term_override": "short", "full_value_consideration": 100000,
         "cost_of_acquisition": 40000}]})
    cg = compute_assessment(inputs).new.heads["capital_gains"]
    # Without the override this undated row would be assumed long-term.
    assert cg.stcg_111a == 60000
    assert cg.ltcg_112a_taxable == 0


# ------------------------------------------------------- legacy fallback --
def test_legacy_template_still_parses():
    data = _legacy_workbook([
        ["Listed equity share (STT paid)", "Infosys shares",
         date(2023, 5, 10), date(2025, 9, 1), 800000, None, 300000, 0,
         2000, 450000, "", 0],
        ["Immovable property (land/building)", "Jaipur plot",
         date(2024, 11, 1), date(2025, 12, 15), 5000000, 6000000,
         3500000, 200000, 50000, None, "54F", 1000000],
    ])
    parsed = cg_excel.parse_cg_excel(data)
    assert parsed["errors"] == []
    r1, r2 = parsed["rows"]
    assert r1["asset_class"] == "listed_equity_stt"
    assert r1["fmv_31jan2018"] == 450000
    assert r2["asset_class"] == "property"
    assert r2["stamp_duty_value"] == 6000000
    assert r2["exemption_section"] == "54F"
    classified = cg_excel.classify_rows(parsed["rows"], "2026-27")
    assert classified[0]["term"] == "Long-term"
    assert classified[1]["term"] == "Short-term"


def test_legacy_bad_rows_flagged_and_skipped():
    data = _legacy_workbook([
        ["Unlisted share", "no sale date", date(2020, 1, 1), None,
         100000, "", 50000, "", "", "", "", ""],
        ["Other capital asset", "bad section", date(2020, 1, 1),
         date(2025, 8, 1), 100000, "", 50000, "", "", "", "80C", 5000],
    ])
    parsed = cg_excel.parse_cg_excel(data)
    assert len(parsed["rows"]) == 1
    assert parsed["rows"][0]["exemption_section"] == ""
    assert any("no transfer" in e for e in parsed["errors"])
    assert any("80C" in e for e in parsed["errors"])


def test_parse_rejects_unrecognised_sheet():
    wb = openpyxl.Workbook()
    wb.active.append(["Just", "random", "columns"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="no header row"):
        cg_excel.parse_cg_excel(buf.getvalue())


def test_imported_genius_rows_flow_through_the_engine():
    from itrcore.tax import serde
    from itrcore.tax.engine import compute_assessment

    data = _genius_workbook([
        ["Listed", "Equity Shares", "Long-Term", "Bluechip", 900000, "",
         "", "01/10/2025", 400000, "", "01/01/2023", 0, "Yes"],
    ])
    rows = cg_excel.parse_cg_excel(data)["rows"]
    inputs = serde.inputs_from_dict({"ay": "2026-27",
                                     "capital_gains": rows})
    result = compute_assessment(inputs)
    cg = result.new.heads["capital_gains"]
    # LTCG 5,00,000 − 1,25,000 exemption → 3,75,000 taxable u/s 112A.
    assert cg.ltcg_112a_taxable == 375000
