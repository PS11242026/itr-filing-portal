"""Interest u/s 234A/234B/234C and late fee u/s 234F.

Rule 119A: interest principal is rounded down to a multiple of ₹100; any
part of a month counts as a full month.
"""

from datetime import date

from dateutil.relativedelta import relativedelta

from itrcore.tax.rounding import pct, r100_floor
from itrcore.tax.types import Challan


def _months_part_as_full(start: date, end: date) -> int:
    """Months from the day AFTER `start` to `end`, part month = full."""
    if end <= start:
        return 0
    months = 0
    cursor = start
    while cursor < end:
        cursor = cursor + relativedelta(months=1)
        months += 1
    return months


def s234a(balance_unpaid: int, filing_date: date | None, due_date: date,
          rate_pm: float) -> int:
    if filing_date is None or filing_date <= due_date or balance_unpaid <= 0:
        return 0
    months = _months_part_as_full(due_date, filing_date)
    return pct(r100_floor(balance_unpaid) * months, rate_pm)


def s234b(assessed_tax: int, paid_by_fy_end: int, filing_date: date | None,
          ay_start: date, rate_pm: float, threshold: int) -> int:
    """assessed_tax = liability minus TDS. Applies when advance tax paid is
    below 90% of assessed tax (and assessed ≥ threshold)."""
    if assessed_tax < threshold:
        return 0
    if paid_by_fy_end * 10 >= assessed_tax * 9:      # ≥ 90%
        return 0
    end = filing_date or ay_start
    if end < ay_start:
        end = ay_start
    months = ((end.year - ay_start.year) * 12
              + (end.month - ay_start.month) + 1)
    shortfall = assessed_tax - paid_by_fy_end
    return pct(r100_floor(shortfall) * months, rate_pm)


def s234c(assessed_tax: int, challans: tuple[Challan, ...],
          schedule: list[dict], rate_pm: float, threshold: int) -> int:
    if assessed_tax < threshold:
        return 0
    total = 0
    for idx, inst in enumerate(schedule):
        by = date.fromisoformat(inst["by"])
        due_cum = pct(assessed_tax, inst["pct"])
        paid_cum = sum(c.amount for c in challans
                       if c.kind == "advance" and c.paid_on
                       and c.paid_on <= by)
        grace_cum = pct(assessed_tax, inst.get("grace_pct", inst["pct"]))
        if paid_cum >= grace_cum:
            continue
        shortfall = due_cum - paid_cum
        if shortfall <= 0:
            continue
        months = 1 if idx == len(schedule) - 1 else 3
        total += pct(r100_floor(shortfall) * months, rate_pm)
    return total


def s234f(total_income: int, filing_date: date | None, due_date: date,
          cfg: dict) -> int:
    if filing_date is None or filing_date <= due_date:
        return 0
    if total_income <= cfg["small_ti_limit"]:
        return cfg["small_amount"]
    return cfg["amount"]
