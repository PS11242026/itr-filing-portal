"""AIS category totals vs the figures going into the computation."""

from dataclasses import dataclass

from itrcore.analysis.classify import load_thresholds
from itrcore.authz import TaxpayerScope
from itrcore.db.repos import importers as importers_repo


@dataclass(frozen=True)
class AISItem:
    key: str                       # "ais:<category>"
    category: str
    label: str
    ais_inr: int
    books_inr: int
    status: str                    # matched | mismatch | ais_only

    @property
    def diff_inr(self) -> int:
        return self.books_inr - self.ais_inr


def _books_figures(inputs_dict: dict) -> dict[str, int]:
    os_ = inputs_dict.get("other_sources", {})
    rent = sum(hp.get("annual_rent_received", 0)
               for hp in inputs_dict.get("house_properties", []))
    salary = sum(s.get("gross_salary", 0)
                 for s in inputs_dict.get("salaries", []))
    prop_sale = sum(cg.get("full_value_consideration", 0)
                    for cg in inputs_dict.get("capital_gains", [])
                    if cg.get("asset_class") == "property")
    sec_sale = sum(cg.get("full_value_consideration", 0)
                   for cg in inputs_dict.get("capital_gains", [])
                   if cg.get("asset_class") in ("listed_equity_stt",
                                                "equity_mf",
                                                "listed_other"))
    return {
        "interest_savings": os_.get("savings_interest", 0),
        "interest_deposits": os_.get("fd_interest", 0),
        "dividend": os_.get("dividend", 0),
        "rent": rent,
        "salary": salary,
        "sale_immovable": prop_sale,
        "sale_securities": sec_sale,
    }


_LABELS = {
    "interest_savings": "Savings interest",
    "interest_deposits": "Deposit interest",
    "dividend": "Dividend",
    "rent": "Rent received",
    "salary": "Gross salary",
    "sale_immovable": "Immovable property sale consideration",
    "sale_securities": "Securities sale consideration",
}


def compare(scope: TaxpayerScope, ay: str,
            inputs_dict: dict) -> list[AISItem]:
    tol = load_thresholds()["recon"]["ais_tolerance_inr"]
    ais_totals: dict[str, int] = {}
    for r in importers_repo.list_ais(scope, ay):
        if r["category"] == "other":
            continue
        ais_totals[r["category"]] = (ais_totals.get(r["category"], 0)
                                     + r["amount_paise"] // 100)
    books = _books_figures(inputs_dict)

    items: list[AISItem] = []
    for category, ais_amount in sorted(ais_totals.items()):
        book_amount = books.get(category, 0)
        if book_amount == 0 and ais_amount > tol:
            status = "ais_only"
        elif abs(book_amount - ais_amount) <= tol:
            status = "matched"
        else:
            status = "mismatch"
        items.append(AISItem(
            key=f"ais:{category}", category=category,
            label=_LABELS.get(category, category), ais_inr=ais_amount,
            books_inr=book_amount, status=status))
    return items
