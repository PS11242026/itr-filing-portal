"""Capital-gains Excel import — Genius "Share Detail" template format.

``build_template()`` reproduces the Genius capital-gain share template
(the 19-column layout of ``Capital Gain Template Genius.xls``: Listed/
Unlisted, Category, Long-Term/Short-Term, Name of Company, Sale Amount,
unquoted-share FMV (50CA), sale/purchase dates, cost, 55(2)(ac) FMV,
transfer expenses, STT, 94(7)/(8) and the per-unit 112A columns) with the
same dropdown validations, header shading, column widths and cell number
formats. ``parse_cg_excel()`` reads a filled workbook back — either this
Genius layout (.xlsx or the original Genius .xls) or the older
standardized Sec-48 template — into the exact row shape the Computation
page stores; ``classify_rows()`` gives the short-term / long-term subhead
preview using the SAME holding-period rule the tax engine applies
(Sec 2(42A) periods from the AY config).
"""

import io
from datetime import date, datetime, timedelta

from itrcore.tax.ayconfig import load as load_cfg
# Single source of truth for the ST/LT boundary — the engine's own test.
from itrcore.tax.capital_gains import _is_long_term
from itrcore.tax.types import CGEntry

ASSET_LABELS = {
    "listed_equity_stt": "Listed equity share (STT paid)",
    "equity_mf": "Equity-oriented mutual fund",
    "listed_other": "Listed bond / debenture",
    "property": "Immovable property (land/building)",
    "unlisted": "Unlisted share",
    "other": "Other capital asset",
}
_LABEL_TO_CODE = {v.casefold(): k for k, v in ASSET_LABELS.items()}

# ------------------------------------------------- Genius template layout --
# Column order, header text, dropdowns, widths and number formats are taken
# verbatim from "Capital Gain Template Genius.xls" (BIFF-dumped) so a staff
# member can fill either file interchangeably.
_G_HEADERS = (
    "Listed/ Unlisted",
    "Category\n(Only If A.Y. >= 2015-16)",
    "Long-Term/ Short-Term",
    "Name of Company",
    "Sale Amount",
    "Whether Shares of a Company other than Quoted Shares\n"
    "(Only If A.Y. >= 2018-19)",
    "Fair Market value of unquoted shares\n"
    "(Only If A.Y. >= 2018-19 & 'Yes' in Col. F)",
    "Date of Sale (dd/mm/yyyy)",
    "Purchase Cost",
    "Fair Market value on Jan 31, 2018 as per section 55(2)(ac)\n"
    "(Only If A.Y. >= 2019-20 & Applicable u/s 112A)",
    "Date of Purchase (dd/mm/yyyy)",
    "Transfer Expenses",
    "STT Paid\n(Enter if Listed)",
    "Bond/Debenture (other than capital indexed)\n"
    "(Enter if Listed Long-Term)",
    "Amount received u/s 94(7) or 94(8)\n(Enter if Short-Term)",
    "ISIN Code\n(Only If A.Y. >= 2019-20 & Applicable u/s 112A)",
    "No. of Shares/Units\n(Only If A.Y. >= 2019-20 & Applicable u/s 112A)",
    "Sale-price per Share/Unit\n"
    "(Only If A.Y. >= 2019-20 & Applicable u/s 112A)",
    "Fair Market Value per share/unit as on 31st January,2018\n"
    "(Only If A.Y. >= 2019-20 & Applicable u/s 112A)",
)
GENIUS_CATEGORIES = (
    "Equity Shares", "Preference Shares",
    "Equity Oriented Mutual Fund Units", "Debt Oriented Mutual Fund Units",
    "Debentures/Bonds", "Units of UTI", "Units of Business Trust",
    "Zero Coupon Bonds", "Government Securities/ Other Mutual Fund",
)
# BIFF column widths / 256 → Excel character widths.
_G_WIDTHS = (8.71, 24.86, 12.14, 24.71, 15.29, 25.43, 25.00, 13.71, 12.71,
             25.00, 14.00, 10.57, 8.29, 16.14, 14.71, 14.71, 14.71, 14.71,
             14.71)
