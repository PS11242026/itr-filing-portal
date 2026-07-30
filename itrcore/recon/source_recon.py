"""AIS / TIS vs Form 26AS cross-reconciliation.

AIS and TIS report income by category; Form 26AS reports the amount
paid/credited on which tax was deducted, per deductor and section. Mapping
26AS TDS sections to the AIS/TIS category taxonomy lets us cross-check the
two ITD sources against each other and surface differences for a client
query. A material disagreement between sources that both report a category is
a `mismatch`; a category only one source reports is `one_sided` (often normal,
e.g. savings interest carries no TDS).
"""

from dataclasses import dataclass

from itrcore.analysis.classify import load_thresholds
from itrcore.authz import TaxpayerScope
from itrcore.db.repos import importers as importers_repo

# 26AS TDS section → AIS/TIS income category.
_SECTION_CATEGORY = {
    "192": "salary", "192A": "salary",
    "194A": "interest_deposits",
    "194": "dividend", "194K": "dividend",
    "194I": "rent", "194-I": "rent", "194IB": "rent",
    "194IA": "sale_immovable",
}

_LABELS = {
    "salary": "Salary",
    "interest_savings": "Interest (savings)",
    "interest_deposits": "Interest (deposits)",
    "dividend": "Dividend",
    "rent": "Rent",
    "sale_immovable": "Property sale",
    "sale_securities": "Securities sale",
}


@dataclass(frozen=True)
class SourceItem:
    key: str                       # "src:<category>"
    category: str
    label: str
    ais_inr: int
    tis_inr: int
    as26_inr: int
    status: str                    # matched | mismatch | one_sided

    def query_text(self) -> str:
        return (f"For {self.label}, the ITD sources disagree — AIS "
                f"₹{self.ais_inr:,}, TIS ₹{self.tis_inr:,}, Form 26AS "
                f"₹{self.as26_inr:,}. Please confirm the correct amount and "
                "the reason for the difference.")


def _by_category(rows, cat_key, amount) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        cat = cat_key(r)
        if cat is None:
            continue
        out[cat] = out.get(cat, 0) + amount(r)
    return out


def compare(scope: TaxpayerScope, ay: str) -> list[SourceItem]:
    tol = load_thresholds()["recon"]["ais_tolerance_inr"]
    ais = _by_category(
        importers_repo.list_ais(scope, ay),
        lambda r: None if r["category"] == "other" else r["category"],
        lambda r: r["amount_paise"] // 100)
    tis = _by_category(
        importers_repo.list_tis(scope, ay),
        lambda r: r["category"],
        lambda r: r["derived_value_paise"] // 100)
    as26 = _by_category(
        importers_repo.list_tds(scope, ay, source="26as"),
        lambda r: _SECTION_CATEGORY.get((r["section"] or "").strip().upper()),
        lambda r: (r["amount_paid_paise"] or 0) // 100)

    items: list[SourceItem] = []
    for cat in sorted(set(ais) | set(tis) | set(as26)):
        a, t, s = ais.get(cat, 0), tis.get(cat, 0), as26.get(cat, 0)
        present = [v for v in (a, t, s) if v > 0]
        if len(present) <= 1:
            status = "one_sided"
        elif max(present) - min(present) <= tol:
            status = "matched"
        else:
            status = "mismatch"
        items.append(SourceItem(
            key=f"src:{cat}", category=cat, label=_LABELS.get(cat, cat),
            ais_inr=a, tis_inr=t, as26_inr=s, status=status))
    return items


def mismatches(scope: TaxpayerScope, ay: str) -> list[SourceItem]:
    return [i for i in compare(scope, ay) if i.status == "mismatch"]
