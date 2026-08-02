"""AIS / TIS workbook — the Genius-style multi-sheet Excel export.

One workbook mirroring a Genius AIS/TIS pull:

* ``Taxpayer Information (TIS)`` — category summary (processed / derived).
* ``TIS Annexure``            — the sources that make up each category.
* ``AIS-TDS-TCS``  (Part B1)  — each info block's summary + transaction detail.
* ``AIS-SFT``      (Part B2)
* ``AIS-Payment Of Tax`` (B3)
* ``AIS-Demand & Refund`` (B4)
* ``AIS-Other Info`` (Part B7)

Built from an :class:`itrcore.ingest.ais.AisDocument`; no Streamlit, no DB.
"""

import io
import re

import xlsxwriter

from itrcore.ingest.ais import AisDocument, AisRow

# Conditional Indian-lakh grouping — Excel collapses plain '#,##,##0.00'
# to western commas (see computation_xlsx.INR_FMT).
INR_FMT = ('[>=100000]#\\,##\\,##0.00;'
           '[<=-100000][Red]-#\\,##\\,##0.00;#,##0.00')
_ACCENT = "#1A5276"

_L2_HEADERS = ["Sr. No.", "Information Category", "Information Code",
               "Information Description", "Information Source", "Count",
               "Amount", "Information Category Code", "Derived Amount"]

# Part code → (sheet name, part title)
_PART_SHEETS = [
    ("B1", "AIS-TDS-TCS",
     "Part B1-Information relating to tax deducted or collected at source"),
    ("B2", "AIS-SFT",
     "Part B2-Information relating to specified financial transaction (SFT)"),
    ("B3", "AIS-Payment Of Tax",
     "Part B3-Information relating to payment of taxes"),
    ("B4", "AIS-Demand & Refund",
     "Part B4-Information relating to demand and refund"),
    ("B7", "AIS-Other Info",
     "Part B7-Any other information in relation to sub-rule (2) of rule "
     "114-I"),
]

_NUMERIC = re.compile(r"^-?\d[\d,]*\.?\d*$")


