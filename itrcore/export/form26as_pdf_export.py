"""Regenerate a clean, readable Form 26AS PDF from parsed data.

Takes the :class:`itrcore.ingest.form26as_pdf.Form26AS` structure and lays it
out part-wise — a deductor summary line then its transactions — the way a
Genius 26AS print looks. fpdf2 core fonts (latin-1), amounts right-aligned.
"""

from fpdf import FPDF

from itrcore.export.format import inr_group
from itrcore.ingest.form26as_pdf import Form26AS

_LATIN1 = str.maketrans({"—": "-", "–": "-", "₹": "Rs.", "’": "'",
                         "‘": "'", "“": '"', "”": '"', "…": "...", "#": ""})


def _t(text: str) -> str:
    return (str(text).translate(_LATIN1)
            .encode("latin-1", "replace").decode("latin-1"))


def _amt(raw: str) -> str:
    s = str(raw or "").replace(",", "").strip()
    try:
        return inr_group(round(float(s))) if s not in ("", "-") else "-"
    except ValueError:
        return _t(raw)


class _Doc(FPDF):
    def __init__(self, name: str, pan: str, ay: str) -> None:
        super().__init__(orientation="L", unit="mm", format="A4")
        self._name, self._pan, self._ay = name, pan, ay
        self.set_auto_page_break(auto=True, margin=14)

    def header(self) -> None:
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 7, "Form 26AS - Annual Tax Statement", align="C",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, _t(f"{self._name}   |   PAN {self._pan}   |   "
                           f"AY {self._ay}"), align="C",
                  new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.cell(0, 5, _t(f"Page {self.page_no()} - regenerated from TRACES "
                           "26AS; verify against the source before filing."),
                  align="C")


# widths (mm) for the transaction grid — sums to ~269 (A4 landscape usable)
_COLS = [(12, "Sr"), (20, "Section"), (30, "Txn Date"), (16, "Status"),
         (30, "Booking Date"), (18, "Remarks"), (48, "Amount Paid"),
         (45, "Tax Deducted"), (45, "TDS Deposited")]


def build(doc: Form26AS, *, ay: str | None = None) -> bytes:
    ay = ay or doc.assessment_year or ""
    pdf = _Doc(doc.name, doc.pan, ay)
    pdf.add_page()

    for part in doc.parts:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(26, 82, 118)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 6, _t(part.title or f"PART-{part.part_id}"),
                 border=0, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        if not part.deductors:
            pdf.set_font("Helvetica", "I", 8)
            pdf.cell(0, 5, "No transactions present.",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            continue

        for d in part.deductors:
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_fill_color(234, 242, 248)
            pdf.cell(0, 5, _t(f"{d.name}  ({d.tan})    "
                              f"Paid: {_amt(d.total_paid)}    "
                              f"Tax Deducted: {_amt(d.total_tax_deducted)}    "
                              f"TDS Deposited: {_amt(d.total_tds_deposited)}"),
                     border="B", fill=True, new_x="LMARGIN", new_y="NEXT")
            if d.transactions:
                pdf.set_font("Helvetica", "B", 7)
                for w, label in _COLS:
                    pdf.cell(w, 4.5, label, border=1, align="C")
                pdf.ln()
                pdf.set_font("Helvetica", "", 7)
                for tx in d.transactions:
                    vals = [tx.sr, _t(tx.section), _t(tx.txn_date),
                            _t(tx.status), _t(tx.booking_date),
                            _t(tx.remarks), _amt(tx.amount_paid),
                            _amt(tx.tax_deducted), _amt(tx.tds_deposited)]
                    for idx, ((w, _label), v) in enumerate(zip(_COLS, vals)):
                        pdf.cell(w, 4.5, v, border=1,
                                 align="R" if idx >= 6 else "L")
                    pdf.ln()
            pdf.ln(1.5)

    return bytes(pdf.output())