# Data-cell number formats per column ('@' = text) — as in the Genius file.
_G_NUMFMT = {4: "0.0000", 6: "0.00", 8: "0.00", 9: "0.0000", 11: "0.00",
             14: "0.00", 16: "0.00", 17: "0.0000", 18: "0.0000"}

# Header keywords that recognise each Genius column even if reworded a bit.
_GENIUS_KEYS = {
    "listed_unlisted": ("listed/ unlisted", "listed/unlisted"),
    "category": ("category",),
    "term": ("long-term/ short-term", "long-term/short-term",
             "long term/ short term"),
    "name": ("name of company", "name of share", "name of scrip"),
    "sale_amount": ("sale amount",),
    "unquoted": ("other than quoted",),
    "fmv_unquoted": ("value of unquoted",),
    "sale_date": ("date of sale",),
    "purchase_cost": ("purchase cost",),
    "fmv_2018": ("jan 31, 2018", "55(2)(ac)"),
    "purchase_date": ("date of purchase",),
    "transfer_expenses": ("transfer expenses",),
    "stt": ("stt paid",),
    "bond": ("bond/debenture",),
    "s94": ("94(7)",),
    "isin": ("isin",),
    "units": ("no. of shares",),
    "price_unit": ("sale-price per", "sale price per"),
    "fmv_unit": ("value per share",),
}

# ------------------------------------------------ legacy template (Sec 48) --
_HEADERS = (
    ("asset_class", "Asset class"),
    ("description", "Description of asset"),
    ("acquisition_date", "Date of acquisition"),
    ("transfer_date", "Date of transfer (sale)"),
    ("full_value_consideration", "Full value of consideration (Sec 48)"),
    ("stamp_duty_value", "Stamp-duty value (Sec 50C — property only)"),
    ("cost_of_acquisition", "Cost of acquisition"),
    ("cost_of_improvement", "Cost of improvement"),
    ("transfer_expenses", "Expenditure on transfer"),
    ("fmv_31jan2018", "FMV as on 31-01-2018 (Sec 55(2)(ac))"),
    ("exemption_section", "Exemption section (54 / 54EC / 54F)"),
    ("exemption_claimed", "Exemption amount claimed"),
)
# Keywords that recognise each legacy column even if staff reword a header.
_HEADER_KEYS = {
    "asset_class": ("asset",),
    "description": ("description", "particular"),
    "acquisition_date": ("acquisition", "purchase date", "acquired"),
    "transfer_date": ("transfer", "sale", "sold"),
    "full_value_consideration": ("consideration", "sale value",
                                 "full value"),
    "stamp_duty_value": ("stamp", "50c", "sdv"),
    "cost_of_acquisition": ("cost of acquisition", "purchase cost"),
    "cost_of_improvement": ("improvement",),
    "transfer_expenses": ("expen",),
    "fmv_31jan2018": ("fmv", "31-01-2018", "31.01.2018", "grandfather"),
    "exemption_section": ("exemption section", "section claimed"),
    "exemption_claimed": ("exemption amount", "amount claimed",
                          "exemption claimed"),
}
_DATE_FIELDS = ("acquisition_date", "transfer_date")
_OPTIONAL_INT_FIELDS = ("stamp_duty_value", "fmv_31jan2018")
_INT_FIELDS = ("full_value_consideration", "cost_of_acquisition",
               "cost_of_improvement", "transfer_expenses",
               "exemption_claimed")


# prefill-row key → template column index (build_template writes these).
_PREFILL_COLS = {"listed": 0, "category": 1, "term": 2, "name": 3,
                 "sale_amount": 4, "sale_date": 7, "purchase_cost": 8,
                 "fmv_2018": 9, "purchase_date": 10,
                 "transfer_expenses": 11, "stt": 12, "isin": 15,
                 "units": 16, "price_unit": 17, "fmv_unit": 18}


