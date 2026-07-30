"""Amount and date normalisation for Indian bank data.

parse_amount handles: Indian lakh grouping (1,23,456.78), western grouping,
currency symbols, bracketed negatives (1,234.56), trailing-sign negatives
(1234.56-), and Dr/Cr suffixes. Everything lands as integer paise.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser

_CLEAN_RE = re.compile(r"[₹$\s]|INR|Rs\.?", re.IGNORECASE)


def parse_amount(value) -> Decimal | None:
    """Returns a Decimal (rupees) or None when the cell is not an amount."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip()
    if not text or text in ("-", "--", "NA"):
        return None
    negative = False
    suffix = text[-2:].upper()
    if suffix in ("DR", "CR"):
        negative = suffix == "DR"
        text = text[:-2].strip()
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    text = _CLEAN_RE.sub("", text).replace(",", "")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def to_paise(amount: Decimal | None) -> int | None:
    if amount is None:
        return None
    return int((amount * 100).quantize(Decimal("1")))


def parse_date(value, formats: tuple[str, ...] = ()) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return dateparser.parse(text, dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError):
        return None


def norm_narration(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()
