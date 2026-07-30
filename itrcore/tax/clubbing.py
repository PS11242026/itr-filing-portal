"""Clubbing of income (Sections 60–64).

Income of a spouse / minor child / transferred asset that the Act clubs in
the assessee's hands. v1 clubs at normal (slab) rates into GTI, applying the
Section 10(32) exemption (per minor child) for minor-child income. Amounts in
whole rupees; the exemption comes from the AY config.
"""

from dataclasses import dataclass

from itrcore.tax.types import ClubbedIncome, Line


@dataclass(frozen=True)
class ClubbingResult:
    total: int
    lines: tuple[Line, ...]


def compute(entries: tuple[ClubbedIncome, ...], cfg: dict) -> ClubbingResult:
    minor_exempt = cfg["minor_exemption_per_child"]
    lines: list[Line] = []
    total = 0
    for e in entries:
        if e.amount <= 0:
            continue
        label = e.description or f"Clubbed income ({e.head})"
        lines.append(Line(f"{label} — clubbed u/s {e.section}", e.section,
                          e.amount))
        amount = e.amount
        if e.minor_child:
            exempt = min(minor_exempt, amount)
            if exempt:
                lines.append(Line("Less: Sec 10(32) exemption (minor child)",
                                  "10(32)", -exempt))
                amount -= exempt
        total += amount
    return ClubbingResult(total=total, lines=tuple(lines))