def build_template(prefill: list[dict] | None = None) -> bytes:
    """The Genius-format Share Detail workbook staff fill and re-upload
    (one security transfer per row; property rows are entered directly on
    the Computation page). ``prefill`` rows (from :func:`prefill_from_ais`)
    are written under the headers so the AIS's own sale legs arrive
    pre-populated — staff complete the purchase dates and verify costs."""
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    f_head = wb.add_format({"bold": True, "font_name": "Arial",
                            "font_size": 10, "bg_color": "#C0C0C0",
                            "border": 1, "text_wrap": True,
                            "align": "center", "valign": "vcenter"})
    fmt_of = {code: wb.add_format({"font_name": "Arial", "font_size": 10,
                                   "num_format": code})
              for code in ("@", "0.00", "0.0000")}

    ws = wb.add_worksheet("Sheet1")          # named as in the Genius file
    ws.set_row(0, 114.75)                    # 2295 twips
    for col, title in enumerate(_G_HEADERS):
        ws.write(0, col, title, f_head)
        ws.set_column(col, col, _G_WIDTHS[col],
                      fmt_of[_G_NUMFMT.get(col, "@")])
    for r, row in enumerate(prefill or [], start=1):
        for field, col in _PREFILL_COLS.items():
            v = row.get(field)
            if v in (None, ""):
                continue
            if isinstance(v, (int, float)):
                ws.write_number(r, col, v,
                                fmt_of[_G_NUMFMT.get(col, "@")])
            else:
                ws.write(r, col, str(v), fmt_of[_G_NUMFMT.get(col, "@")])
    last = 65535                             # same reach as the Genius DVs
    ws.data_validation(1, 0, last, 0, {
        "validate": "list", "source": ["Listed", "Unlisted"]})
    ws.data_validation(1, 1, last, 1, {
        "validate": "list", "source": list(GENIUS_CATEGORIES)})
    ws.data_validation(1, 2, last, 2, {
        "validate": "list", "source": ["Long-Term", "Short-Term"]})
    for c in (5, 12, 13):                    # F, M, N — Yes/No
        ws.data_validation(1, c, last, c, {
            "validate": "list", "source": ["Yes", "No"]})
    wb.close()
    return buf.getvalue()


# ---------------------------------------- prefill from the AIS SFT detail --
# Depository / RTA sale-of-securities blocks (SFT-17/18) carry these
# transaction-level columns; matched by needle so label variations parse.
_AIS_DETAIL_KEYS = {
    "sale_date": ("date of sale",),
    "name": ("security name",),
    "sec_class": ("security class",),
    "asset_type": ("asset type",),
    "quantity": ("quantity",),
    "price_unit": ("sale price per",),
    "sale_amount": ("sales consideration", "sale consideration"),
    "purchase_cost": ("cost of acquisition",),
    "fmv_unit": ("unit fmv",),
    "fmv_2018": ("fair market value",),
    "stt": ("stt",),
    "debit_type": ("debit type",),
}
_SEC_CLASS_CATEGORY = (
    ("equity oriented", "Equity Oriented Mutual Fund Units"),
    ("debt oriented", "Debt Oriented Mutual Fund Units"),
    ("preference", "Preference Shares"),
    ("equity share", "Equity Shares"),
    ("zero coupon", "Zero Coupon Bonds"),
    ("debenture", "Debentures/Bonds"),
    ("business trust", "Units of Business Trust"),
    ("uti", "Units of UTI"),
    ("government", "Government Securities/ Other Mutual Fund"),
    ("bond", "Debentures/Bonds"),
)


def _detail_index(labels) -> dict[str, int]:
    """Label list → {field: column index}, needle-matched. 'indexed cost of
    acquisition' must not shadow 'cost of acquisition', and 'unit fmv' must
    win over the broader 'fair market value'."""
    lowered = [str(label).casefold() for label in labels]
    out: dict[str, int] = {}
    for field, needles in _AIS_DETAIL_KEYS.items():
        for i, text in enumerate(lowered):
            if i in out.values():
                continue
            if field == "purchase_cost" and "indexed" in text:
                continue
            if field == "fmv_2018" and "unit" in text:
                continue
            if field == "stt" and text != "stt":
                continue
            if any(n in text for n in needles):
                out[field] = i
                break
    return out


