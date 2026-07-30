"""Transaction fingerprints and statement-coverage tracking.

Fingerprint = sha256(account | date | amount | narration[:40] | balance or
same-day sequence). The balance disambiguates two identical same-day UPI
payments; when balance is absent a stable per-batch sequence is used and
ambiguous repeats go to a review list rather than silently deduping.
"""

import hashlib
from collections import Counter
from datetime import date

from itrcore.ingest.base import RawTxn


def fingerprint(account_id: int, txn: RawTxn, seq: int = 0) -> str:
    tail = str(txn.balance_paise) if txn.balance_paise is not None \
        else f"seq{seq}"
    payload = (f"{account_id}|{txn.txn_date.isoformat()}|{txn.amount_paise}"
               f"|{txn.narration[:40]}|{tail}")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def with_fingerprints(account_id: int,
                      txns: list[RawTxn]) -> list[tuple[RawTxn, str]]:
    seen: Counter = Counter()
    out = []
    for txn in txns:
        key = (txn.txn_date, txn.amount_paise, txn.narration[:40])
        seq = seen[key]
        seen[key] += 1
        out.append((txn, fingerprint(account_id, txn, seq)))
    return out


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def fy_months(fy_start: date, fy_end: date) -> list[str]:
    months = []
    y, m = fy_start.year, fy_start.month
    while (y, m) <= (fy_end.year, fy_end.month):
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def coverage_gaps(covered: set[str], fy_start: date,
                  fy_end: date) -> list[str]:
    """Missing months strictly inside the covered span (open ends are
    reported separately as informational)."""
    if not covered:
        return []
    all_months = fy_months(fy_start, fy_end)
    in_span = [m for m in all_months
               if min(covered) <= m <= max(covered)]
    return [m for m in in_span if m not in covered]
