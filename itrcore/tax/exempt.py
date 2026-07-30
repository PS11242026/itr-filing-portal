"""Exempt income (Schedule EI) — Sections 10/DTAA incomes reported in the
return but never included in total income.

The one computational effect: net agricultural income u/s 10(1) above the
Finance-Act threshold is aggregated with taxable income for RATE purposes
(partial integration) in both regimes; that adjustment lives in regimes.py.
This module only classifies and totals the entries.
"""

from dataclasses import dataclass

from itrcore.tax.types import ExemptIncomeEntry, Line

AGRI_SECTION = "10(1)"

# Practitioner-facing catalogue (Schedule EI natures). Labels only — every
# statutory NUMBER stays in the AY config.
EXEMPT_SECTIONS: dict[str, str] = {
    "10(1)": "Agricultural income",
    "10(2)": "Share from HUF income",
    "10(2A)": "Share of profit from partnership firm",
    "10(10)": "Gratuity (exempt portion)",
    "10(10AA)": "Leave encashment (exempt portion)",
    "10(10C)": "VRS compensation",
    "10(10D)": "Life insurance maturity proceeds",
    "10(11)": "PF / PPF interest & withdrawal",
    "10(12)": "Recognised PF accumulated balance",
    "10(15)": "Exempt interest (tax-free bonds etc.)",
    "10(16)": "Scholarships",
    "10(17)": "MP / MLA allowances",
    "10(19)": "Armed-forces family pension",
    "10(26)": "Scheduled-tribe area income",
    "10(34A)": "Share buy-back proceeds",
    "DTAA": "Income not chargeable to tax under DTAA",
    "Other": "Any other exempt income",
}


@dataclass(frozen=True)
class ExemptResult:
    total: int
    agri_income: int                 # net agricultural income u/s 10(1)
    lines: tuple[Line, ...]


def compute(entries: tuple[ExemptIncomeEntry, ...]) -> ExemptResult:
    lines: list[Line] = []
    total = 0
    agri = 0
    for e in entries:
        if e.amount <= 0:
            continue
        nature = EXEMPT_SECTIONS.get(e.section, e.section)
        label = f"{nature} — {e.description}" if e.description else nature
        lines.append(Line(label, e.section, e.amount))
        total += e.amount
        if e.section == AGRI_SECTION:
            agri += e.amount
    if total:
        lines.append(Line("Total exempt income (not part of total income)",
                          "Sch EI", total))
    return ExemptResult(total=total, agri_income=agri, lines=tuple(lines))
