"""Head: Income from other sources (Section 56).

Manual add/less adjustment lines (``AdjustmentEntry`` rows targeting
``other_sources.<field>``) are applied to the field BEFORE the head math —
so the family-pension 1/3rd deduction and (via :func:`adjusted_fields`)
the 80TTA/80TTB interest caps see the adjusted figures — and each one is
printed as its own line, Genius-style.
"""

from itrcore.tax.types import AdjustmentEntry, HeadResult, Line, OtherSources

_FIELDS = ("savings_interest", "post_office_savings_interest", "fd_interest",
           "dividend", "family_pension", "taxable_gifts", "other")


def _by_field(adjustments) -> dict[str, list[AdjustmentEntry]]:
    out: dict[str, list[AdjustmentEntry]] = {}
    for a in adjustments:
        _, _, field = a.target.partition(".")
        if field in _FIELDS:
            out.setdefault(field, []).append(a)
    return out


def post_office_exemption(gross: int, joint: bool, cfg: dict) -> int:
    """Sec 10(15)(i) r/w Rule 2BBA item (i): interest on a Post Office
    savings-bank account is exempt up to ₹3,500 (₹7,000 where the account is
    held jointly). 115BAC does not withdraw it, so it applies under both
    regimes — and it is an exemption, not a Chapter VI-A deduction, so it
    comes off the income itself."""
    cap = cfg["joint" if joint else "single"]
    return min(max(0, gross), cap)


def adjusted_fields(os_: OtherSources, adjustments,
                    exempt_cfg: dict | None = None) -> dict[str, int]:
    """Field → adjusted value (floored at 0), for the deduction engine's
    80TTA/80TTB interest figures. Post-office savings interest is folded into
    ``savings_interest`` NET of its 10(15)(i) exemption, which is exactly the
    figure 80TTA/80TTB caps operate on."""
    adj = _by_field(adjustments)
    out = {f: max(0, getattr(os_, f)
                  + sum(a.amount for a in adj.get(f, ())))
           for f in _FIELDS}
    if exempt_cfg is not None:
        po = out["post_office_savings_interest"]
        out["savings_interest"] += po - post_office_exemption(
            po, os_.post_office_joint_account, exempt_cfg)
    return out


def compute(os_: OtherSources, regime_cfg: dict,
            adjustments: tuple[AdjustmentEntry, ...] = (),
            post_office_cfg: dict | None = None) -> HeadResult:
    adj = _by_field(adjustments)
    lines: list[Line] = []
    total = 0

    def _field(field: str, base: int, label: str, section: str) -> int:
        if base:
            lines.append(Line(label, section, base))
        for a in adj.get(field, ()):
            lines.append(Line(f"Adjustment: {a.label or 'manual'}", section,
                              a.amount))
        return base + sum(a.amount for a in adj.get(field, ()))

    simple = [
        ("savings_interest", "Interest — savings accounts", "56",
         os_.savings_interest),
        ("fd_interest", "Interest — deposits (FD/RD/others)", "56",
         os_.fd_interest),
        ("dividend", "Dividend income", "56", os_.dividend),
        ("taxable_gifts",
         "Taxable gifts (aggregate > ₹50,000 from non-relatives)",
         "56(2)(x)", os_.taxable_gifts),
        ("other", "Other income", "56", os_.other),
    ]
    for field, label, section, base in simple:
        total += _field(field, base, label, section)

    po = _field("post_office_savings_interest",
                os_.post_office_savings_interest,
                "Interest — post office savings account", "56")
    if po:
        exempt = post_office_exemption(
            po, os_.post_office_joint_account,
            post_office_cfg or {"single": 0, "joint": 0})
        if exempt:
            lines.append(Line(
                "Less: exempt u/s 10(15)(i) — post office savings account"
                + (" (joint)" if os_.post_office_joint_account else ""),
                "10(15)(i)", -exempt))
        po -= exempt
    total += po

    fp = _field("family_pension", os_.family_pension, "Family pension", "56")
    if fp > 0:
        ded = min(fp // 3, regime_cfg["family_pension_deduction_cap"])
        if ded:
            lines.append(Line("Less: family pension deduction", "57(iia)",
                              -ded))
        total += fp - ded
    else:
        total += fp

    ded57 = min(max(0, os_.deductions_57), max(0, total))
    if ded57:
        lines.append(Line("Less: expenses incurred to earn income", "57",
                          -ded57))
        total -= ded57
    if total < 0:                      # over-subtraction via adjustments
        lines.append(Line("Other sources floored at zero (adjustments "
                          "exceed the income)", "56", -total))
        total = 0

    lines.append(Line("Income from other sources", "56", total))
    return HeadResult(head="other_sources", amount=total, lines=tuple(lines))
