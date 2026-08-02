"""Form 26AS (TRACES 'Annual Tax Statement') PDF parser.

The 26AS a practitioner actually downloads is a multi-page PDF with parts
PART-I…PART-X. Each part lists deductors/collectors as a summary row
(name, TAN, totals) followed by that deductor's transaction rows. pdfplumber
recovers the 9-column grid cleanly, so this reads it structurally rather than
scraping text.

Returns both a rich, part-wise structure (for the Genius-style display and the
regenerated PDF) and a flat ``entries`` list — one per deductor — in exactly
the shape :func:`itrcore.ingest.form26as.parse_26as_text` returns, so the
existing 26AS importer and reconciliation consume it unchanged.
"""

import io
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

import pdfplumber

_TAN_RE = re.compile(r"^[A-Z]{4}\d{5}[A-Z]$")
_SECTION_RE = re.compile(r"^\d{3}[A-Z]{0,3}$|^19[0-9][A-Z]{0,3}$|"
                         r"^20[0-9][A-Z]{0,3}$")
_PART_RE = re.compile(r"\bPART[-\s]*([IVX]+|X)\b", re.IGNORECASE)


@dataclass
class Txn:
    sr: str = ""
    section: str = ""
    txn_date: str = ""
    status: str = ""
    booking_date: str = ""
    remarks: str = ""
    amount_paid: str = ""
    tax_deducted: str = ""
    tds_deposited: str = ""


@dataclass
class Deductor:
    name: str = ""
    tan: str = ""
    total_paid: str = ""
    total_tax_deducted: str = ""
    total_tds_deposited: str = ""
    transactions: list[Txn] = field(default_factory=list)


@dataclass
class Part:
    part_id: str = ""
    title: str = ""
    deductors: list[Deductor] = field(default_factory=list)


@dataclass
class Challan:
    """A PART-C row: tax paid by the assessee himself, not deducted by
    anyone. These carry a BSR code + challan serial + date instead of a TAN,
    which is why the deductor scanner never saw them — and why every
    computation was missing its advance / self-assessment tax credit."""

    major_head: str = ""       # "0021" — income tax other than companies
    minor_head: str = ""       # "100" advance, "300" self-assessment, "400" …
    kind: str = "advance"      # advance | self_assessment | regular_assessment
    bsr_code: str = ""
    challan_serial: str = ""
    deposit_date: str = ""     # dd-mm-yyyy as printed
    amount_paise: int | None = None


@dataclass
class Form26AS:
    pan: str = ""
    name: str = ""
    financial_year: str = ""
    assessment_year: str = ""
    parts: list[Part] = field(default_factory=list)
    challans: list[Challan] = field(default_factory=list)


def document_to_dict(doc: Form26AS) -> dict:
    """JSON-safe dict for persistence."""
    return asdict(doc)


def document_from_dict(data: dict) -> Form26AS:
    return Form26AS(
        pan=data.get("pan", ""), name=data.get("name", ""),
        financial_year=data.get("financial_year", ""),
        assessment_year=data.get("assessment_year", ""),
        parts=[Part(
            part_id=p.get("part_id", ""), title=p.get("title", ""),
            deductors=[Deductor(
                **{k: v for k, v in dd.items() if k != "transactions"},
                transactions=[Txn(**tx) for tx in dd.get("transactions", [])])
                for dd in p.get("deductors", [])])
            for p in data.get("parts", [])],
        challans=[Challan(**c) for c in data.get("challans", [])])


