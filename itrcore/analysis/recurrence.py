"""Recurring-credit series detection (salary / rent / other periodic)."""

from dataclasses import dataclass, field
from datetime import date
from statistics import median

from rapidfuzz import fuzz


@dataclass
class Series:
    key: str
    display: str
    txn_ids: list[int]
    dates: list[date]
    amounts: list[int]                       # paise
    monthly: bool = False
    kind: str = "periodic"          # salary | rent_candidate | periodic
    missing_months: list[str] = field(default_factory=list)
    ended_early: bool = False
    median_amount: int = 0


def _merge_keys(groups: dict) -> dict:
    """Fuzzy-merge counterparty groups (ACME CORP ≈ ACME CORP PVT LTD)."""
    merged: dict[str, list] = {}
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        target = None
        for existing in merged:
            if fuzz.token_sort_ratio(key, existing) >= 90:
                target = existing
                break
        if target is None:
            merged[key] = list(groups[key])
        else:
            merged[target].extend(groups[key])
    return merged


def find_series(txns: list[dict], thresholds: dict,
                employer_names: list[str],
                fy_end: date) -> list[Series]:
    """txns: dicts with id, txn_date, amount_paise, counterparty_key,
    counterparty_display, narration — credits only are considered."""
    rec = thresholds["recurrence"]
    sal = thresholds["salary"]

    groups: dict[str, list[dict]] = {}
    for t in txns:
        if t["amount_paise"] <= 0:
            continue
        groups.setdefault(t["counterparty_key"], []).append(t)
    groups = _merge_keys(groups)

    out: list[Series] = []
    for key, members in groups.items():
        if len(members) < rec["min_occurrences"]:
            continue
        members.sort(key=lambda t: t["txn_date"])
        med = int(median(t["amount_paise"] for t in members))
        tol = med * rec["amount_tolerance_pct"] // 100
        cluster = [t for t in members if abs(t["amount_paise"] - med) <= tol]
        if len(cluster) < rec["min_occurrences"]:
            continue
        dates = [t["txn_date"] for t in cluster]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        lo, hi = rec["monthly_gap_days"]
        in_range = [g for g in gaps if lo <= g <= hi]
        monthly = (bool(gaps) and median(gaps) >= lo
                   and median(gaps) <= hi
                   and len(in_range) / len(gaps)
                   >= rec["min_fraction_in_gap"])

        series = Series(
            key=key, display=cluster[0]["counterparty_display"],
            txn_ids=[t["id"] for t in cluster], dates=dates,
            amounts=[t["amount_paise"] for t in cluster],
            monthly=monthly, median_amount=med)

        if monthly:
            narr_all = " ".join(t["narration"] for t in cluster)
            has_sal_kw = any(kw in narr_all for kw in sal["keywords"])
            employer_match = any(
                fuzz.token_set_ratio(key.upper(), emp.upper()) >= 80
                for emp in employer_names)
            if ((has_sal_kw or employer_match)
                    and med >= sal["min_amount_inr"] * 100):
                series.kind = "salary"
            else:
                series.kind = "rent_candidate"

            covered = {f"{d.year}-{d.month:02d}" for d in dates}
            months = []
            y, m = dates[0].year, dates[0].month
            while (y, m) <= (dates[-1].year, dates[-1].month):
                months.append(f"{y}-{m:02d}")
                m += 1
                if m > 12:
                    m, y = 1, y + 1
            series.missing_months = [mo for mo in months
                                     if mo not in covered]
            last = dates[-1]
            months_before_fy_end = ((fy_end.year - last.year) * 12
                                    + fy_end.month - last.month)
            series.ended_early = months_before_fy_end >= 2
        out.append(series)
    return out
