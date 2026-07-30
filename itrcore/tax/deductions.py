"""Chapter VI-A deductions: caps, the 80CCE group cap, regime filtering.

VI-A never reduces special-rate income (111A/112/112A) — the caller passes
cap_base = GTI excluding those amounts.
"""

from itrcore.tax.types import Line


def compute(claims: tuple[tuple[str, int], ...], cfg_raw: dict, *,
            regime: str, senior: bool, cap_base: int,
            savings_interest: int,
            total_interest: int) -> tuple[int, tuple[Line, ...]]:
    ded_cfg = cfg_raw["deductions"]
    allowed_new = set(cfg_raw["new_regime"]["allowed_deductions"])
    group_cap = ded_cfg["80CCE_group_cap"]

    lines: list[Line] = []
    total = 0
    group_used = 0

    for section, claimed in claims:
        amt = max(0, int(claimed))
        if amt == 0:
            continue
        if regime == "new" and section not in allowed_new:
            lines.append(Line(f"{section} claimed ₹{amt:,} — not available "
                              "under the new regime", section, 0))
            continue

        scfg = ded_cfg.get(section) if isinstance(ded_cfg.get(section),
                                                  dict) else None
        cap = scfg.get("cap") if scfg else None

        if section == "80TTA":
            if senior:
                lines.append(Line("80TTA not available to senior citizens "
                                  "(use 80TTB)", section, 0))
                continue
            amt = min(amt, max(0, savings_interest), cap or amt)
        elif section == "80TTB":
            if not senior:
                lines.append(Line("80TTB is senior-citizens only", section,
                                  0))
                continue
            amt = min(amt, max(0, total_interest), cap or amt)
        elif cap is not None:
            amt = min(amt, cap)

        if scfg and scfg.get("group") == "80CCE":
            room = max(0, group_cap - group_used)
            if amt > room:
                lines.append(Line(f"{section} restricted by 80CCE group cap",
                                  "80CCE", -(amt - room)))
            amt = min(amt, room)
            group_used += amt

        if amt <= 0:
            continue
        total += amt
        lines.append(Line(f"Deduction u/s {section}", section, -amt))

    if total > max(0, cap_base):
        lines.append(Line("Deductions restricted to gross total income "
                          "(excluding special-rate income)", "VI-A",
                          -(total - max(0, cap_base))))
        total = max(0, cap_base)

    return total, tuple(lines)