def prefill_from_ais(doc) -> list[dict]:
    """AIS sale-of-securities L1 detail → Genius-template prefill rows.
    Everything the AIS reports is filled (sale leg, LT/ST, quantity,
    price, depository-reported cost, FMV, STT); Date of Purchase stays
    blank for staff — the AIS does not carry it."""
    rows: list[dict] = []
    for block in getattr(doc, "rows", ()):
        if block.bucket != "sale_securities" or not block.detail.labels:
            continue
        idx = _detail_index(block.detail.labels)
        if "sale_amount" not in idx and "price_unit" not in idx:
            continue

        def _cell(drow, field):
            i = idx.get(field)
            return drow[i] if i is not None and i < len(drow) else None

        for drow in block.detail.rows:
            sale = _to_float(_cell(drow, "sale_amount"))
            units = _to_float(_cell(drow, "quantity"))
            price = _to_float(_cell(drow, "price_unit"))
            if not sale and not (units and price):
                continue
            sec_class = _cell_str(_cell(drow, "sec_class")).casefold()
            category = next((cat for needle, cat in _SEC_CLASS_CATEGORY
                             if needle in sec_class), "")
            asset_type = _cell_str(_cell(drow, "asset_type")).casefold()
            stt_paid = _to_float(_cell(drow, "stt"))
            debit = _cell_str(_cell(drow, "debit_type")).casefold()
            if stt_paid > 0 or ("market" in debit and "off" not in debit) \
                    or "amc" in debit:
                stt = "Yes"
            elif "off" in debit:
                stt = "No"
            else:
                stt = ""
            cost = _to_float(_cell(drow, "purchase_cost"))
            fmv_unit = _to_float(_cell(drow, "fmv_unit"))
            fmv = _to_float(_cell(drow, "fmv_2018"))
            rows.append({
                "listed": "Unlisted" if "unlisted" in sec_class
                          else "Listed",
                "category": category,
                "term": ("Long-Term" if asset_type.startswith("long")
                         else "Short-Term"
                         if asset_type.startswith("short") else ""),
                "name": _cell_str(_cell(drow, "name")),
                "sale_amount": sale or round(units * price, 2),
                "sale_date": _cell_str(_cell(drow, "sale_date")),
                "purchase_cost": cost or None,
                "fmv_2018": fmv or None,
                "stt": stt,
                "units": units or None,
                "price_unit": price or None,
                "fmv_unit": fmv_unit or None,
            })
    return rows


# ------------------------------------------------------------ cell helpers --
def _cell_str(v) -> str:
    return str(v).strip() if v is not None else ""


def _to_date(v, *, row: int, label: str, errors: list[str]):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) and 20000 < float(v) < 60000:
        # Excel serial date (1900 system) — .xls text columns filled with
        # a real date cell land here.
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    s = str(v).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    errors.append(f"row {row}: could not read {label} '{s}' — use "
                  "dd/mm/yyyy")
    return None


def _to_int(v, *, row: int, label: str, errors: list[str]) -> int:
    if v in (None, ""):
        return 0
    if isinstance(v, (int, float)):
        return int(round(v))
    s = str(v).replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(round(float(s)))
    except ValueError:
        errors.append(f"row {row}: '{v}' is not a number for {label}")
        return 0


def _to_float(v) -> float:
    """Best-effort float for the per-unit 112A columns (0 when unreadable)."""
    if v in (None, ""):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _asset_code(v, *, row: int, errors: list[str]) -> str:
    s = _cell_str(v)
    if not s:
        errors.append(f"row {row}: asset class is blank")
        return "other"
    key = s.casefold()
    if key in ASSET_LABELS:
        return key
    if key in _LABEL_TO_CODE:
        return _LABEL_TO_CODE[key]
    for code, label in ASSET_LABELS.items():
        if key in label.casefold() or label.casefold() in key:
            return code
    errors.append(f"row {row}: unknown asset class '{s}' — treated as "
                  "'Other capital asset'")
    return "other"


