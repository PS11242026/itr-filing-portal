"""Form 26AS (TRACES text/CSV export) parser → TDS entries.

The TRACES text export is caret (^) separated with PART-wise sections.
This parser is deliberately tolerant: it scans for deductor rows carrying
a TAN plus amounts, and everything it cannot read lands in `skipped` for
manual entry rather than raising.
"""

import re
from decimal import Decimal

from itrcore.ingest.normalize import parse_amount

_TAN_RE = re.compile(r"^[A-Z]{4}\d{5}[A-Z]$")


def _to_paise(value) -> int | None:
    amount = parse_amount(value)
    if amount is None:
        return None
    return int((amount * Decimal(100)).quantize(Decimal("1")))


def parse_26as_text(data: bytes) -> dict:
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("could not decode the 26AS file")

    entries: list[dict] = []
    skipped: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        sep = "^" if "^" in line else ("," if "," in line else None)
        if sep is None:
            continue
        cells = [c.strip().strip('"') for c in line.split(sep)]
        tan_idx = next((i for i, c in enumerate(cells)
                        if _TAN_RE.match(c)), None)
        if tan_idx is None:
            continue
        amounts = [(_to_paise(c), i) for i, c in enumerate(cells)]
        amounts = [(a, i) for a, i in amounts
                   if a is not None and i > tan_idx]
        if not amounts:
            skipped.append(f"line {line_no}: TAN found but no amounts")
            continue
        # Convention in TRACES rows: amount paid/credited appears before
        # tax deducted/deposited; the LAST amount is the TDS deposited.
        tds_paise = amounts[-1][0]
        paid_paise = amounts[0][0] if len(amounts) > 1 else None
        name = ""
        if tan_idx > 0 and cells[tan_idx - 1] and \
                _to_paise(cells[tan_idx - 1]) is None:
            name = cells[tan_idx - 1][:100]
        section = next((c for c in cells
                        if re.fullmatch(r"\d{3}[A-Z]{0,3}|192|194[A-Z]?",
                                        c)), "")
        entries.append({"tan": cells[tan_idx], "deductor_name": name,
                        "section": section,
                        "amount_paid_paise": paid_paise,
                        "tds_paise": tds_paise})
    if not entries:
        raise ValueError("no TDS rows recognised — use the text/CSV export "
                         "from TRACES (not the PDF), or enter TDS manually")
    return {"entries": entries, "skipped": skipped}