def _paise(value) -> int | None:
    s = re.sub(r"[^\d.\-]", "", str(value or ""))
    if s in ("", "-", "."):
        return None
    try:
        return int((Decimal(s) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


def _cells(row) -> list[str]:
    return [(c or "").replace("\n", " ").strip() for c in row]


def _is_int(s: str) -> bool:
    return s.isdigit()


_YEAR_RE = re.compile(r"^\d{4}-\d{2,4}$")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


def _find_meta(tables) -> dict:
    """First valid value wins — 'Financial Year' etc. also appear in the
    legend/notes further down, so guard with format checks."""
    meta = {"pan": "", "name": "", "fy": "", "ay": ""}
    for t in tables:
        for row in t:
            cells = _cells(row)
            for i, c in enumerate(cells):
                low = c.lower()
                nxt = cells[i + 1] if i + 1 < len(cells) else ""
                if "permanent account number" in low and not meta["pan"] \
                        and _PAN_RE.match(nxt.strip()):
                    meta["pan"] = nxt.strip()
                elif low == "name of assessee" and not meta["name"]:
                    meta["name"] = nxt
                elif low == "financial year" and not meta["fy"] \
                        and _YEAR_RE.match(nxt):
                    meta["fy"] = nxt
                elif low == "assessment year" and not meta["ay"] \
                        and _YEAR_RE.match(nxt):
                    meta["ay"] = nxt
    return meta


def parse_26as_pdf(data: bytes) -> dict:
    """Parse an Annual Tax Statement PDF. Returns a dict with ``document``
    (:class:`Form26AS`), a flat importer-ready ``entries`` list, and
    ``skipped`` notes."""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        tables = [t for page in pdf.pages for t in page.extract_tables()]
        page_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        has_chars = any(page.chars for page in pdf.pages)
    if not has_chars and not page_text.strip():
        raise ValueError(
            "this PDF has no text layer — it looks flattened / 'printed to "
            "PDF' (its text is drawn as vector outlines) or scanned, so it "
            "cannot be read. Download the ORIGINAL Form 26AS from TRACES "
            "(the PAN-named file) rather than a re-saved copy.")
    if not tables:
        raise ValueError("no tables found in this PDF — if it is a scanned "
                         "image, 26AS OCR is not supported; download the "
                         "text-based 26AS from TRACES")

    meta = _find_meta(tables)
    doc = Form26AS(pan=meta["pan"], name=meta["name"],
                   financial_year=meta["fy"], assessment_year=meta["ay"])

    # Titles for the parts that appear, from the page text (tables drop them).
    part_titles: dict[str, str] = {}
    for m in _PART_RE.finditer(page_text):
        pid = m.group(1).upper()
        line = page_text[m.start():m.start() + 120].split("\n")[0]
        part_titles.setdefault(pid, line.strip())

    current_part: Part | None = None
    current_deductor: Deductor | None = None

    def ensure_part(pid: str) -> Part:
        nonlocal current_part
        for p in doc.parts:
            if p.part_id == pid:
                current_part = p
                return p
        p = Part(part_id=pid, title=part_titles.get(pid, f"PART-{pid}"))
        doc.parts.append(p)
        current_part = p
        return p

    for t in tables:
        for row in t:
            cells = _cells(row)
            joined = " ".join(cells)
            pm = _PART_RE.search(joined)
            if pm and not any(_TAN_RE.match(c) for c in cells):
                ensure_part(pm.group(1).upper())
                current_deductor = None
                continue
            if not any(cells):
                continue
            # header rows
            low0 = cells[0].lower()
            if low0 in ("sr. no.", "sr.no.", "sr no", "sr. no"):
                continue
            # PART-C: tax paid by the assessee (no TAN — BSR code instead).
            if (current_part is not None and current_part.part_id == "C"
                    and _is_int(cells[0])):
                challan = _challan_row(cells)
                if challan is not None:
                    doc.challans.append(challan)
                    continue
            tan_idx = next((i for i, c in enumerate(cells)
                            if _TAN_RE.match(c)), None)
            if tan_idx is not None and _is_int(cells[0]):
                if current_part is None:
                    ensure_part("I")
                amounts = [c for c in cells[tan_idx + 1:]
                           if _paise(c) is not None]
                name = " ".join(c for c in cells[1:tan_idx] if c).strip()
                current_deductor = Deductor(
                    name=name, tan=cells[tan_idx],
                    total_paid=amounts[0] if len(amounts) > 0 else "",
                    total_tax_deducted=amounts[1] if len(amounts) > 1 else "",
                    total_tds_deposited=amounts[-1] if amounts else "")
                current_part.deductors.append(current_deductor)
                continue
            # transaction row: int sr + section-like second cell
            if (current_deductor is not None and len(cells) >= 3
                    and _is_int(cells[0]) and _SECTION_RE.match(cells[1])):
                amounts = [c for c in cells if _paise(c) is not None]
                current_deductor.transactions.append(Txn(
                    sr=cells[0], section=cells[1],
                    txn_date=cells[2] if len(cells) > 2 else "",
                    status=cells[3] if len(cells) > 3 else "",
                    booking_date=cells[4] if len(cells) > 4 else "",
                    remarks=cells[5] if len(cells) > 5 else "",
                    amount_paid=amounts[-3] if len(amounts) >= 3 else "",
                    tax_deducted=amounts[-2] if len(amounts) >= 2 else "",
                    tds_deposited=amounts[-1] if amounts else ""))

    entries, skipped = _flat_entries(doc)
    if not doc.parts:
        raise ValueError("this PDF has tables but no 26AS parts were "
                         "recognised — is it really an Annual Tax Statement?")
    return {"document": doc, "entries": entries, "skipped": skipped}


# Minor head on the challan → what the payment actually was. 100 = advance
# tax, 300 = self-assessment tax, 400 = tax on regular assessment.
_MINOR_HEAD_KIND = {"100": "advance", "102": "advance",
                    "300": "self_assessment", "400": "regular_assessment"}
_BSR_RE = re.compile(r"^\d{7}$")
_DATE_RE = re.compile(r"^(\d{2})[-/](\d{2})[-/](\d{4})$")


def _challan_row(cells: list[str]) -> Challan | None:
    """A PART-C row reads: Sr | Major Head | Minor Head | Tax | Surcharge |
    Education Cess | Others | Total Tax | BSR Code | Date of Deposit |
    Challan Serial No. Column order shifts between TRACES releases, so the
    row is recognised by its shapes (7-digit BSR, dd-mm-yyyy date) rather
    than by position."""
    bsr = next((c for c in cells[1:] if _BSR_RE.match(c)), "")
    deposit = next((c for c in cells[1:] if _DATE_RE.match(c)), "")
    if not bsr or not deposit:
        return None
    amounts = [c for c in cells[1:] if _paise(c) is not None
               and c not in (bsr, deposit)]
    if not amounts:
        return None
    # Major/minor head are the leading 4- and 3-digit codes.
    major = next((c for c in cells[1:4] if re.fullmatch(r"0\d{3}", c)), "")
    minor = next((c for c in cells[1:5]
                  if re.fullmatch(r"\d{3}", c) and c != major), "")
    # Total tax is the largest of the money columns — the components
    # (tax / surcharge / cess / others) all roll into it.
    total = max(_paise(a) or 0 for a in amounts)
    return Challan(major_head=major, minor_head=minor,
                   kind=_MINOR_HEAD_KIND.get(minor, "advance"),
                   bsr_code=bsr, challan_serial=cells[-1].strip(),
                   deposit_date=deposit, amount_paise=total)


def challans_for_computation(doc: Form26AS) -> list[dict]:
    """PART-C challans in the shape the computation's `challans` editor and
    :class:`itrcore.tax.types.Challan` take: ISO date, whole rupees."""
    out: list[dict] = []
    for c in doc.challans:
        if not c.amount_paise or c.kind == "regular_assessment":
            continue                    # 400 is post-assessment, not a credit
        m = _DATE_RE.match(c.deposit_date)
        iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
        out.append({
            "kind": c.kind,
            "paid_on": iso,
            "amount": round(c.amount_paise / 100),
            "reference": f"{c.bsr_code} - {c.challan_serial}"})
    return out


def _predominant_section(d: Deductor) -> str:
    if not d.transactions:
        return ""
    counts: dict[str, int] = {}
    for tx in d.transactions:
        counts[tx.section] = counts.get(tx.section, 0) + 1
    return max(counts, key=counts.get)


def _flat_entries(doc: Form26AS) -> tuple[list[dict], list[str]]:
    """One row per deductor in Part I/II (TDS), shaped for the 26AS importer.
    TDS deposited (not tax deducted) is what the taxpayer can claim."""
    entries: list[dict] = []
    skipped: list[str] = []
    for part in doc.parts:
        if part.part_id not in ("I", "II"):
            continue                        # non-TDS parts: display only
        for d in part.deductors:
            tds = _paise(d.total_tds_deposited)
            if tds is None:
                skipped.append(f"{d.name or d.tan}: no TDS total")
                continue
            entries.append({
                "tan": d.tan,
                "deductor_name": d.name[:100],
                "section": _predominant_section(d),
                "amount_paid_paise": _paise(d.total_paid),
                "tds_paise": tds})
    return entries, skipped
