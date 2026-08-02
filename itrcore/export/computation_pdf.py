"""PDF computation sheet (fpdf2, core fonts — amounts as 'Rs.')."""

from fpdf import FPDF

from itrcore.export.format import rs

_LATIN1_MAP = str.maketrans({"—": "-", "–": "-", "₹": "Rs.", "’": "'",
                             "‘": "'", "“": '"', "”": '"', "…": "..."})


def _t(text: str) -> str:
    """Core PDF fonts are latin-1 only; transliterate what we can."""
    return (text.translate(_LATIN1_MAP)
            .encode("latin-1", "replace").decode("latin-1"))


class _Sheet(FPDF):
    def __init__(self, title: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc_title = title
        self.set_auto_page_break(auto=True, margin=18)

    def header(self) -> None:
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, _t(self.doc_title), align="C",
                  new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 6, _t(f"Page {self.page_no()} - subject to CA review "
                           "before filing"), align="C")

    def kv_row(self, label: str, a: str, b: str, bold: bool = False) -> None:
        self.set_font("Helvetica", "B" if bold else "", 9)
        self.cell(96, 6, _t(label), border=1)
        self.cell(43, 6, a, border=1, align="R")
        self.cell(43, 6, b, border=1, align="R",
                  new_x="LMARGIN", new_y="NEXT")


def build(*, taxpayer_name: str, pan_masked: str, ay: str,
          result: dict) -> bytes:
    pdf = _Sheet(f"Computation of Income & Tax — AY {ay}")
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _t(f"{taxpayer_name}   |   PAN {pan_masked}   |   "
                   f"Recommended regime: {result['recommended'].upper()}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(96, 6, "Particulars", border=1)
    pdf.cell(43, 6, "Old regime", border=1, align="R")
    pdf.cell(43, 6, "New regime", border=1, align="R",
             new_x="LMARGIN", new_y="NEXT")

    old, new = result["old"], result["new"]
    rows = [
        ("Gross total income", old["gti"], new["gti"], False),
        ("Chapter VI-A deductions", -old["deductions_total"],
         -new["deductions_total"], False),
        ("Total income (Sec 288A)", old["total_income"],
         new["total_income"], True),
        ("Exempt income - Schedule EI (not included above)",
         old.get("exempt_total", 0), new.get("exempt_total", 0), False),
        ("Tax at slab rates", old["slab_tax"], new["slab_tax"], False),
        ("Tax on special-rate income", old["special_tax"],
         new["special_tax"], False),
        ("Rebate u/s 87A", -old["rebate_87a"], -new["rebate_87a"], False),
        ("Surcharge (net)", old["surcharge"] - old["marginal_relief"],
         new["surcharge"] - new["marginal_relief"], False),
        ("Health & education cess", old["cess"], new["cess"], False),
        ("Total tax liability", old["tax_liability"],
         new["tax_liability"], True),
        ("TDS / taxes paid",
         -(old["tds_total"] + old["advance_tax_paid"]
           + old["self_assessment_paid"]),
         -(new["tds_total"] + new["advance_tax_paid"]
           + new["self_assessment_paid"]), False),
        ("Interest 234A/B/C + fee 234F",
         old["interest_234a"] + old["interest_234b"]
         + old["interest_234c"] + old["fee_234f"],
         new["interest_234a"] + new["interest_234b"]
         + new["interest_234c"] + new["fee_234f"], False),
        ("Net payable / (refund)", old["payable_or_refund"],
         new["payable_or_refund"], True),
    ]
    for label, a, b, bold in rows:
        pdf.kv_row(label, rs(a), rs(b), bold=bold)

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, _t(f"Detailed schedule - {result['recommended']} regime"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    regime = result[result["recommended"]]
    for head_key in ("salary", "house_property", "business", "speculation",
                     "capital_gains", "other_sources"):
        head = regime["heads"].get(head_key) or {}
        for ln in head.get("lines", []):
            label = f"[{ln['section']}] {ln['label']}"
            pdf.cell(140, 5, _t(label[:88]), border=0)
            pdf.cell(42, 5, rs(ln["amount"]), border=0, align="R",
                     new_x="LMARGIN", new_y="NEXT")
    for ln in (list(regime.get("setoff_lines", []))
               + list(regime.get("deduction_lines", []))
               + list(regime.get("special_tax_lines", []))
               + list(regime.get("lines", []))):
        pdf.cell(140, 5, _t(f"[{ln['section']}] {ln['label']}"[:88]), border=0)
        pdf.cell(42, 5, rs(ln["amount"]), border=0, align="R",
                 new_x="LMARGIN", new_y="NEXT")

    exempt_lines = regime.get("exempt_lines", [])
    if exempt_lines:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, _t("Exempt income - Schedule EI (reported; not part "
                          "of total income)"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        for ln in exempt_lines:
            pdf.cell(140, 5, _t(f"[{ln['section']}] {ln['label']}"[:88]),
                     border=0)
            pdf.cell(42, 5, rs(ln["amount"]), border=0, align="R",
                     new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, _t("Computation of tax - slab rates & special rates"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    for ln in regime.get("slab_lines", []):
        pdf.cell(140, 5, _t(f"[{ln['section']}] {ln['label']}"[:88]),
                 border=0)
        pdf.cell(42, 5, rs(ln["amount"]), border=0, align="R",
                 new_x="LMARGIN", new_y="NEXT")
    for label, amount in (
            ("Tax at slab rates on normal income", regime["slab_tax"]),
            ("Tax on special-rate income", regime["special_tax"]),
            ("Less: rebate u/s 87A", -regime["rebate_87a"]),
            ("Surcharge (net of marginal relief)",
             regime["surcharge"] - regime["marginal_relief"]),
            ("Health & education cess", regime["cess"]),
            ("Total tax liability", regime["tax_liability"])):
        pdf.cell(140, 5, _t(label), border=0)
        pdf.cell(42, 5, rs(amount), border=0, align="R",
                 new_x="LMARGIN", new_y="NEXT")

    out = pdf.output()
    return bytes(out)
