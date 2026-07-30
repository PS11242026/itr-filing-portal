"""Regular-books workbook (Genius BS & P&L / ITR-3 Part A structure):
parse → account chain → reconciliation gap."""

import io

import openpyxl

from itrcore.ingest import books_excel


def _workbook() -> bytes:
    """A miniature Genius-format book set with the real file's traps:
    numeric statutory item codes on rows, computed Total rows, and
    inter-account transfer rows (all of which must be skipped)."""
    wb = openpyxl.Workbook()

    bs = wb.active
    bs.title = "Balance Sheet"
    bs.append(["A", "SOURCES OF FUNDS"])
    bs.append([None, 1, "Proprietor's capital", None, 500000])
    bs.append([None, 2, "Unsecured loans from banks", None, 200000])
    bs.append([None, 5, "Sources of funds Total (1c+2c)", None, 999999])
    bs.append(["B", "APPLICATION OF FUNDS"])
    bs.append([None, 1, "Net Block of fixed assets", None, 300000])
    bs.append([None, 3, "Cash-in-hand", None, 400000])

    mfg = wb.create_sheet("Manufacturing Account")
    mfg.append(["I", "Opening Inventory"])
    mfg.append([None, "A", "Opening stock of raw-material", None, 50000])
    mfg.append([None, "B", "Purchases of raw material", None, 150000])
    mfg.append([None, "F", "Total of Debits (Aiii+B)", None, 999999])
    mfg.append([2, "Closing Stock"])
    mfg.append([None, "i", "Raw material", None, 30000])

    trd = wb.create_sheet("Trading Account")
    trd.append(["CREDITS TO TRADING ACCOUNT"])
    trd.append([4, "A", "Sale of goods", None, 900000])
    trd.append([None, "B", "Gross receipts from Profession", None, 100000])
    trd.append([5, None, "Closing Stock of Finished Stocks", None, 40000])
    trd.append([6, None, "Total of credits (4D + 5)", None, 999999])
    trd.append(["DEBITS TO TRADING ACCOUNT"])
    trd.append([7, None, "Opening Stock of Finished Goods", None, 60000])
    trd.append([8, None, "Purchases (net of refunds)", None, 250000])
    trd.append([11, None, "Cost of Goods Produced - transferred from "
                          "Manufacturing Account", None, 999999])

    pnl = wb.create_sheet("Profit and Loss Account")
    pnl.append(["CREDITS TO PROFIT AND LOSS ACCOUNT"])
    pnl.append([13, None, "Gross profit transferred from Trading Account",
                None, 999999])
    pnl.append([14, "iv", "Interest income", None, 20000])
    pnl.append(["DEBITS TO PROFIT AND LOSS ACCOUNT"])
    pnl.append([16, None, "Freight outward", 16, 15000])
    pnl.append([22, "i", "Salaries and wages", None, 120000])
    pnl.append([22, "xi", "Total compensation to employees", None, 999999])
    pnl.append([52, None, "Depreciation and amortisation", None, 35000])
    # unfilled row whose only numerics are statutory codes — must NOT
    # become an entry
    pnl.append([44, None, "Rates and taxes", 44, None])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_buckets_and_skip_rules():
    parsed = books_excel.parse_books_excel(_workbook())
    bs = parsed["balance_sheet"]
    assert {e["amount"] for e in bs["sources"]} == {500000, 200000}
    assert {e["amount"] for e in bs["application"]} == {300000, 400000}
    # totals / transfers / code-only rows never become entries
    all_amounts = [e["amount"] for acct in parsed.values()
                   for side in acct.values() for e in side]
    assert 999999 not in all_amounts
    assert 44 not in all_amounts and 16 not in all_amounts
    assert {e["amount"] for e in parsed["manufacturing"]["credits"]} \
        == {30000}


def test_account_chain_reconciles():
    summary = books_excel.summarise(
        books_excel.parse_books_excel(_workbook()))
    # COGS = 50k + 150k − 30k closing
    assert summary.cost_of_goods_produced == 170000
    # GP = (900k + 100k + 40k closing FG) − (60k + 250k) − COGS
    assert summary.gross_profit == 560000
    # NP = GP + 20k other income − (15k + 120k + 35k)
    assert summary.net_profit == 410000
    # BS: 700k sources vs 700k application → balanced
    assert summary.bs_gap == 0


def test_books_mismatch_flagged_before_compute(partner, taxpayer_id):
    from itrcore.analysis import recon_gaps
    from itrcore.authz import mint_scope

    scope = mint_scope(partner, taxpayer_id)
    parsed = books_excel.parse_books_excel(_workbook())
    inputs = {
        "other_sources": {},
        "regular_businesses": [{"name": "Demo Traders",
                                "net_profit_before_tax": 400000}],
        "books": {"Demo Traders": parsed}}
    gaps = recon_gaps.find_gaps(scope, "2026-27", inputs)
    assert len(gaps) == 1 and gaps[0].head == "Business (books)"
    assert "410,000" in gaps[0].message and "400,000" in gaps[0].message

    inputs["regular_businesses"][0]["net_profit_before_tax"] = 410000
    assert recon_gaps.find_gaps(scope, "2026-27", inputs) == []

    inputs["regular_businesses"] = []
    gaps = recon_gaps.find_gaps(scope, "2026-27", inputs)
    assert gaps and "no regular-books business row" in gaps[0].message


def test_missing_pnl_sheet_rejected():
    import pytest
    wb = openpyxl.Workbook()
    wb.active.title = "Random"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="Profit and Loss"):
        books_excel.parse_books_excel(buf.getvalue())
