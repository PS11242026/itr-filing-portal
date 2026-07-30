"""Regular books of accounts — the Genius "BS and PL" workbook (which
mirrors the ITR-3 Part A Balance Sheet / Manufacturing / Trading / P&L
statutory structure) parsed into heads and subheads, with the account
chain recomputed from the ENTRY rows:

    cost of goods produced = Manufacturing debits − closing WIP/raw
    gross profit           = Trading credits − Trading debits − COGP
    net profit (books)     = gross profit + P&L credits − P&L debits

Total rows and inter-account transfer rows in the workbook are SKIPPED
(their cached formula values may be stale); we re-derive every total so
the computation's business income can reconcile against the books.
"""

import io
from dataclasses import dataclass

_SHEET_KEYS = {
    "balance_sheet": ("balance sheet",),
    "manufacturing": ("manufacturing",),
    "trading": ("trading account",),
    "pnl": ("profit and loss",),
}
# Rows never treated as entries: computed totals and cross-account
# transfers (re-derived), bare section banners, and the P&L's
# below-the-line appropriation block (items 54-60 — provision for tax,
# reserves, balances carried). The reconciliation target is item 53,
# net profit BEFORE taxes, matching regular_businesses'
# net_profit_before_tax.
_SKIP_MARKERS = ("total", "transferred", "credits to", "debits to",
                 "sources of funds", "application of funds",
                 "provision for current tax", "provision for deferred",
                 "profit after tax", "appropriation",
                 "balance brought forward", "transfer to reserves",
                 "balance carried")
# Section banners that flip the side while walking a sheet.
_CREDIT_BANNERS = ("credits to", "closing stock", "sources of funds")
_DEBIT_BANNERS = ("debits to", "application of funds")


@dataclass(frozen=True)
class BooksSummary:
    cost_of_goods_produced: int
    gross_profit: int
    net_profit: int
    bs_sources: int
    bs_application: int

    @property
    def bs_gap(self) -> int:
        return self.bs_sources - self.bs_application


def _row_label(cells: list) -> str:
    parts = [str(c).strip() for c in cells
             if isinstance(c, str) and str(c).strip()]
    return " ".join(parts)


def _row_amount(cells: list):
    """The entered amount = the LAST numeric in the row, ignoring the two
    leading columns (statutory item numbers) and any numeric ≤ 60 (the
    highest statutory item code in the workbook — codes like 16/44/58 are
    sometimes numeric cells). Sub-₹61 book entries are immaterial to the
    net-profit tie-out."""
    nums = [c for c in cells[2:] if isinstance(c, (int, float))
            and not isinstance(c, bool) and abs(c) > 60]
    return int(round(nums[-1])) if nums else None


def parse_books_excel(data: bytes) -> dict:
    """Filled workbook → {account: {"credits": [...], "debits": [...]}}
    with each entry {"label", "amount"} (whole rupees). The Balance Sheet
    lands under "sources"/"application" instead of credits/debits."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True,
                                    read_only=True)
    except Exception as exc:
        raise ValueError(f"could not open the workbook: {exc}") from exc

    sheets: dict[str, str] = {}
    for name in wb.sheetnames:
        low = name.casefold()
        if "other details" in low:
            continue
        for key, needles in _SHEET_KEYS.items():
            if key not in sheets and any(n in low for n in needles):
                sheets[key] = name
    if "pnl" not in sheets:
        raise ValueError(
            "no 'Profit and Loss Account' sheet found — upload the "
            "Genius-format BS & P&L workbook")

    out: dict[str, dict] = {}
    for key, sheet_name in sheets.items():
        ws = wb[sheet_name]
        # Manufacturing starts on the debit side (opening stock, purchases,
        # wages...); Trading and P&L start under a CREDITS banner; the
        # Balance Sheet starts with SOURCES OF FUNDS.
        side = "debits" if key == "manufacturing" else "credits"
        buckets: dict[str, list] = {"credits": [], "debits": []}
        for row in ws.iter_rows(values_only=True):
            cells = list(row)
            label = _row_label(cells)
            if not label:
                continue
            low = label.casefold()
            if any(b in low for b in _CREDIT_BANNERS):
                side = "credits"
            elif any(b in low for b in _DEBIT_BANNERS):
                side = "debits"
            if any(m in low for m in _SKIP_MARKERS):
                continue
            amount = _row_amount(cells)
            if not amount:
                continue
            buckets[side].append({"label": label[:120], "amount": amount})
        if key == "balance_sheet":
            out[key] = {"sources": buckets["credits"],
                        "application": buckets["debits"]}
        else:
            out[key] = buckets
    return out


def _total(entries: list[dict]) -> int:
    return sum(int(e.get("amount", 0) or 0) for e in entries)


def summarise(parsed: dict) -> BooksSummary:
    mfg = parsed.get("manufacturing", {})
    cogs = _total(mfg.get("debits", [])) - _total(mfg.get("credits", []))
    trading = parsed.get("trading", {})
    gross = (_total(trading.get("credits", []))
             - _total(trading.get("debits", [])) - cogs)
    pnl = parsed.get("pnl", {})
    net = gross + _total(pnl.get("credits", [])) \
        - _total(pnl.get("debits", []))
    bs = parsed.get("balance_sheet", {})
    return BooksSummary(
        cost_of_goods_produced=cogs, gross_profit=gross, net_profit=net,
        bs_sources=_total(bs.get("sources", [])),
        bs_application=_total(bs.get("application", [])))
