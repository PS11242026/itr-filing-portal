"""AIS v15 decrypt + classify: envelope round-trip, part bifurcation, and the
de-duplication that stops salary (reported in B1 and B7) double counting."""

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from itrcore.ingest import ais, ais_crypto

PAN = "AAAPL1234C"
DOB = "01/01/1980"

_L2_LABELS = ["Information Category", "Information Code",
              "Information Description", "Information Source", "Count",
              "Amount", "Information Category Code", "Derived Amount",
              "Qualifies For"]


def _l2(cat, code, desc, src, count, amount, ccode):
    return {"columnLabel": _L2_LABELS,
            "columnData": [[cat, code, desc, src, count, amount, ccode,
                            None, ""]]}


def _block(cat, code, desc, src, count, amount, ccode, detail_labels,
           detail_rows):
    return {"title": cat, "l1Src": "X", "infoSrcId": src,
            "l2": _l2(cat, code, desc, src, count, amount, ccode),
            "l1": {"columnLabel": [{"name": n} for n in detail_labels],
                   "columnData": detail_rows}}


def _sample_tree():
    return {
        "metadata": {"loggedInPan": PAN, "jsonVersion": "15.0.0"},
        "partA": {
            "columnLabel": ["Permanent Account Number (PAN)",
                            "Name of Assessee", "Date of Birth"],
            "columnData": [PAN, "TEST TAXPAYER", DOB]},
        "partB": {"sections": [
            {"title": "Part B1-Information relating to tax deducted or "
                      "collected at source",
             "heading": "TDS/TCS Information", "elements": [
                 _block("Salary", "TDS-192", "Salary received (Section 192)",
                        "EMP LTD (BLRC00001A)", "2", "10,00,000.00", "SAL",
                        ["Quarter", "Amount Paid/Credited"],
                        [["Q1", "5,00,000.00"], ["Q2", "5,00,000.00"]]),
                 _block("Interest from deposit", "TDS-194A",
                        "Interest (Section 194A)", "SBI (BLRC00002B)", "1",
                        "50,000.00", "IND", ["Quarter", "Amount"],
                        [["Q1", "50,000.00"]])]},
            {"title": "Part B2-Information relating to specified financial "
                      "transaction (SFT)",
             "heading": "SFT Information", "elements": [
                 _block("Interest from deposit", "SFT-016(TD)",
                        "Interest income (SFT-016)", "HDFC (AAA.1)", "1",
                        "20,000.00", "IND", ["Reported On", "Interest amount"],
                        [["01/05/2026", "20,000.00"]])]},
            {"title": "Part B7-Any other information",
             "heading": "Other Information", "elements": [
                 _block("Salary", "TDS-Ann.II-SAL", "Salary (TDS Annexure II)",
                        "EMP LTD (BLRC00001A)", "1", "10,00,000.00", "SAL",
                        ["Gross Salary"], [["10,00,000.00"]])]},
        ]}}


def _encrypt(plaintext: bytes, pan: str, dob: str) -> bytes:
    """Reproduce the ITD envelope so the test needs no real AIS file."""
    iv = bytes(range(16))
    salt = bytes(range(16, 32))
    key = hashlib.pbkdf2_hmac(
        "sha256", ais_crypto.build_password(pan, dob).encode(), salt, 1000, 32)
    pad = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return (iv.hex() + salt.hex()).encode() + base64.b64encode(ct)


@pytest.fixture
def encrypted_ais():
    return _encrypt(json.dumps(_sample_tree()).encode(), PAN, DOB)


def test_looks_encrypted_vs_plain(encrypted_ais):
    assert ais_crypto.looks_encrypted(encrypted_ais)
    assert not ais_crypto.looks_encrypted(b'{"partB": {}}')


def test_normalize_dob_forms():
    assert ais_crypto.normalize_dob("01/01/1980") == "01011980"
    assert ais_crypto.normalize_dob("1980-01-01") == "01011980"
    assert ais_crypto.normalize_dob("01011980") == "01011980"


def test_iso_dob_for_record_storage():
    assert ais_crypto.iso_dob("19/08/1958") == "1958-08-19"
    assert ais_crypto.iso_dob("1976/09/09") == "1976-09-09"
    assert ais_crypto.iso_dob("15071965") == "1965-07-15"
    assert ais_crypto.iso_dob("") is None
    assert ais_crypto.iso_dob(None) is None
    assert ais_crypto.iso_dob("not a date") is None


def test_decrypt_round_trip(encrypted_ais):
    plain = ais_crypto.decrypt(encrypted_ais, pan=PAN, dob=DOB)
    assert json.loads(plain)["metadata"]["jsonVersion"] == "15.0.0"


