"""Advance / self-assessment tax reaches the computation as a credit.

The failure this pins: a computation showing tax payable that the client
had already settled u/s 140A. Two things caused it — the AIS Part B3 block
was skipped by the parser, and even once parsed, a challan is reported in
the AIS of the year it was PAID while it discharges the year it RELATES
to, so the AY being filed must be matched on the row's own financial year.
"""

from itrcore.analysis import autofill
from itrcore.authz import mint_scope
from itrcore.db.repos import importers as importers_repo
from itrcore.ingest import ais

_LABELS = ["Financial Year", "Major Head", "Minor Head", "Tax (A)",
           "Surcharge (B)", "Education Cess (C)", "Others (D)",
           "Total (A+B+C+D)", "BSR Code", "Date Of Deposit",
           "Challan Serial Number", "Challan Identification Number"]


def _ais_doc(*rows):
    tree = {"metadata": {"jsonVersion": "15.0.0"},
            "partA": {"columnLabel": [], "columnData": []},
            "partB": {"sections": [{
                "sectionKey": "paymentOfTaxes",
                "title": "Part B3-Information relating to payment of taxes",
                "heading": "Payment of Taxes",
                "elements": [{"title": "", "columnLabel": _LABELS,
                              "columnData": list(rows)}]}]}}
    return ais.document_to_dict(ais._parse_v15(tree))


def _row(fy, minor, total, bsr, deposited, serial):
    return [fy, "Income Tax (Other than Companies)", minor, total, "0", "0",
            "0", total, bsr, deposited, serial, f"{serial}SBIN"]


def _assemble(partner, taxpayer_id, ay="2026-27"):
    return autofill.assemble(mint_scope(partner, taxpayer_id), ay, base={})


def test_advance_tax_for_this_ay_is_credited(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers_repo.save_ais_document(scope, "2026-27", _ais_doc(
        _row("2025-26", "Advance Tax", "50,000", "0000002", "12/06/2025",
             "22222"),
        _row("2025-26", "Advance Tax", "1,50,000", "0000001", "13/09/2025",
             "33333")), "test")
    got = _assemble(partner, taxpayer_id).inputs["challans"]
    assert [(c["kind"], c["amount"], c["paid_on"]) for c in got] == [
        ("advance", 50000, "2025-06-12"), ("advance", 150000, "2025-09-13")]


def test_self_assessment_tax_paid_in_july_comes_from_next_years_ais(
        partner, taxpayer_id):
    """The real-world case. SAT for AY 2026-27 is paid 28-07-2026, which
    falls in FY 2026-27, so it is reported in the AY 2027-28 AIS — stamped
    FY 2025-26. Reading only the AY 2026-27 document can never find it."""
    scope = mint_scope(partner, taxpayer_id)
    importers_repo.save_ais_document(scope, "2026-27", _ais_doc(
        _row("2024-25", "Self Assessment", "1,87,060", "0000003",
             "12/09/2025", "44444")), "test")
    importers_repo.save_ais_document(scope, "2027-28", _ais_doc(
        _row("2025-26", "Self Assessment", "1,08,050", "0000001",
             "28/07/2026", "11111")), "test")

    res = _assemble(partner, taxpayer_id)
    assert res.inputs["challans"] == [
        {"kind": "self_assessment", "paid_on": "2026-07-28",
         "amount": 108050, "reference": "0000001 - 11111"}]
    # …and the AY 2025-26 challan sitting in the same store is NOT credited.
    assert any("belongs to AY 2025-26" in f for f in res.follow_ups)


def test_another_years_challan_is_never_silently_credited(partner,
                                                          taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers_repo.save_ais_document(scope, "2026-27", _ais_doc(
        _row("2024-25", "Self Assessment", "1,87,060", "0000003",
             "12/09/2025", "44444")), "test")
    res = _assemble(partner, taxpayer_id)
    assert res.inputs["challans"] == []
    assert any("belongs to AY 2025-26" in f for f in res.follow_ups)
    assert any("No self-assessment tax for AY 2026-27" in f
               for f in res.follow_ups)


def test_manually_entered_challan_is_not_duplicated_by_the_import(
        partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers_repo.save_ais_document(scope, "2026-27", _ais_doc(
        _row("2025-26", "Advance Tax", "50,000", "0000002", "12/06/2025",
             "22222")), "test")
    base = {"challans": [{"kind": "advance", "paid_on": "2025-06-12",
                          "amount": 50000, "reference": ""}]}
    got = autofill.assemble(scope, "2026-27", base=base).inputs["challans"]
    assert len(got) == 1


def test_part_c_of_a_text_26as_reaches_the_computation(partner, taxpayer_id):
    """The other supported source: PART-C of the TRACES text/CSV export,
    which persists no document of its own. Import → Auto-fill → the credit
    lands in the computation and the payable closes to nil."""
    from itrcore.ingest import form26as
    from itrcore.tax import serde
    from itrcore.tax.engine import compute_assessment

    scope = mint_scope(partner, taxpayer_id)
    text = ("PART C - Details of Tax Paid (other than TDS or TCS)\n"
            "1^0021^300^1,08,050.00^0^0^0^1,08,050.00^0000001"
            "^28-07-2026^11111\n")
    parsed = form26as.parse_26as_text(text.encode())
    importers_repo.import_challans(scope, "2026-27", parsed["challans"])

    inputs = autofill.assemble(scope, "2026-27", base={}).inputs
    assert inputs["challans"] == [
        {"kind": "self_assessment", "paid_on": "2026-07-28",
         "amount": 108050, "reference": "0000001 - 11111"}]

    # …and it is actually credited, not merely stored.
    inputs = {**inputs, "ay": "2026-27",
              "salaries": [{"employer_name": "X", "gross_salary": 2000000}]}
    before = compute_assessment(serde.inputs_from_dict(
        {**inputs, "challans": []})).new.payable_or_refund
    after = compute_assessment(serde.inputs_from_dict(inputs)).new
    assert after.self_assessment_paid == 108050
    assert after.payable_or_refund == before - 108050


def test_every_challan_is_listed_in_the_autofill_summary(partner,
                                                         taxpayer_id):
    """Once a challan has been saved it is no longer 'new', but it must
    still appear in the classified summary — otherwise a credit imported on
    the first auto-fill silently vanishes from the report on the second."""
    scope = mint_scope(partner, taxpayer_id)
    importers_repo.save_ais_document(scope, "2026-27", _ais_doc(
        _row("2025-26", "Advance Tax", "50,000", "0000002", "12/06/2025",
             "22222")), "test")
    base = autofill.assemble(scope, "2026-27", base={}).inputs
    again = autofill.assemble(scope, "2026-27", base=base)
    paid = [s for s in again.sources if s.head == "Taxes paid"]
    assert len(paid) == 1
    assert paid[0].amount_inr == 50000
    assert "Advance tax" in paid[0].detail


def test_document_stored_before_b3_support_is_flagged(partner, taxpayer_id):
    """An AIS saved by the older parser has no tax_payments KEY at all and
    cannot self-heal — the challans exist only in the raw AIS file."""
    scope = mint_scope(partner, taxpayer_id)
    stale = _ais_doc()
    stale.pop("tax_payments")
    importers_repo.save_ais_document(scope, "2026-27", stale, "test")
    res = _assemble(partner, taxpayer_id)
    assert any("imported before Part B3" in f for f in res.follow_ups)
