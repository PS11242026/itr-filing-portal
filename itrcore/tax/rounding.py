"""Rounding rules.

pct(): component amounts round half-up to the rupee (matches utility lines).
r10(): Sections 288A (total income) and 288B (final tax/refund) — nearest
₹10, ₹5 rounds up.
r100_floor(): Rule 119A — interest u/s 234A/B/C is computed on the amount
rounded DOWN to a multiple of ₹100.
"""

from decimal import ROUND_HALF_UP, Decimal


def to_rupees(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def pct(amount: int, rate) -> int:
    """rate may be int/float/str (e.g. 12.5); exact decimal arithmetic."""
    return to_rupees(Decimal(amount) * Decimal(str(rate)) / Decimal(100))


def r10(x: int) -> int:
    if x >= 0:
        return ((x + 5) // 10) * 10
    return -(((-x) + 5) // 10) * 10


def r100_floor(x: int) -> int:
    if x <= 0:
        return 0
    return (x // 100) * 100
