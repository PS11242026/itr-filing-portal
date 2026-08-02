"""Form 26AS (TRACES text/CSV export) parser → TDS entries and challans.

The TRACES text export is caret (^) separated with PART-wise sections.
This parser is deliberately tolerant: it scans for deductor rows carrying
a TAN plus amounts, and everything it cannot read lands in `skipped` for
manual entry rather than raising.

It also reads the tax-paid section — "PART C - Details of Tax Paid (other
than TDS or TCS)" — whose rows carry a BSR code and challan serial INSTEAD
of a TAN, which is why the TAN scan never saw them. Only the text/CSV
export and pre-AY-2023-24 statements still have this part; the current
TRACES Annual Tax Statement PDF dropped it in favour of AIS Part B3.
"""

import re
from decimal import Decimal

from itrcore.ingest.normalize import parse_amount

_TAN_RE = re.compile(r"^[A-Z]{4}\d{5}[A-Z]$")
_BSR_RE = re.compile(r"^\d{7}$")
_DATE_RE = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$")
# Minor head → what the payment was. TRACES prints the code, the words, or
# both depending on the export.
_MINOR_HEAD_KIND = {
    "100": "advance", "advance tax": "advance",
    "300": "self_assessment", "self assessment tax": "self_assessment",
    "self assessment": "self_assessment",
    "400": "regular_assessment", "tax on regular assessment":
        "regular_assessment",
}


def _challan_from_cells(cells: list[str]) -> dict | None:
    """A PART-C row:

        Sr | Major head | Minor head | Tax | Surcharge | Cess | Others |
        Total | BSR code | Date of deposit | Challan serial

    Anchored on the 7-digit BSR code, then read POSITIONALLY. Picking the
    money columns by "looks like a number" instead would happily take the
    BSR code (0000002) or the challan serial (22222) for a rupee figure —
    both are bare digits, and one of them is usually the largest number in
    the row."""
    bsr_idx = next((i for i, c in enumerate(cells) if _BSR_RE.match(c)), None)
    if bsr_idx is None or bsr_idx == 0:
        return None
    tail = cells[bsr_idx + 1:]
    date_cell = next((c for c in tail if _DATE_RE.match(c)), "")
    if not date_cell:
        return None
    serial = ""
    after_date = tail[tail.index(date_cell) + 1:]
    if after_date:
        serial = after_date[0].strip()

    # Money columns are everything between the heads and the BSR code; the
    # last of them is Total (A+B+C+D).
    money = [parse_amount(c) for c in cells[1:bsr_idx]]
    money = [m for m in money if m is not None]
    total = parse_amount(cells[bsr_idx - 1])
    if total is None or total <= 0:
        total = max(money) if money else None
    if not total or total <= 0:
        return None

    kind = "advance"
    for c in cells[1:bsr_idx]:
        hit = _MINOR_HEAD_KIND.get(c.strip().lower())
        if hit:
            kind = hit
            break
    m = _DATE_RE.match(date_cell)
    return {
        "kind": kind,
        "paid_on": f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}",
        "amount": int(total),
        "reference": f"{bsr_idx and cells[bsr_idx]} - {serial}".strip(" -"),
    }


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
    challans: list[dict] = []
    skipped: list[str] = []
    in_part_c = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if "tax paid (other than tds or tcs)" in low or \
                re.search(r"\bpart[-\s]*c\b", low):
            in_part_c = True
        elif re.search(r"\bpart[-\s]*[abd-z]\b", low):
            in_part_c = False
        sep = "^" if "^" in line else ("," if "," in line else None)
        if sep is None:
            continue
        cells = [c.strip().strip('"') for c in line.split(sep)]
        tan_idx = next((i for i, c in enumerate(cells)
                        if _TAN_RE.match(c)), None)
        if tan_idx is None:
            # PART-C rows have a BSR code where a deductor row has a TAN.
            if in_part_c:
                challan = _challan_from_cells(cells)
                if challan is not None:
                    challans.append(challan)
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
    if not entries and not challans:
        raise ValueError("no TDS rows recognised — use the text/CSV export "
                         "from TRACES (not the PDF), or enter TDS manually")
    return {"entries": entries, "challans": challans, "skipped": skipped}
