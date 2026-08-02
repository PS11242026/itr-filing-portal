"""Head: Profits and gains of business or profession (Section 28).

Increment 1 — the presumptive schemes: 44AD (eligible business), 44ADA
(profession) and 44AE (goods carriage). Presumptive income is deemed net of
all expenses and depreciation; the assessee may instead declare a higher
figure. Regular books (P&L → net profit, depreciation u/s 32, disallowances
u/s 40/40A/43B) arrive in a later increment.

Whole rupees only; every rate and turnover limit comes from the AY config.
"""

from itrcore.tax.rounding import pct
from itrcore.tax.types import (HeadResult, Line, PresumptiveBusiness,
                               RegularBusiness)

_SECTION_LABEL = {
    "44AD": "business u/s 44AD",
    "44ADA": "profession u/s 44ADA",
    "44AE": "goods carriage u/s 44AE",
}


def _s44ad(b: PresumptiveBusiness, cfg: dict) -> tuple[int, bool]:
    turnover = b.turnover_digital + b.turnover_cash
    presumptive = (pct(b.turnover_digital, cfg["rate_digital"])
                   + pct(b.turnover_cash, cfg["rate_cash"]))
    income = max(presumptive, b.declared_income)
    # Higher turnover limit applies only if cash receipts ≤ 5% of turnover
    # (cash * 20 ≤ turnover) — kept in integer arithmetic.
    low_cash = b.turnover_cash * 20 <= turnover
    limit = (cfg["turnover_limit_low_cash"] if low_cash
             else cfg["turnover_limit"])
    return income, turnover > limit


def _s44ada(b: PresumptiveBusiness, cfg: dict) -> tuple[int, bool]:
    income = max(pct(b.gross_receipts, cfg["rate"]), b.declared_income)
    return income, b.gross_receipts > cfg["receipts_limit_low_cash"]


def _s44ae(b: PresumptiveBusiness, cfg: dict) -> tuple[int, bool]:
    income = (b.heavy_ton_months * cfg["heavy_per_ton_month"]
              + b.other_vehicle_months * cfg["other_per_vehicle_month"])
    return max(income, b.declared_income), False


def _regular(rb: RegularBusiness, lines: list[Line], *,
             speculative: bool = False) -> int:
    name = rb.name or ("Speculation business" if speculative
                       else "Business (books)")
    if speculative and rb.turnover:
        lines.append(Line(f"{name} — turnover from speculative activity",
                          "43(5)", rb.turnover))
    lines.append(Line(f"{name} — net profit per P&L",
                      "43(5)" if speculative else "28",
                      rb.net_profit_before_tax))
    income = rb.net_profit_before_tax
    for amount, label, section, sign in (
            (rb.depreciation_books, "Add: depreciation charged in books",
             "32", 1),
            (rb.depreciation_it, "Less: depreciation allowable u/s 32",
             "32", -1),
            (rb.disallowances, "Add: disallowances (40 / 40A(3) / 43B)",
             "40", 1),
            (rb.other_additions, "Add: other adjustments", "28", 1),
            (rb.other_deductions, "Less: other adjustments", "28", -1)):
        if amount:
            lines.append(Line(label, section, sign * amount))
            income += sign * amount
    return income


def compute(presumptive: tuple[PresumptiveBusiness, ...],
            regular: tuple[RegularBusiness, ...],
            cfg: dict) -> tuple[HeadResult, HeadResult]:
    """Returns (ordinary business, speculation business) as separate heads.

    They are one head for presentation but never for set-off: a speculation
    loss is ring-fenced by Sec 73, so the two totals must stay apart until
    :mod:`itrcore.tax.setoff` has had its say."""
    lines: list[Line] = []
    spec_lines: list[Line] = []
    total = 0
    spec_total = 0
    for b in presumptive:
        if b.section == "44AD":
            income, over = _s44ad(b, cfg["s44ad"])
        elif b.section == "44ADA":
            income, over = _s44ada(b, cfg["s44ada"])
        elif b.section == "44AE":
            income, over = _s44ae(b, cfg["s44ae"])
        else:
            continue
        name = b.name or _SECTION_LABEL[b.section].split(" u/s")[0].title()
        lines.append(Line(f"{name} — presumptive {_SECTION_LABEL[b.section]}",
                          b.section, income))
        if over:
            lines.append(Line(
                "⚠ Receipts exceed the presumptive limit — regular books "
                "and audit (Sec 44AA/44AB) required", b.section, 0))
        total += income
    for rb in regular:
        if rb.is_speculative:
            spec_total += _regular(rb, spec_lines, speculative=True)
        else:
            total += _regular(rb, lines)
    if lines:
        lines.append(Line("Income from business or profession", "28", total))
    if spec_lines:
        spec_lines.append(Line("Income from speculation business", "43(5)",
                               spec_total))
    return (HeadResult(head="business", amount=total, lines=tuple(lines)),
            HeadResult(head="speculation", amount=spec_total,
                       lines=tuple(spec_lines)))