# ------------------------------------------------------------ sheet loading --
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _load_sheets(data: bytes) -> list[tuple[str, list[list]]]:
    """Workbook bytes → [(sheet name, rows-of-values)] for .xlsx (openpyxl)
    and the original Genius .xls (xlrd), dates converted to datetimes."""
    if data[:8] == _OLE_MAGIC:
        import xlrd
        try:
            wb = xlrd.open_workbook(file_contents=data)
        except Exception as exc:
            raise ValueError(f"could not open the Excel file: {exc}") from exc
        out = []
        for ws in wb.sheets():
            rows = []
            for r in range(ws.nrows):
                vals = []
                for c in range(ws.ncols):
                    cell = ws.cell(r, c)
                    v = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        v = xlrd.xldate.xldate_as_datetime(v, wb.datemode)
                    elif cell.ctype == xlrd.XL_CELL_EMPTY or v == "":
                        v = None
                    vals.append(v)
                rows.append(vals)
            out.append((ws.name, rows))
        return out
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True,
                                    read_only=True)
    except Exception as exc:
        raise ValueError(f"could not open the Excel file: {exc}") from exc
    return [(name, [list(row)
                    for row in wb[name].iter_rows(values_only=True)])
            for name in wb.sheetnames]


def _ordered(sheets: list[tuple[str, list[list]]]) -> list[list[list]]:
    """CG-looking sheets first, then the rest in workbook order."""
    preferred = [rows for name, rows in sheets
                 if any(k in name.casefold()
                        for k in ("capital", "cg", "share"))]
    rest = [rows for name, rows in sheets
            if not any(k in name.casefold()
                       for k in ("capital", "cg", "share"))]
    return preferred + rest


# --------------------------------------------------- Genius header mapping --
def _map_genius(cells: list) -> dict[int, str] | None:
    lowered = [_cell_str(c).casefold().replace("\n", " ") for c in cells]
    mapping: dict[int, str] = {}
    for idx, text in enumerate(lowered):
        if not text:
            continue
        for field, keys in _GENIUS_KEYS.items():
            if field in mapping.values():
                continue
            if any(k in text for k in keys):
                mapping[idx] = field
                break
    have = set(mapping.values())
    if {"sale_amount", "sale_date"} <= have and \
            ("listed_unlisted" in have or "category" in have):
        return mapping
    return None


_G_CATEGORY_KEYS = (
    ("equity_mf_units", ("equity oriented",)),
    ("debt_mf_units", ("debt oriented",)),
    ("preference", ("preference",)),
    ("equity_shares", ("equity",)),
    ("zero_coupon", ("zero coupon",)),
    ("debenture_bond", ("debenture", "bond")),
    ("business_trust", ("business trust",)),
    ("uti", ("uti",)),
    ("govt_other_mf", ("government", "other mutual")),
)


def _genius_asset_class(category_text: str, listed: bool, stt_yes: bool,
                        *, row: int, errors: list[str]) -> str:
    """(Category, Listed/Unlisted, STT) → engine asset class. Drives the
    Sec 2(42A) holding period and the 111A/112/112A rate bucket."""
    t = _cell_str(category_text).casefold()
    cat = next((key for key, needles in _G_CATEGORY_KEYS
                if any(n in t for n in needles)), None)
    if cat is None:
        if t:
            errors.append(f"row {row}: unknown category '{category_text}' "
                          "— treated as 'Other capital asset'")
        else:
            errors.append(f"row {row}: category is blank — treated as "
                          "'Other capital asset'")
        return "other"
    if cat == "equity_mf_units":
        if listed and not stt_yes:
            errors.append(f"row {row}: equity-oriented MF without STT — "
                          "confirm; classified as equity MF (111A/112A) "
                          "anyway")
        return "equity_mf"
    if cat == "equity_shares":
        if not listed:
            return "unlisted"
        if not stt_yes:
            errors.append(f"row {row}: listed equity without STT — taxed "
                          "u/s 112 (not 111A/112A)")
            return "listed_other"
        return "listed_equity_stt"
    if cat == "business_trust":
        # 111A/112A cover STT-paid units of a business trust.
        return "listed_equity_stt" if stt_yes else (
            "listed_other" if listed else "other")
    if cat == "uti":
        return "equity_mf" if stt_yes else (
            "listed_other" if listed else "other")
    if cat == "preference":
        return "listed_other" if listed else "unlisted"
    if cat in ("debenture_bond", "zero_coupon", "govt_other_mf"):
        return "listed_other" if listed else "other"
    return "other"                           # debt_mf_units and fallthrough


