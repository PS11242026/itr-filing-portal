"""Ingestion: amounts, probing, bank CSV parsing, dedup, feed parsers."""

import json
from datetime import date
from decimal import Decimal

import pytest

from itrcore.ingest import ais, form26as, prefill, probe
from itrcore.ingest.banks import best_parse
from itrcore.ingest.dedup import (coverage_gaps, fingerprint,
                                  with_fingerprints)
from itrcore.ingest.normalize import parse_amount, parse_date
from itrcore.ingest.base import RawTxn


# ------------------------------------------------------------- normalize --

@pytest.mark.parametrize("raw,expected", [
    ("1,23,456.78", Decimal("123456.78")),        # Indian grouping
    ("1,234,567.89", Decimal("1234567.89")),      # western grouping
    ("(2,500.00)", Decimal("-2500.00")),          # brackets
    ("1234.56-", Decimal("-1234.56")),            # trailing sign
    ("₹ 5,000", Decimal("5000")),
    ("Rs. 750.25", Decimal("750.25")),
    ("2500 Dr", Decimal("-2500")),
    ("2500 Cr", Decimal("2500")),
    ("-", None),
    ("", None),
    ("SALARY", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_date_formats_and_fallback():
    assert parse_date("15/07/2025", ("%d/%m/%Y",)) == date(2025, 7, 15)
    assert parse_date("15 Jul 2025") == date(2025, 7, 15)
    assert parse_date("02-01-2026") == date(2026, 1, 2)   # dayfirst
    assert parse_date("TOTAL") is None


def test_probe_kinds():
    assert probe.make_probe("a.pdf", b"%PDF-1.7 x").kind == "pdf"
    assert probe.make_probe("a.xlsx", b"PK\x03\x04").kind == "xlsx"
    assert probe.make_probe("a.xls", b"<html><table>").kind == "xls_html"
    assert probe.make_probe("a.csv", b"Date,Narration\n1,2").kind == "csv"


# ------------------------------------------------------------- bank CSVs --

HDFC_CSV = (
    "HDFC BANK Ltd.\n"
    "Statement of account\n"
    "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,"
    "Deposit Amt.,Closing Balance\n"
    "01/04/25,SALARY-ACME LTD-APR,NEFT001,01/04/25,,\"1,50,000.00\","
    "\"3,10,450.00\"\n"
    "05/04/25,UPI-SWIGGY-swiggy@icici-512345,UPI512,05/04/25,450.00,,"
    "\"3,10,000.00\"\n"
    "10/04/25,ATM WDL MUMBAI,ATM01,10/04/25,\"10,000.00\",,"
    "\"3,00,000.00\"\n"
)

SBI_CSV = (
    "Txn Date,Value Date,Description,Ref No./Cheque No.,Debit,Credit,"
    "Balance\n"
    "03 Apr 2025,03 Apr 2025,TO TRANSFER-UPI/DR/1234/RAMESH,UPI1,"
    "\"2,000.00\",, \"98,000.00\"\n"
    "07 Apr 2025,07 Apr 2025,BY TRANSFER-NEFT RENT FROM TENANT,N1,,"
    "\"25,000.00\",\"1,23,000.00\"\n"
)


def test_hdfc_csv_parses_with_seeded_map():
    p = probe.make_probe("stmt.csv", HDFC_CSV.encode())
    result = best_parse(p)
    assert result.adapter_id == "hdfc_v1"
    assert len(result.txns) == 3
    salary = result.txns[0]
    assert salary.txn_date == date(2025, 4, 1)
    assert salary.amount_paise == 15000000          # +1.5L credit
    assert salary.balance_paise == 31045000
    assert result.txns[1].amount_paise == -45000    # 450 debit
    assert result.confidence == 1.0


def test_sbi_csv_parses_with_seeded_map():
    p = probe.make_probe("stmt.csv", SBI_CSV.encode())
    result = best_parse(p)
    assert result.adapter_id == "sbi_v1"
    assert [t.amount_paise for t in result.txns] == [-200000, 2500000]


def test_html_xls_statement():
    html = ("<html><table>"
            "<tr><td>Date</td><td>Narration</td><td>Chq/Ref No</td>"
            "<td>Amount</td><td>Dr / Cr</td><td>Balance</td></tr>"
            "<tr><td>05-04-2025</td><td>UPI-ZOMATO</td><td>1</td>"
            "<td>350.00</td><td>DR</td><td>50,000.00</td></tr>"
            "<tr><td>06-04-2025</td><td>NEFT SALARY</td><td>2</td>"
            "<td>1,00,000.00</td><td>CR</td><td>1,50,000.00</td></tr>"
            "</table></html>")
    p = probe.make_probe("stmt.xls", html.encode())
    assert p.kind == "xls_html"
    result = best_parse(p)
    assert result.adapter_id == "kotak_v1"
    assert [t.amount_paise for t in result.txns] == [-35000, 10000000]


# ----------------------------------------------------------------- dedup --

def test_fingerprints_stable_and_sequenced():
    txn = RawTxn(txn_date=date(2025, 5, 1), narration="UPI-RAMESH",
                 amount_paise=-50000)
    twin = RawTxn(txn_date=date(2025, 5, 1), narration="UPI-RAMESH",
                  amount_paise=-50000)
    pairs = with_fingerprints(7, [txn, twin])
    assert pairs[0][1] != pairs[1][1]              # same-day twins split
    again = with_fingerprints(7, [txn, twin])
    assert [fp for _, fp in again] == [fp for _, fp in pairs]  # stable

    with_balance = RawTxn(txn_date=date(2025, 5, 1),
                          narration="UPI-RAMESH", amount_paise=-50000,
                          balance_paise=100000)
    assert fingerprint(7, with_balance) == fingerprint(7, with_balance)


def test_coverage_gaps_detects_internal_holes():
    covered = {"2025-04", "2025-05", "2025-08"}
    gaps = coverage_gaps(covered, date(2025, 4, 1), date(2026, 3, 31))
    assert gaps == ["2025-06", "2025-07"]
    assert coverage_gaps(set(), date(2025, 4, 1), date(2026, 3, 31)) == []


# ----------------------------------------------------------- feed parsers --

def test_ais_json_parser():
    tree = {"aisData": [
        {"infoDesc": "Interest from savings bank",
         "processedValue": "12500.50"},
        {"infoDesc": "Dividend received", "amount": 8000},
        {"nested": {"infoDesc": "Sale of immovable property",
                    "reportedValue": "6000000"}},
    ]}
    entries = ais.parse_ais_json(json.dumps(tree).encode())
    by_cat = {e["category"]: e["amount_paise"] for e in entries}
    assert by_cat["interest_savings"] == 1250050
    assert by_cat["dividend"] == 800000
    assert by_cat["sale_immovable"] == 600000000


def test_ais_rejects_garbage():
    with pytest.raises(ValueError):
        ais.parse_ais_json(b"not json")
    with pytest.raises(ValueError):
        ais.parse_ais_json(b"{}")


def test_26as_text_parser():
    text = ("Form 26AS - Annual Tax Statement\n"
            "1^ACME LTD^MUMA12345B^192^1500000.00^150000.00^150000.00\n"
            "2^HDFC BANK^MUMH98765C^194A^40000.00^4000.00^4000.00\n"
            "gibberish line without separators\n")
    parsed = form26as.parse_26as_text(text.encode())
    assert len(parsed["entries"]) == 2
    first = parsed["entries"][0]
    assert first["tan"] == "MUMA12345B"
    assert first["tds_paise"] == 15000000
    assert first["deductor_name"] == "ACME LTD"


def test_prefill_json_parser():
    tree = {"personalInfo": {"pan": "AAAPL1234C", "dob": "1985-06-15"},
            "tdsOnSalaries": [{
                "TAN": "MUMA12345B",
                "EmployerOrDeductorOrCollecterName": "ACME LTD",
                "TotalTDSSal": 150000}],
            "bank": {"IFSCCode": "HDFC0001234", "BankName": "HDFC",
                     "BankAccountNo": "50100123456789"}}
    info = prefill.parse_prefill_json(json.dumps(tree).encode())
    assert info["pan"] == "AAAPL1234C"
    assert info["tds"][0]["amount"] == 150000
    assert info["bank_accounts"][0]["ifsc"] == "HDFC0001234"
