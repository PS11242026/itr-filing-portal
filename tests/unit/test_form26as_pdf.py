"""Form 26AS PDF: flat-entry extraction, serialization, and PDF regeneration.

The parser is calibrated against real TRACES PDFs (which carry PII, so aren't
committed); here we exercise the structural logic and the re-export on a
hand-built document."""

from itrcore.export import form26as_pdf_export
from itrcore.ingest import form26as_pdf as f26


def _doc():
    return f26.Form26AS(
        pan="AAAPL1234C", name="TEST TAXPAYER",
        financial_year="2025-26", assessment_year="2026-27",
        parts=[f26.Part(part_id="I", title="PART-I - Details of TDS",
                        deductors=[
            f26.Deductor(
                name="STATE BANK OF INDIA", tan="MUMS86175F",
                total_paid="214405.00", total_tax_deducted="21443.00",
                total_tds_deposited="21443.00",
                transactions=[
                    f26.Txn(sr="1", section="194A", txn_date="31-Mar-2026",
                            amount_paid="30750.00", tax_deducted="3075.00",
                            tds_deposited="3075.00"),
                    f26.Txn(sr="2", section="194A", txn_date="31-Dec-2025",
                            amount_paid="30750.00", tax_deducted="3075.00",
                            tds_deposited="3075.00")]),
            f26.Deductor(
                name="EMPLOYER LTD", tan="MUMS89582D",
                total_paid="1584000.00", total_tax_deducted="110604.00",
                total_tds_deposited="110604.00",
                transactions=[
                    f26.Txn(sr="1", section="192", txn_date="25-Feb-2026",
                            amount_paid="132750.00", tax_deducted="9562.00",
                            tds_deposited="9562.00")])])])


def test_flat_entries_use_tds_deposited():
    entries, skipped = f26._flat_entries(_doc())
    assert not skipped
    by_tan = {e["tan"]: e for e in entries}
    assert by_tan["MUMS86175F"]["tds_paise"] == 2144300
    assert by_tan["MUMS86175F"]["section"] == "194A"
    assert by_tan["MUMS89582D"]["section"] == "192"
    # claimable TDS is the DEPOSITED total, in paise
    assert by_tan["MUMS89582D"]["tds_paise"] == 11060400


def test_predominant_section():
    d = _doc().parts[0].deductors[0]
    assert f26._predominant_section(d) == "194A"


def test_paise_parsing():
    assert f26._paise("30,750.00") == 3075000
    assert f26._paise("-") is None
    assert f26._paise("") is None


def test_document_serialization_round_trip():
    doc = _doc()
    back = f26.document_from_dict(f26.document_to_dict(doc))
    assert back.pan == doc.pan
    assert back.parts[0].deductors[0].tan == "MUMS86175F"
    assert len(back.parts[0].deductors[0].transactions) == 2
    assert back.parts[0].deductors[1].transactions[0].section == "192"


def test_pdf_export_is_valid_pdf():
    pdf = form26as_pdf_export.build(_doc(), ay="2026-27")
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 800
