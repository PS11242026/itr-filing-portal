"""AIS Part B3 — 'Payment of Taxes': advance tax and self-assessment tax.

Since the AY 2023-24 redesign the TRACES Form 26AS carries only TDS/TCS and
refunds (its Part VII says in terms "for other details refer AIS"), so the
AIS is the only pulled source for these challans. B3 is shaped unlike every
other AIS block — a flat columnLabel/columnData table with no ``l2`` summary
— which is how every challan came to be dropped in silence.
"""

import pytest

from itrcore.ingest import ais

_B3_LABELS = ["Financial Year", "Major Head", "Minor Head", "Tax (A)",
              "Surcharge (B)", "Education Cess (C)", "Others (D)",
              "Total (A+B+C+D)", "BSR Code", "Date Of Deposit",
              "Challan Serial Number", "Challan Identification Number"]


def _b3(*rows):
    return {"sectionKey": "paymentOfTaxes",
            "title": "Part B3-Information relating to payment of taxes",
            "heading": "Payment of Taxes",
            "elements": [{"title": "", "columnLabel": _B3_LABELS,
                          "columnData": list(rows)}]}


def _row(fy, minor, total, bsr, deposited, serial):
    return [fy, "Income Tax (Other than Companies)", minor, total, "0", "0",
            "0", total, bsr, deposited, serial, f"{serial}SBIN"]


def _doc(*rows):
    return ais._parse_v15({"metadata": {"jsonVersion": "15.0.0"},
                           "partA": {"columnLabel": [], "columnData": []},
                           "partB": {"sections": [_b3(*rows)]}})


def test_b3_challans_are_parsed_despite_having_no_l2_block():
    doc = _doc(_row("2024-25", "Self Assessment", "1,87,060", "0000003",
                    "12/09/2025", "44444"))
    assert len(doc.tax_payments) == 1
    p = doc.tax_payments[0]
    assert p.kind == "self_assessment"
    assert p.amount_paise == 18706000
    assert p.bsr_code == "0000003"
    assert p.challan_serial == "44444"


def test_minor_head_decides_the_kind():
    doc = _doc(_row("2025-26", "Advance Tax", "50,000", "0000002",
                    "12/06/2025", "22222"),
               _row("2025-26", "Self Assessment", "1,08,050", "0000001",
                    "28/07/2026", "11111"),
               _row("2024-25", "Tax on Regular Assessment", "9,000",
                    "0000002", "01/01/2026", "9"))
    assert [p.kind for p in doc.tax_payments] == [
        "advance", "self_assessment", "regular_assessment"]


def test_challan_belongs_to_the_year_it_relates_to_not_the_year_paid():
    """The whole point. Self-assessment tax for AY 2026-27 is paid in
    July 2026 — inside FY 2026-27 — but the row is stamped FY 2025-26 and
    discharges AY 2026-27. Crediting on the deposit date would put it
    against the wrong year."""
    doc = _doc(_row("2025-26", "Self Assessment", "1,08,050", "0000001",
                    "28/07/2026", "11111"))
    assert doc.tax_payments[0].ay == "2026-27"

    mine, others = ais.challans_for_ay(doc, "2026-27")
    assert mine == [{"kind": "self_assessment", "paid_on": "2026-07-28",
                     "amount": 108050, "reference": "0000001 - 11111"}]
    assert others == []


def test_another_years_challan_is_reported_but_never_credited():
    doc = _doc(_row("2024-25", "Self Assessment", "1,87,060", "0000003",
                    "12/09/2025", "44444"))
    mine, others = ais.challans_for_ay(doc, "2026-27")
    assert mine == []
    assert [p.ay for p in others] == ["2025-26"]


def test_regular_assessment_tax_is_not_a_credit_against_the_return():
    doc = _doc(_row("2025-26", "Tax on Regular Assessment", "9,000",
                    "0000002", "01/01/2026", "9"))
    mine, others = ais.challans_for_ay(doc, "2026-27")
    assert mine == [] and others == []


def test_tax_payments_survive_persistence():
    doc = _doc(_row("2025-26", "Advance Tax", "1,50,000", "0000002",
                    "13/09/2025", "33333"))
    back = ais.document_from_dict(ais.document_to_dict(doc))
    assert back.tax_payments == doc.tax_payments


def test_b3_does_not_leak_into_the_income_rows():
    doc = _doc(_row("2025-26", "Advance Tax", "1,50,000", "0000002",
                    "13/09/2025", "33333"))
    assert doc.rows == ()
    assert doc.category_totals() == {}


@pytest.mark.parametrize("fy,expected", [("2025-26", "2026-27"),
                                         ("2024-25", "2025-26"),
                                         ("2099-00", "2100-01"),
                                         ("", ""), ("rubbish", "")])
def test_fy_to_ay(fy, expected):
    assert ais.TaxPayment(fy=fy).ay == expected
