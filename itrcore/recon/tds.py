"""TDS reconciliation: computation-input claims vs imported 26AS rows.

Grouped by TAN. Statuses:
  matched              — within tolerance
  claim_exceeds_26as   — HARD BLOCK: cannot claim more than 26AS shows
  unclaimed_26as       — 26AS has TDS the return doesn't claim →
                         possible unreported income / missed credit
"""

from dataclasses import dataclass

from itrcore.analysis.classify import load_thresholds
from itrcore.authz import TaxpayerScope
from itrcore.db.repos import importers as importers_repo


@dataclass(frozen=True)
class TDSItem:
    key: str                       # "tds:<TAN>"
    tan: str
    deductor: str
    claimed_inr: int
    as26_inr: int
    status: str

    @property
    def diff_inr(self) -> int:
        return self.claimed_inr - self.as26_inr


def compare(scope: TaxpayerScope, ay: str,
            inputs_dict: dict) -> list[TDSItem]:
    tol = load_thresholds()["recon"]["tds_tolerance_inr"]
    claimed: dict[str, dict] = {}
    for t in inputs_dict.get("tds", []):
        tan = (t.get("tan") or "").strip().upper() or "(no TAN)"
        entry = claimed.setdefault(tan, {"amount": 0,
                                         "name": t.get("deductor", "")})
        entry["amount"] += int(t.get("amount", 0))

    as26: dict[str, dict] = {}
    for r in importers_repo.list_tds(scope, ay, source="26as"):
        tan = (r["tan"] or "").strip().upper() or "(no TAN)"
        entry = as26.setdefault(tan, {"amount": 0,
                                      "name": r["deductor_name"] or ""})
        entry["amount"] += r["tds_paise"] // 100

    items: list[TDSItem] = []
    for tan in sorted(set(claimed) | set(as26)):
        c = claimed.get(tan, {}).get("amount", 0)
        a = as26.get(tan, {}).get("amount", 0)
        name = (claimed.get(tan, {}).get("name")
                or as26.get(tan, {}).get("name") or "")
        if abs(c - a) <= tol:
            status = "matched"
        elif c > a:
            status = "claim_exceeds_26as"
        else:
            status = "unclaimed_26as"
        items.append(TDSItem(key=f"tds:{tan}", tan=tan, deductor=name,
                             claimed_inr=c, as26_inr=a, status=status))
    return items