def test_decrypt_wrong_dob_fails(encrypted_ais):
    with pytest.raises(ais_crypto.AisDecryptError):
        ais_crypto.decrypt(encrypted_ais, pan=PAN, dob="02/02/1990")


def test_parse_document_bifurcates_parts(encrypted_ais):
    doc = ais.parse_ais_document(encrypted_ais, pan=PAN, dob=DOB)
    assert doc.name == "TEST TAXPAYER"
    assert {r.part for r in doc.rows} == {"B1", "B2", "B7"}
    b1 = doc.rows_for_part("B1")
    salary = next(r for r in b1 if r.category == "Salary")
    assert salary.info_code == "TDS-192"
    assert salary.amount_paise == 100000000
    assert salary.detail.labels == ("Quarter", "Amount Paid/Credited")
    assert len(salary.detail.rows) == 2


@pytest.fixture
def pairwise_dedup(monkeypatch):
    """Flip off the SFT-only rule for the tests that pin the older
    pairwise behaviour."""
    monkeypatch.setattr(ais, "_sft_only", lambda: False)


def test_dedup_salary_not_double_counted(encrypted_ais):
    doc = ais.parse_ais_document(encrypted_ais, pan=PAN, dob=DOB)
    totals = doc.category_totals()          # paise
    # Salary is in B1 (TDS-192) AND B7 (Annexure) for the SAME employer → once.
    assert totals["salary"] == 100000000


def test_sft_feed_alone_decides_an_sft_backed_bucket(encrypted_ais):
    # Deposit interest is reported by SBI in B1 (TDS-194A, 50k) and by HDFC
    # in B2 (SFT-016, 20k). Payer names and figures both differ, so pairwise
    # matching would sum them — and that is exactly how a TDS re-report
    # inflates the FD interest. Once the bucket is SFT-backed, only the SFT
    # feed counts.
    doc = ais.parse_ais_document(encrypted_ais, pan=PAN, dob=DOB)
    assert doc.category_totals()["interest_deposits"] == 2000000
    dropped = {(r.source, r.amount_paise) for r in doc.dropped_tds_rows()}
    assert ("SBI (BLRC00002B)", 5000000) in dropped   # visible, not deleted


def test_pairwise_dedup_when_sft_only_disabled(encrypted_ais, pairwise_dedup):
    doc = ais.parse_ais_document(encrypted_ais, pan=PAN, dob=DOB)
    # Different payers, different figures → both survive under the old rule.
    assert doc.category_totals()["interest_deposits"] == 7000000


def test_tis_summary_processed_values(encrypted_ais):
    doc = ais.parse_ais_document(encrypted_ais, pan=PAN, dob=DOB)
    tis = {r["category"]: r for r in doc.tis_summary()}
    assert tis["Salary"]["processed_paise"] == 100000000
    assert tis["Salary"]["derived_paise"] == 100000000
    assert tis["Interest from deposit"]["processed_paise"] == 2000000


def test_flat_entries_feed_importer(encrypted_ais):
    entries = ais.parse_ais_json(encrypted_ais, pan=PAN, dob=DOB)
    by = {e["category"]: e["amount_paise"] for e in entries}
    assert by["salary"] == 100000000
    assert by["interest_deposits"] == 2000000


def test_tis_entries_are_grouped_by_bucket_not_category_text():
    """Post-office savings interest is filed by the ITD under plain
    "Interest from savings bank". Re-classifying that TEXT would fold it
    back into bank interest — and because auto-fill prefers the TIS figure
    over the AIS one, the same money would then land in BOTH the savings
    and the post-office field."""
    tree = {"metadata": {"jsonVersion": "15.0.0"},
            "partA": {"columnLabel": [], "columnData": []},
            "partB": {"sections": [
                {"title": "Part B2-SFT", "heading": "SFT Information",
                 "elements": [
                     _block("Interest from savings bank", "SFT-016(SB)",
                            "Interest", "STATE BANK OF INDIA (AAACS.AB)", "1",
                            "24,286.00", "INS", [], []),
                     _block("Interest from savings bank", "SFT-016(SB)",
                            "Interest", "CPRC CHENNAI (CHEC18029E.AP563)", "1",
                            "13,633.00", "INS", [], [])]}]}}
    doc = ais._parse_v15(tree)
    by = {e["category"]: e["processed_value_paise"]
          for e in ais.tis_entries(doc)}
    assert by == {"interest_savings": 2428600,
                  "interest_post_office": 1363300}


def test_post_office_savings_interest_gets_its_own_bucket():
    # The AIS files it under the same category as any bank's savings
    # interest; only the payer says it is the post office, and only post
    # office savings interest gets the 10(15)(i) exemption.
    assert ais._bucket("Interest from savings bank", "INS",
                       "CPRC CHENNAI (CHEC00001A)") == "interest_post_office"
    assert ais._bucket("Interest from savings bank", "INS",
                       "HDFC BANK LIMITED") == "interest_savings"