def _num(value):
    """Return a float when the cell is a formatted number, else None."""
    if value is None:
        return None
    s = str(value).strip()
    if not _NUMERIC.match(s):
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def build(doc: AisDocument, *, ay: str) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    f_key = wb.add_format({"bold": True})
    f_title = wb.add_format({"bold": True, "font_size": 13,
                             "font_color": _ACCENT})
    f_part = wb.add_format({"bold": True, "font_color": "white",
                            "bg_color": _ACCENT})
    f_head = wb.add_format({"bold": True, "bg_color": "#D6EAF8", "border": 1})
    f_sub = wb.add_format({"bold": True, "bg_color": "#EAF2F8", "border": 1,
                           "italic": True})
    f_cell = wb.add_format({"border": 1})
    f_num = wb.add_format({"border": 1, "num_format": INR_FMT})

    def meta(ws):
        ws.set_column(0, 0, 10)
        ws.set_column(1, 8, 24)
        ws.write(0, 0, "Name of Assessee", f_key)
        ws.write(0, 1, doc.name)
        ws.write(1, 0, "Assessee PAN", f_key)
        ws.write(1, 1, doc.pan)
        ws.write(2, 0, "Assessment Year", f_key)
        ws.write(2, 1, ay)

    def write_row(ws, r, values, fmt_txt, fmt_num):
        for c, v in enumerate(values):
            n = _num(v)
            if n is not None and c > 0:
                ws.write_number(r, c, n, fmt_num)
            else:
                ws.write(r, c, "" if v is None else str(v), fmt_txt)

    # ---- TIS summary ----
    ws = wb.add_worksheet("Taxpayer Information (TIS)")
    meta(ws)
    ws.write(3, 0, "Taxpayer Information Summary (TIS)", f_title)
    ws.write_row(4, 0, ["SR. NO.", "INFORMATION CATEGORY", "PROCESSED VALUE",
                        "DERIVED VALUE"], f_head)
    r = 5
    for row in doc.tis_summary():
        ws.write(r, 0, row["sr"], f_cell)
        ws.write(r, 1, row["category"], f_cell)
        ws.write_number(r, 2, row["processed_paise"] / 100, f_num)
        ws.write_number(r, 3, row["derived_paise"] / 100, f_num)
        r += 1

    # ---- TIS Annexure (sources behind each category) ----
    wa = wb.add_worksheet("TIS Annexure")
    meta(wa)
    wa.write(3, 0, "Annexure to Taxpayer Information Summary (TIS)", f_title)
    r = 4
    by_cat: dict[str, list[AisRow]] = {}
    for row in doc.rows:
        by_cat.setdefault(row.category, []).append(row)
    for cat in sorted(by_cat):
        wa.write_row(r, 0, ["INFORMATION CATEGORY", cat], f_head)
        r += 1
        wa.write_row(r, 0, ["SR.", "PART", "INFORMATION DESCRIPTION",
                            "INFORMATION SOURCE", "INFO CODE", "REPORTED /"
                            " AMOUNT", "DERIVED"], f_sub)
        r += 1
        for i, row in enumerate(by_cat[cat], start=1):
            wa.write(r, 0, i, f_cell)
            wa.write(r, 1, row.part, f_cell)
            wa.write(r, 2, row.description, f_cell)
            wa.write(r, 3, row.source, f_cell)
            wa.write(r, 4, row.info_code, f_cell)
            wa.write_number(r, 5, row.amount_paise / 100, f_num)
            wa.write_number(r, 6, (row.derived_paise or row.amount_paise)
                            / 100, f_num)
            r += 1
        r += 1

    # ---- AIS part sheets (summary + transaction detail per block) ----
    for part, sheet_name, part_title in _PART_SHEETS:
        wp = wb.add_worksheet(sheet_name)
        meta(wp)
        wp.write(3, 0, "Annual Information Statement (AIS)", f_title)
        wp.merge_range(4, 0, 4, 8, part_title, f_part)
        r = 5
        # B3 carries challans, not information blocks (see AisDocument.
        # tax_payments) — it has no l2/l1 shape to lay out.
        if part == "B3":
            if not doc.tax_payments:
                wp.write(r, 0, "No tax payment reported under this part.",
                         f_cell)
                continue
            wp.write_row(r, 0, ["Sr.", "Financial year", "Major head",
                                "Minor head", "Amount", "BSR code",
                                "Date of deposit", "Challan serial",
                                "Challan identification no."], f_head)
            r += 1
            for i, p in enumerate(doc.tax_payments, start=1):
                write_row(wp, r, [i, p.fy, p.major_head, p.minor_head,
                                  p.amount_paise / 100, p.bsr_code,
                                  p.deposit_date, p.challan_serial, p.cin],
                          f_cell, f_num)
                r += 1
            continue
        blocks = doc.rows_for_part(part)
        if not blocks:
            wp.write(r, 0, "No information reported under this part.", f_cell)
            continue
        for i, row in enumerate(blocks, start=1):
            wp.write_row(r, 0, _L2_HEADERS, f_head)
            r += 1
            derived = ("" if row.derived_paise is None
                       else row.derived_paise / 100)
            write_row(wp, r, [i, row.category, row.info_code, row.description,
                              row.source, row.count, row.amount_paise / 100,
                              row.category_code, derived], f_cell, f_num)
            r += 1
            if row.detail.labels:
                wp.write_row(r, 0, ["Sr."] + list(row.detail.labels), f_sub)
                r += 1
                for j, drow in enumerate(row.detail.rows, start=1):
                    write_row(wp, r, [j, *drow], f_cell, f_num)
                    r += 1
            r += 1

    wb.close()
    return buf.getvalue()