def _parse_genius(mapping: dict[int, str], rows: list[list],
                  header_at: int, ay: str | None) -> dict:
    out_rows: list[dict] = []
    errors: list[str] = []
    holding = (load_cfg(ay).raw["capital_gains"]["holding_months"]
               if ay else None)
    for row_no, cells in enumerate(rows[header_at + 1:],
                                   start=header_at + 2):
        values = {field: (cells[idx] if idx < len(cells) else None)
                  for idx, field in mapping.items()}
        if all(v in (None, "") for v in values.values()):
            continue

        listed_txt = _cell_str(values.get("listed_unlisted")).casefold()
        stt_yes = _cell_str(values.get("stt")).casefold().startswith("y")
        if listed_txt.startswith("unlisted"):
            listed = False
        elif listed_txt.startswith("listed"):
            listed = True
        else:
            listed = stt_yes         # STT paid ⇒ on-market ⇒ listed
            errors.append(f"row {row_no}: Listed/Unlisted blank — assumed "
                          f"{'Listed' if listed else 'Unlisted'}")

        asset_class = _genius_asset_class(
            values.get("category"), listed, stt_yes,
            row=row_no, errors=errors)

        units = _to_float(values.get("units"))
        sale = _to_int(values.get("sale_amount"), row=row_no,
                       label="sale amount", errors=errors)
        if not sale and units:
            per = _to_float(values.get("price_unit"))
            if per:
                sale = int(round(units * per))
        # Sec 50CA: for unquoted shares the FMV is the deemed consideration
        # when it exceeds the actual sale price.
        if _cell_str(values.get("unquoted")).casefold().startswith("y"):
            fmv_unq = _to_int(values.get("fmv_unquoted"), row=row_no,
                              label="FMV of unquoted shares", errors=errors)
            if fmv_unq > sale:
                errors.append(f"row {row_no}: Sec 50CA — FMV of unquoted "
                              f"shares ₹{fmv_unq:,} adopted as full value "
                              f"of consideration (sale ₹{sale:,})")
                sale = fmv_unq

        fmv18 = _to_int(values.get("fmv_2018"), row=row_no,
                        label="FMV on 31-01-2018", errors=errors)
        if not fmv18 and units:
            per_fmv = _to_float(values.get("fmv_unit"))
            if per_fmv:
                fmv18 = int(round(units * per_fmv))

        s94 = _to_int(values.get("s94"), row=row_no,
                      label="amount u/s 94(7)/94(8)", errors=errors)
        if s94:
            errors.append(f"row {row_no}: ₹{s94:,} received u/s 94(7)/"
                          "94(8) — dividend/bonus-stripping loss disallowal "
                          "is NOT auto-applied; adjust the cost/loss "
                          "manually before filing")

        rec = {
            "asset_class": asset_class,
            "description": (_cell_str(values.get("name"))
                            or _cell_str(values.get("category"))),
            "acquisition_date": _to_date(values.get("purchase_date"),
                                         row=row_no, label="date of "
                                         "purchase", errors=errors),
            "transfer_date": _to_date(values.get("sale_date"), row=row_no,
                                      label="date of sale", errors=errors),
            "full_value_consideration": sale,
            "stamp_duty_value": None,
            "cost_of_acquisition": _to_int(values.get("purchase_cost"),
                                           row=row_no, label="purchase "
                                           "cost", errors=errors),
            "cost_of_improvement": 0,
            "transfer_expenses": _to_int(values.get("transfer_expenses"),
                                         row=row_no, label="transfer "
                                         "expenses", errors=errors),
            "fmv_31jan2018": fmv18 or None,
            "exemption_claimed": 0,
            "exemption_section": "",
        }
        if rec["transfer_date"] is None:
            errors.append(f"row {row_no}: no date of sale — row skipped")
            continue

        # Declared Long-Term/Short-Term vs the engine's date-driven test.
        declared = _cell_str(values.get("term")).casefold()
        rec["term_override"] = ""
        if declared.startswith(("long", "short")):
            declared_lt = declared.startswith("long")
            if rec["acquisition_date"] is None:
                # No date on file — the declared term drives classification
                # (engine honours term_override for undated rows).
                rec["term_override"] = "long" if declared_lt else "short"
                if not declared_lt:
                    errors.append(
                        f"row {row_no}: Short-Term taken from the sheet's "
                        "own column (no date of purchase) — add the "
                        "purchase date before filing")
            elif holding is not None:
                entry = CGEntry(
                    asset_class=asset_class,
                    transfer_date=date.fromisoformat(rec["transfer_date"]),
                    acquisition_date=date.fromisoformat(
                        rec["acquisition_date"]))
                computed_lt = _is_long_term(entry, holding[asset_class])
                if computed_lt != declared_lt:
                    errors.append(
                        f"row {row_no}: sheet says "
                        f"{'Long' if declared_lt else 'Short'}-Term but the "
                        f"dates imply {'long' if computed_lt else 'short'}-"
                        f"term for {ASSET_LABELS[asset_class]} — the engine "
                        "follows the dates")
        out_rows.append(rec)
    return {"rows": out_rows, "errors": errors}


