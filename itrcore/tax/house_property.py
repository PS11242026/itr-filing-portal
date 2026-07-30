"""Head: Income from house property (Sections 22–27).

Correct statutory order (the SRS wording was off): NAV = GAV − municipal
taxes paid by the owner; then 30% standard deduction OF NAV (24(a)); then
interest on borrowed capital (24(b)).

New regime (115BAC): self-occupied 24(b) interest is not deductible, and a
net HP loss can neither be set off against other heads nor carried forward
(it lapses). The aggregate-level set-off/cap is applied in regimes.py.
"""

from decimal import ROUND_HALF_UP, Decimal

from itrcore.tax.rounding import pct
from itrcore.tax.types import HeadResult, HouseProperty, Line


def _share(amount: int, share_pct: float) -> int:
    return int((Decimal(amount) * Decimal(str(share_pct)) / 100)
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute(props: tuple[HouseProperty, ...], hp_cfg: dict,
            *, new_regime: bool) -> HeadResult:
    if not props:
        return HeadResult(head="house_property", amount=0)

    lines: list[Line] = []
    let_out_total = 0
    sop_interest_claims = 0

    for i, p in enumerate(props, start=1):
        annual_pci = p.pre_construction_interest_total // 5
        interest = max(0, p.interest_24b) + annual_pci
        tag = p.address or f"Property {i}"

        if p.kind == "self_occupied":
            if new_regime:
                if interest:
                    lines.append(Line(
                        f"{tag}: self-occupied — 24(b) interest ₹{interest:,}"
                        " not deductible under 115BAC", "115BAC", 0))
            else:
                sop_interest_claims += interest
                lines.append(Line(f"{tag}: self-occupied — interest claim",
                                  "24(b)", -interest))
            continue

        gav = max(0, p.annual_rent_received)
        municipal = min(max(0, p.municipal_taxes_paid), gav)
        nav = gav - municipal
        std30 = pct(nav, hp_cfg["standard_deduction_pct"])
        income = nav - std30 - interest
        income = _share(income, p.co_owner_share_pct)
        lines.append(Line(f"{tag}: gross annual value (rent received)", "23",
                          gav))
        if municipal:
            lines.append(Line(f"{tag}: less municipal taxes paid", "23",
                              -municipal))
        lines.append(Line(f"{tag}: less 30% of NAV", "24(a)", -std30))
        if interest:
            lines.append(Line(f"{tag}: less interest on borrowed capital",
                              "24(b)", -interest))
        if p.co_owner_share_pct != 100.0:
            lines.append(Line(f"{tag}: owner share "
                              f"{p.co_owner_share_pct:g}%", "26", income))
        let_out_total += income

    sop_income = 0
    if not new_regime and sop_interest_claims:
        capped = min(sop_interest_claims, hp_cfg["sop_interest_cap"])
        if capped < sop_interest_claims:
            lines.append(Line("Self-occupied interest capped", "24(b)",
                              -(sop_interest_claims - capped)))
        sop_income = -capped

    aggregate = let_out_total + sop_income

    arrears_total = sum(max(0, p.arrears_rent_received) for p in props)
    if arrears_total:
        arr_ded = pct(arrears_total, hp_cfg["standard_deduction_pct"])
        lines.append(Line("Arrears / unrealised rent received", "25A",
                          arrears_total))
        lines.append(Line("Less: 30% of arrears", "25A", -arr_ded))
        aggregate += arrears_total - arr_ded

    if new_regime and aggregate < 0:
        lines.append(Line(
            f"Net HP loss ₹{-aggregate:,} cannot be set off or carried "
            "forward under 115BAC — lapses", "115BAC", 0))
        aggregate = 0

    lines.append(Line("Income from house property", "22", aggregate))
    return HeadResult(head="house_property", amount=aggregate,
                      lines=tuple(lines))
