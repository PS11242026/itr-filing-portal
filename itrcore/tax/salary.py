"""Head: Salaries (Sections 15–17)."""

from itrcore.tax.types import HeadResult, Line, SalaryIncome


def compute(salaries: tuple[SalaryIncome, ...],
            regime_cfg: dict) -> HeadResult:
    if not salaries:
        return HeadResult(head="salary", amount=0)
    lines: list[Line] = []
    net_total = 0
    prof_tax_total = 0
    for s in salaries:
        net = max(0, s.gross_salary - s.exempt_allowances)
        lines.append(Line(f"Gross salary — {s.employer_name}", "17",
                          s.gross_salary))
        if s.exempt_allowances:
            lines.append(Line(f"Less: exempt allowances — {s.employer_name}",
                              "10", -s.exempt_allowances))
        net_total += net
        prof_tax_total += max(0, s.prof_tax)

    std = min(regime_cfg["standard_deduction_salary"], net_total)
    if std:
        lines.append(Line("Less: standard deduction", "16(ia)", -std))
    # 16(ii) entertainment allowance — govt employees, old regime only (cap
    # present only in the old-regime config): least of cap, actual, 1/5 net.
    ent = 0
    ent_cap = regime_cfg.get("entertainment_allowance_cap")
    if ent_cap:
        ent_total = sum(max(0, s.entertainment_allowance) for s in salaries)
        ent = min(ent_cap, ent_total, net_total // 5, max(0, net_total - std))
    if ent:
        lines.append(Line("Less: entertainment allowance", "16(ii)", -ent))
    prof = min(prof_tax_total, max(0, net_total - std - ent))
    if prof:
        lines.append(Line("Less: professional tax", "16(iii)", -prof))

    income = max(0, net_total - std - ent - prof)
    lines.append(Line("Income chargeable under Salaries", "15", income))
    return HeadResult(head="salary", amount=income, lines=tuple(lines))
