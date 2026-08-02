"""PART-C of the TRACES text/CSV 26AS export — tax paid by the assessee.

These rows carry a BSR code and challan serial INSTEAD of a TAN, which is
why the deductor scan never saw them. Only this export and pre-AY-2023-24
statements still have the part at all; the current Annual Tax Statement PDF
dropped it in favour of AIS Part B3.
"""

import pytest

from itrcore.ingest import form26as

_TEXT = """\
PART A - Details of Tax Deducted at Source
Sr. No.^Name of Deductor^TAN of Deductor^Total Amount Paid^Total Tax Deducted
1^ACME LTD^BLRA12345C^10,00,000.00^1,00,000.00
PART C - Details of Tax Paid (other than TDS or TCS)
Sr.^Major Head^Minor Head^Tax^Surcharge^Cess^Others^Total^BSR^Date^Serial
1^0021^100^50,000.00^0.00^0.00^0.00^50,000.00^0000002^12-06-2025^22222
2^0021^300^1,08,050.00^0.00^0.00^0.00^1,08,050.00^0000001^28-07-2026^11111
PART D - Details of Paid Refund
1^No transactions present
"""


def _parsed():
    return form26as.parse_26as_text(_TEXT.encode())


def test_tds_rows_still_parse_alongside_part_c():
    assert [e["tan"] for e in _parsed()["entries"]] == ["BLRA12345C"]


def test_part_c_challans_are_read():
    got = _parsed()["challans"]
    assert got == [
        {"kind": "advance", "paid_on": "2025-06-12", "amount": 50000,
         "reference": "0000002 - 22222"},
        {"kind": "self_assessment", "paid_on": "2026-07-28", "amount": 108050,
         "reference": "0000001 - 11111"},
    ]


def test_part_c_scanning_stops_at_the_next_part():
    # The PART-D row after it must not be mistaken for a challan.
    assert len(_parsed()["challans"]) == 2


def test_minor_head_words_are_accepted_too():
    text = ("PART C - Details of Tax Paid (other than TDS or TCS)\n"
            "1^0021^Self Assessment Tax^7,470.00^0^0^0^7,470.00^0000003"
            "^31-07-2025^55555\n")
    got = form26as.parse_26as_text(text.encode())["challans"]
    assert got[0]["kind"] == "self_assessment"
    assert got[0]["amount"] == 7470


def test_a_statement_with_only_challans_is_not_an_error():
    text = ("PART C - Details of Tax Paid (other than TDS or TCS)\n"
            "1^0021^100^50,000.00^0^0^0^50,000.00^0000002^12-06-2025^22222\n")
    parsed = form26as.parse_26as_text(text.encode())
    assert parsed["entries"] == []
    assert len(parsed["challans"]) == 1


def test_a_statement_with_neither_still_raises():
    with pytest.raises(ValueError, match="no TDS rows recognised"):
        form26as.parse_26as_text(b"nothing^useful^here\n")
