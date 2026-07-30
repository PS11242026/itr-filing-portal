"""Pre-compute reconciliation: imported AIS/TIS/26AS figures that the
current computation inputs do NOT reflect (whether typed manually or
auto-filled). The Computation page shows these as a blocking dialog before
running the engine — the practitioner either fixes the inputs or computes
anyway with eyes open.

Pure read/compare: never writes, never blocks by itself. Tolerance comes
from the same thresholds file the auto-fill AIS/TIS comparison uses.
"""

from dataclasses import dataclass

from itrcore.analysis.classify import load_thresholds
from itrcore.authz import TaxpayerScope
from itrcore.db.repos import importers as importers_repo


@dataclass(frozen=True)
class Gap:
    head: str            # where in the computation it belongs
    message: str         # practitioner-readable, amounts included
    severity: str        # "critical" (income/TDS missing) | "info"


def _r(paise) -> int:
    return int(paise or 0) // 100


def _derived_totals(scope: TaxpayerScope, ay: str) -> dict[str, int]:
    """Per-category rupee totals: TIS derived value wins, else AIS."""
    ais: dict[str, int] = {}
    for row in importers_repo.list_ais(scope, ay):
        ais[row["category"]] = ais.get(row["category"], 0) \
            + _r(row["amount_paise"])
    tis: dict[str, int] = {}
    for row in importers_repo.list_tis(scope, ay):
        tis[row["category"]] = tis.get(row["category"], 0) \
            + _r(row["derived_value_paise"])
    return {**ais, **tis}


def find_gaps(scope: TaxpayerScope, ay: str, inputs: dict) -> list[Gap]:
    gaps: list[Gap] = []
    tol = load_thresholds()["recon"]["ais_tolerance_inr"]
    totals = _derived_totals(scope, ay)
    os_in = inputs.get("other_sources", {}) or {}

    def _adj(target: str) -> int:
        """Signed total of the manual add/less rows on a target — the
        computation carries base + adjustments, so recon compares that."""
        return sum(int(a.get("amount", 0) or 0)
                   for a in inputs.get("adjustments", [])
                   if a.get("target") == target)

    def _check_amount(imported: int | None, entered: int, head: str,
                      label: str) -> None:
        if imported is None or imported <= 0:
            return
        if entered + tol < imported:
            gaps.append(Gap(
                head=head, severity="critical",
                message=f"{label}: AIS/TIS report ₹{imported:,} but the "
                        f"computation carries ₹{entered:,}."))

    def _check(category: str, entered: int, head: str, label: str) -> None:
        _check_amount(totals.get(category), entered, head, label)

    def _os(field: str) -> int:
        return (int(os_in.get(field, 0) or 0)
                + _adj(f"other_sources.{field}"))

    _check("interest_savings", _os("savings_interest"),
           "Other sources", "Savings-account interest")
    _check("interest_deposits", _os("fd_interest"),
           "Other sources", "FD/deposit interest")
    _check("dividend", _os("dividend"), "Other sources", "Dividend")
    _check("family_pension", _os("family_pension"),
           "Other sources", "Family pension")
    other_imported = (totals.get("interest_others", 0)
                      + totals.get("other_income", 0))
    _check_amount(other_imported or None, _os("other"), "Other sources",
                  "Other income (interest-others & AIS other income)")

    salary_entered = (sum(int(s.get("gross_salary", 0) or 0)
                          for s in inputs.get("salaries", []))
                      + _adj("salary"))
    _check("salary", salary_entered, "Salary", "Gross salary")

    rent_entered = sum(int(h.get("annual_rent_received", 0) or 0)
                       for h in inputs.get("house_properties", []))
    _check("rent", rent_entered, "House property", "Rent received")

    # Transactions AIS only totals — presence checks, not rupee matches.
    sec_sale = totals.get("sale_securities", 0)
    if sec_sale > 0 and not any(
            r.get("asset_class") in ("listed_equity_stt", "equity_mf",
                                     "listed_other", "unlisted", "other")
            for r in inputs.get("capital_gains", [])):
        gaps.append(Gap(
            head="Capital gains", severity="critical",
            message=f"AIS/TIS report securities sales of ₹{sec_sale:,} "
                    "but the computation has no securities capital-gains "
                    "row."))
    prop_sale = totals.get("sale_immovable", 0)
    if prop_sale > 0 and not any(
            r.get("asset_class") == "property"
            for r in inputs.get("capital_gains", [])):
        gaps.append(Gap(
            head="Capital gains", severity="critical",
            message=f"AIS/TIS report an immovable-property sale of "
                    f"₹{prop_sale:,} but the computation has no property "
                    "capital-gains row."))

    # Regular books of accounts: each business row carrying books must
    # reconcile with the books' derived net profit.
    books = inputs.get("books", {}) or {}
    if books:
        from itrcore.ingest import books_excel
        rows = inputs.get("regular_businesses", [])
        entered_by_name = {
            (b.get("name") or f"Business {i + 1}"):
                int(b.get("net_profit_before_tax", 0) or 0)
            for i, b in enumerate(rows)}
        for biz_name, parsed in books.items():
            try:
                net = books_excel.summarise(parsed).net_profit
            except Exception:
                continue
            entered = entered_by_name.get(biz_name)
            if entered is None:
                gaps.append(Gap(
                    head="Business (books)", severity="critical",
                    message=f"Books are on file for '{biz_name}' but no "
                            "regular-books business row carries that "
                            "name."))
            elif entered != net:
                gaps.append(Gap(
                    head="Business (books)", severity="critical",
                    message=f"'{biz_name}': business income ₹{entered:,} "
                            f"does not reconcile with the books' net "
                            f"profit ₹{net:,}."))

    # 26AS TDS — every imported deductor row should be in the inputs, and
    # the total should not fall short.
    as26 = importers_repo.list_tds(scope, ay, source="26as")
    if as26:
        entered_rows = inputs.get("tds", [])
        entered_total = (sum(int(t.get("amount", 0) or 0)
                             for t in entered_rows)
                         + _adj("tds") + _adj("tcs"))
        entered_tans = {str(t.get("tan") or "").strip().upper()
                        for t in entered_rows}
        entered_names = {str(t.get("deductor") or "").strip().casefold()
                         for t in entered_rows}
        imported_total = 0
        for row in as26:
            amount = _r(row["tds_paise"])
            imported_total += amount
            tan = str(row["tan"] or "").strip().upper()
            name = str(row["deductor_name"] or "").strip()
            present = (tan and tan in entered_tans) or (
                name and name.casefold() in entered_names)
            if amount > 0 and not present:
                gaps.append(Gap(
                    head="TDS", severity="critical",
                    message=f"26AS TDS of ₹{amount:,} by "
                            f"{name or tan or 'a deductor'} is not in the "
                            "TDS tab."))
        if not gaps or all(g.head != "TDS" for g in gaps):
            if entered_total + tol < imported_total:
                gaps.append(Gap(
                    head="TDS", severity="critical",
                    message=f"26AS total TDS is ₹{imported_total:,} but "
                            f"the TDS tab carries ₹{entered_total:,}."))
    return gaps
