"""Form 26AS PART-C — tax paid by the assessee himself.

These rows carry a BSR code and challan serial instead of a TAN, which is
why the deductor scanner never saw them and every computation was missing
its advance / self-assessment tax credit.
"""

from itrcore.ingest import form26as_pdf as f26


def _row(sr, major, minor, total, bsr, deposited, serial):
    return [sr, major, minor, "0.00", "0.00", "0.00", "0.00", total, bsr,
            deposited, serial]


def test_advance_and_self_assessment_challans_are_read():
    rows = [
        _row("1", "0021", "100", "50,000.00", "0000002", "12-06-2025",
             "22222"),
        _row("2", "0021", "100", "1,50,000.00", "0000001", "13-09-2025",
             "33333"),
        _row("3", "0021", "300", "1,08,050.00", "0000001", "28-07-2026",
             "11111"),
    ]
    challans = [f26._challan_row(r) for r in rows]
    assert [c.kind for c in challans] == ["advance", "advance",
                                          "self_assessment"]
    assert [c.amount_paise for c in challans] == [5000000, 15000000, 10805000]
    assert challans[2].bsr_code == "0000001"
    assert challans[2].challan_serial == "11111"


def test_challans_shaped_for_the_computation():
    doc = f26.Form26AS(challans=[
        f26.Challan(minor_head="100", kind="advance", bsr_code="0000002",
                    challan_serial="22222", deposit_date="12-06-2025",
                    amount_paise=5000000),
        # 400 is tax on regular assessment — post-assessment, never a credit
        # against the return being prepared.
        f26.Challan(minor_head="400", kind="regular_assessment",
                    bsr_code="0000002", challan_serial="9",
                    deposit_date="01-01-2026", amount_paise=100000)])
    out = f26.challans_for_computation(doc)
    assert out == [{"kind": "advance", "paid_on": "2025-06-12",
                    "amount": 50000, "reference": "0000002 - 22222"}]


def test_a_deductor_row_is_not_mistaken_for_a_challan():
    # No BSR code and no deposit date → not a PART-C row.
    assert f26._challan_row(["1", "ACME LTD", "BLRA12345C", "1,00,000.00",
                             "10,000.00", "10,000.00"]) is None


def test_document_round_trips_challans():
    doc = f26.Form26AS(pan="AAAPL1234C", challans=[
        f26.Challan(minor_head="300", kind="self_assessment",
                    bsr_code="0000001", challan_serial="11111",
                    deposit_date="28-07-2026", amount_paise=10805000)])
    back = f26.document_from_dict(f26.document_to_dict(doc))
    assert back.challans == doc.challans