def computation_rows_from_ais(doc) -> dict:
    """AIS sale-of-securities detail → ready-to-compute capital-gains rows
    (the exact shape the Computation page stores), for auto-populating the
    CG table. Uses everything the AIS reports — sale leg, depository cost,
    the reporter's own Long/Short-Term as ``term_override`` (the AIS
    carries no purchase date), FMV, STT — and returns
    ``{"rows": [...], "notes": [...]}`` with the caveats staff must
    resolve (purchase dates, cost verification)."""
    notes: list[str] = []
    rows: list[dict] = []
    short_undated = 0
    for i, p in enumerate(prefill_from_ais(doc), start=1):
        errors: list[str] = []
        transfer = _to_date(p.get("sale_date"), row=i, label="date of sale",
                            errors=errors)
        if transfer is None:
            continue
        stt_yes = p.get("stt") == "Yes"
        listed = p.get("listed") != "Unlisted"
        asset_class = _genius_asset_class(p.get("category"), listed,
                                          stt_yes, row=i, errors=[])
        term = ("long" if p.get("term") == "Long-Term"
                else "short" if p.get("term") == "Short-Term" else "")
        if term == "short":
            short_undated += 1
        units = p.get("units") or 0
        fmv = p.get("fmv_2018") or (round(units * p["fmv_unit"])
                                    if units and p.get("fmv_unit") else 0)
        rows.append({
            "asset_class": asset_class,
            "description": p.get("name") or p.get("category") or "AIS sale",
            "acquisition_date": None,
            "transfer_date": transfer,
            "full_value_consideration": int(round(p.get("sale_amount")
                                                  or 0)),
            "stamp_duty_value": None,
            "cost_of_acquisition": int(round(p.get("purchase_cost") or 0)),
            "cost_of_improvement": 0,
            "transfer_expenses": 0,
            "fmv_31jan2018": int(round(fmv)) or None,
            "exemption_claimed": 0,
            "exemption_section": "",
            "term_override": term,
        })
    if rows:
        notes.append(
            f"{len(rows)} capital-gains row(s) created from the AIS "
            "sale-of-securities detail — sale legs, depository-reported "
            "costs and the AIS's own Long/Short-Term. The AIS carries NO "
            "purchase dates: add them (needed for 112A grandfathering and "
            "verification) and check costs/transfer expenses against the "
            "broker statement, or complete the pre-filled worksheet and "
            "re-import.")
    if short_undated:
        notes.append(
            f"{short_undated} short-term row(s) rely on the AIS Asset Type "
            "for classification until their purchase dates are entered.")
    return {"rows": rows, "notes": notes}


# ---------------------------------------------------- legacy header mapping --
def _map_headers(cells: list) -> dict[int, str] | None:
    """Header row → {column index: field}. None if this isn't the header."""
    lowered = [_cell_str(c).casefold() for c in cells]
    mapping: dict[int, str] = {}
    for idx, text in enumerate(lowered):
        if not text:
            continue
        for field, keys in _HEADER_KEYS.items():
            if field in mapping.values():
                continue
            if any(k in text for k in keys):
                mapping[idx] = field
                break
    essential = ("asset_class", "transfer_date", "full_value_consideration")
    return mapping if all(f in mapping.values() for f in essential) else None