def test_credit_card_payment_is_expenditure_not_income():
    # SFT-006: a ₹37 lakh credit-card bill the assessee PAID. The AIS calls
    # several such rows "Miscellaneous payment"; none of them is income.
    assert ais.classify("Payment to credit card") == "expenditure"
    assert ais.classify("Purchase of bank drafts or pay orders in cash") \
        == "expenditure"
    assert ais.classify("Winnings from lottery") == "other_income"


def test_encrypted_without_credentials_errors(encrypted_ais):
    with pytest.raises(ValueError, match="encrypted"):
        ais.parse_ais_json(encrypted_ais)


def _dividend_tree(with_sft: bool):
    """Real dividend shape: the SAME dividends under Part B1 (TDS-194)
    and Part B2 (SFT-015), with payer names that differ between the two
    feeds — plus SFT-only payers that deducted no TDS."""
    b1 = [_block("Dividend", "TDS-194", "Dividend (Section 194)", src, "1",
                 amt, "DIV", ["Quarter", "Amount"], [["Q1", amt]])
          for src, amt in (
              ("ITC LIMITED (CALI01639B)", "20,055.00"),
              ("HDFC ASSET MANAGEMENT COMPANY LIMITED (MUMH04900A)",
               "10,350.00"),
              ("NIPPON LIFE INDIA ASSET MANAGEMENT LIMITED (MUMR07433G)",
               "3,800.00"),
              ("TATA CONSULTANCY SERVICES LIMITED (MUMT24925F)",
               "1,924.00"))]
    b2 = [_block("Dividend", "SFT-015", "Dividend income (SFT-015)", src,
                 "1", amt, "DIV", ["Reported On", "Amount"],
                 [["01/05/2026", amt]])
          for src, amt in (
              ("I T C LIMITED (AAACI5950L.AZ257)", "20,055.00"),
              ("HDFC ASSET MANAGEMENT COMPANY LIMITED (AAACH7772J)",
               "10,350.00"),
              ("RELIANCE NIPPON LIFE ASSET MANAGMENT LIMITED (AAACR4849R)",
               "3,800.00"),
              ("TATA CONSULTANCY SERVICES LIMITED (AAACR4849R)",
               "1,924.00"),
              ("CENTRAL DEPOSITORY SERVICES (INDIA) LIMITED (AAACC0000C)",
               "625.00"),
              ("GREAVES COTTON LIMITED (AAACG2062M.AZ165)", "100.00"))]
    sections = [
        {"title": "Part B1-Information relating to tax deducted or "
                  "collected at source",
         "heading": "TDS/TCS Information", "elements": b1}]
    if with_sft:
        sections.append(
            {"title": "Part B2-Information relating to specified financial "
                      "transaction (SFT)",
             "heading": "SFT Information", "elements": b2})
    return {"metadata": {"loggedInPan": PAN, "jsonVersion": "15.0.0"},
            "partA": {"columnLabel": ["Permanent Account Number (PAN)",
                                      "Name of Assessee", "Date of Birth"],
                      "columnData": [PAN, "DIVIDEND SHAPE", DOB]},
            "partB": {"sections": sections}}


def test_dividend_counts_sft_only_not_tds_blocks():
    doc = ais.parse_ais_document(
        json.dumps(_dividend_tree(with_sft=True)).encode())
    # B1 names differ from B2 ("ITC" vs "I T C", "NIPPON…" vs "RELIANCE
    # NIPPON…") so the payer-wise dedup alone would double count to 60,709.
    # Only the SFT side may count: 36,854 — the Genius/TIS figure.
    assert doc.category_totals()["dividend"] == 36854_00
    tis = {r["category"]: r for r in doc.tis_summary()}
    assert tis["Dividend"]["processed_paise"] == 36854_00
    assert tis["Dividend"]["derived_paise"] == 36854_00
    # The B1 blocks stay visible in the document itself.
    assert sum(1 for r in doc.rows_for_part("B1")
               if r.bucket == "dividend") == 4


def test_dividend_falls_back_to_tds_blocks_without_sft():
    doc = ais.parse_ais_document(
        json.dumps(_dividend_tree(with_sft=False)).encode())
    # No SFT dividend in the AIS → the TDS-194 blocks still fill the head.
    assert doc.category_totals()["dividend"] == 36129_00


def _interest_tree(with_sft: bool):
    """Real interest shape: the same FD interest under B1 (TDS-194A) and B2
    (SFT-016(TD)) with DIFFERENT payer names for the same payer —
    'CENTRALIZED PROCESSING AND RECONCILIATION CENTRE' vs 'CPRC CHENNAI'
    — plus an SFT-only bank that deducted no TDS."""
    b1 = [_block("Interest from deposit", "TDS-194A", "Interest (194A)",
                 src, "1", amt, "IND", ["Quarter", "Amount"],
                 [["Q1", amt]])
          for src, amt in (
              ("HDFC BANK LIMITED (MUMH03189E)", "2,84,863.00"),
              ("CENTRALIZED PROCESSING AND RECONCILIATION CENTRE "
               "(CHEC18029E)", "1,23,000.00"))]
    b2 = [_block("Interest from deposit", "SFT-016(TD)",
                 "Interest income (SFT-016)", src, "1", amt, "IND",
                 ["Reported On", "Amount"], [["01/05/2026", amt]])
          for src, amt in (
              ("HDFC BANK LIMITED (AAACH2702H.AB772)", "2,84,863.00"),
              ("CPRC CHENNAI (CHEC18029E.AP563)", "1,23,000.00"),
              ("STATE BANK OF INDIA (AAACS8577K.AB7)", "45,046.00"))]
    sections = [
        {"title": "Part B1-Information relating to tax deducted or "
                  "collected at source",
         "heading": "TDS/TCS Information", "elements": b1}]
    if with_sft:
        sections.append(
            {"title": "Part B2-Information relating to specified financial "
                      "transaction (SFT)",
             "heading": "SFT Information", "elements": b2})
    return {"metadata": {"loggedInPan": PAN, "jsonVersion": "15.0.0"},
            "partA": {"columnLabel": ["Permanent Account Number (PAN)",
                                      "Name of Assessee", "Date of Birth"],
                      "columnData": [PAN, "INTEREST SHAPE", DOB]},
            "partB": {"sections": sections}}


def test_interest_counts_sft_only_when_sft_present():
    doc = ais.parse_ais_document(
        json.dumps(_interest_tree(with_sft=True)).encode())
    # Name-dedup alone would double count CPRC (different names across
    # feeds) to 5,75,909 — the SFT side alone is the Genius/TIS figure.
    assert doc.category_totals()["interest_deposits"] == 452909_00
    tis = {r["category"]: r for r in doc.tis_summary()}
    assert tis["Interest from deposit"]["processed_paise"] == 452909_00


def test_interest_falls_back_to_tds_blocks_without_sft():
    # Company-deposit interest reported ONLY via TDS-194A must still count.
    doc = ais.parse_ais_document(
        json.dumps(_interest_tree(with_sft=False)).encode())
    assert doc.category_totals()["interest_deposits"] == 407863_00


def test_document_from_dict_recomputes_stale_buckets():
    # Documents saved under an older taxonomy self-heal on load: bucket is
    # derived from category/code, never trusted from the stored JSON.
    doc = ais.document_from_dict({"rows": [
        {"part": "B7", "part_title": "t", "category": "Family pension",
         "category_code": "", "info_code": "OTH", "description": "fp",
         "source": "S", "count": 1, "amount_paise": 100,
         "derived_paise": None, "bucket": "salary",
         "detail_labels": [], "detail_rows": []},
        {"part": "B1", "part_title": "t", "category": "Dividend",
         "category_code": "DIV", "info_code": "TDS-194",
         "description": "d", "source": "S", "count": 1,
         "amount_paise": 200, "derived_paise": None, "bucket": "other",
         "detail_labels": [], "detail_rows": []}]})
    assert doc.rows[0].bucket == "family_pension"
    assert doc.rows[1].bucket == "dividend"


def test_document_serialization_round_trip(encrypted_ais):
    doc = ais.parse_ais_document(encrypted_ais, pan=PAN, dob=DOB)
    back = ais.document_from_dict(ais.document_to_dict(doc))
    assert back.category_totals() == doc.category_totals()
    assert len(back.rows) == len(doc.rows)
    assert back.rows[0].detail.rows == doc.rows[0].detail.rows


def test_excel_export_has_genius_sheets(encrypted_ais):
    import io

    import openpyxl

    from itrcore.export import ais_excel
    doc = ais.parse_ais_document(encrypted_ais, pan=PAN, dob=DOB)
    wb = openpyxl.load_workbook(io.BytesIO(ais_excel.build(doc, ay="2026-27")))
    assert "Taxpayer Information (TIS)" in wb.sheetnames
    assert "AIS-TDS-TCS" in wb.sheetnames
    assert "AIS-SFT" in wb.sheetnames
    # TIS summary carries the de-duplicated salary (whole rupees)
    tis_ws = wb["Taxpayer Information (TIS)"]
    salary = [row for row in tis_ws.iter_rows(values_only=True)
              if row[1] == "Salary"]
    assert salary and salary[0][2] == 1000000