def _parse_legacy(mapping: dict[int, str], rows: list[list],
                  header_at: int) -> dict:
    out_rows: list[dict] = []
    errors: list[str] = []
    for row_no, cells in enumerate(rows[header_at + 1:],
                                   start=header_at + 2):
        values = {field: (cells[idx] if idx < len(cells) else None)
                  for idx, field in mapping.items()}
        if all(v in (None, "") for v in values.values()):
            continue
        rec: dict = {
            "asset_class": _asset_code(values.get("asset_class"),
                                       row=row_no, errors=errors),
            "description": _cell_str(values.get("description")),
            "acquisition_date": _to_date(values.get("acquisition_date"),
                                         row=row_no, label="acquisition "
                                         "date", errors=errors),
            "transfer_date": _to_date(values.get("transfer_date"),
                                      row=row_no, label="transfer date",
                                      errors=errors),
            "exemption_section": _cell_str(
                values.get("exemption_section")).upper().replace("SEC ", ""),
        }
        for field in _INT_FIELDS:
            rec[field] = _to_int(values.get(field), row=row_no,
                                 label=field.replace("_", " "),
                                 errors=errors)
        for field in _OPTIONAL_INT_FIELDS:
            amount = _to_int(values.get(field), row=row_no,
                             label=field.replace("_", " "), errors=errors)
            rec[field] = amount or None
        if rec["transfer_date"] is None:
            errors.append(f"row {row_no}: no transfer (sale) date — row "
                          "skipped")
            continue
        if rec["exemption_section"] not in ("", "54", "54EC", "54F"):
            errors.append(f"row {row_no}: exemption section "
                          f"'{rec['exemption_section']}' is not 54/54EC/54F "
                          "— cleared")
            rec["exemption_section"] = ""
        out_rows.append(rec)
    return {"rows": out_rows, "errors": errors}


def parse_cg_excel(data: bytes, ay: str | None = None) -> dict:
    """Filled template → {"rows": [computation-shaped dicts],
    "errors": [readable problems]}. Accepts the Genius Share Detail layout
    (.xlsx from ``build_template()`` or the original .xls) and the older
    standardized template. Never raises on bad cells — every problem is
    reported with its row number instead. Passing ``ay`` additionally
    cross-checks the sheet's declared Long/Short-Term against the dates."""
    sheet_rows = _ordered(_load_sheets(data))

    # Genius layout first — its "Sale Amount" header would mislead the
    # legacy keyword map, never the other way round.
    for rows in sheet_rows:
        for at, cells in enumerate(rows[:10]):
            mapping = _map_genius(cells)
            if mapping:
                return _parse_genius(mapping, rows, at, ay)
    for rows in sheet_rows:
        for at, cells in enumerate(rows):
            mapping = _map_headers(cells)
            if mapping:
                return _parse_legacy(mapping, rows, at)
    raise ValueError(
        "no header row recognised — keep the template's column headings "
        "(Listed/Unlisted, Category, Sale Amount, Date of Sale, Purchase "
        "Cost, ...)")


def classify_rows(rows: list[dict], ay: str) -> list[dict]:
    """ST/LT + rate-bucket preview per row, using the engine's own
    Sec 2(42A) holding-period test and the AY config's periods."""
    holding = load_cfg(ay).raw["capital_gains"]["holding_months"]
    out = []
    for r in rows:
        entry = CGEntry(
            asset_class=r["asset_class"],
            transfer_date=date.fromisoformat(r["transfer_date"]),
            acquisition_date=(date.fromisoformat(r["acquisition_date"])
                              if r.get("acquisition_date") else None),
            term_override=r.get("term_override", ""))
        long_term = _is_long_term(entry, holding[entry.asset_class])
        if long_term:
            bucket = ("LTCG — Sec 112A (listed equity/equity MF)"
                      if entry.asset_class in ("listed_equity_stt",
                                               "equity_mf")
                      else "LTCG — Sec 112")
        else:
            bucket = ("STCG — Sec 111A (listed equity/equity MF)"
                      if entry.asset_class in ("listed_equity_stt",
                                               "equity_mf")
                      else "STCG — at slab rates")
        out.append({**r, "term": "Long-term" if long_term else "Short-term",
                    "bucket": bucket})
    return out
